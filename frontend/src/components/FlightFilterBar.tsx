import { useState, useEffect, useRef } from 'react';
import { CircleHelp } from 'lucide-react';
import {
  FLIGHT_FILTER_FIELDS,
  type FlightFilterCondition,
  type FlightFilterField,
  type FlightFilterSpec,
} from '../api/flights';
import type { FilterCondition, FilterSpec } from '../api/analysis';
import type { DataTypeGroup } from '../api/models';
import DataItemFilter from './DataItemFilter';

type FlightCondition = FlightFilterCondition;

interface Props {
  value: FlightFilterSpec | null;
  onChange: (spec: FlightFilterSpec | null) => void;
  dataColumnGroups: DataTypeGroup[];
  dataFilter: FilterSpec | null;
  onDataFilterChange: (spec: FilterSpec | null) => void;
  dataFilterLoading?: boolean;
  dataFilterError?: string | null;
}

const NUMERIC_OPS: { value: FlightFilterCondition['op']; label: string; title: string }[] = [
  { value: 'gt', label: '>', title: '大于' },
  { value: 'gte', label: '≥', title: '大于等于' },
  { value: 'lt', label: '<', title: '小于' },
  { value: 'lte', label: '≤', title: '小于等于' },
  { value: 'eq', label: '=', title: '等于' },
  { value: 'between', label: '~', title: '范围' },
];

// Build lookups once. Fields are static, so module scope is fine.
const fieldMap = new Map<string, FlightFilterField>();
FLIGHT_FILTER_FIELDS.forEach((f) => fieldMap.set(f.key, f));

const TEXT_FIELDS = FLIGHT_FILTER_FIELDS.filter((f) => f.type === 'text');
const NUMERIC_FIELDS = FLIGHT_FILTER_FIELDS.filter((f) => f.type === 'number');

function defaultOpFor(fieldKey: string): FlightFilterCondition['op'] {
  return fieldMap.get(fieldKey)?.type === 'text' ? 'contains' : 'gt';
}

function emptyCond(): FlightFilterCondition {
  return { field: '', op: 'gt', value: null, min_val: null, max_val: null };
}

// A condition is "valid" (counts toward filtering) when it has a field and a
// usable value. Incomplete rows are kept in UI state but not emitted.
function isCondValid(c: FlightFilterCondition): boolean {
  if (!c.field || !fieldMap.has(c.field)) return false;
  const field = fieldMap.get(c.field)!;
  if (field.type === 'text') return (c.value ?? '').trim() !== '';
  if (c.op === 'between') return c.min_val != null && c.max_val != null;
  return c.value != null && c.value.trim() !== '' && Number.isFinite(Number(c.value));
}

function condSummary(c: FlightFilterCondition): string {
  const field = fieldMap.get(c.field);
  if (!field) return '';
  const label = field.label;
  if (field.type === 'text') return `${label} 包含 ${c.value}`;
  const opLabel = NUMERIC_OPS.find((o) => o.value === c.op)?.label ?? c.op;
  const val = c.op === 'between' ? `${c.min_val}~${c.max_val}` : c.value;
  return `${label} ${opLabel} ${val}`;
}

function dataCondSummary(condition: FilterCondition, groups: DataTypeGroup[]): string {
  const separator = condition.column.indexOf('.');
  const dataTypeKey = separator >= 0 ? condition.column.slice(0, separator) : '';
  const columnName = separator >= 0 ? condition.column.slice(separator + 1) : condition.column;
  const group = groups.find((item) => item.data_type_key === dataTypeKey);
  const column = group?.columns.find((item) => item.column_name === columnName);
  const label = column?.display_label || columnName;
  const qualifiedLabel = group ? `${group.label}/${label}` : label;
  const opLabel = NUMERIC_OPS.find((item) => item.value === condition.op)?.label ?? condition.op;
  const value = condition.op === 'between'
    ? `${condition.min_val}~${condition.max_val}`
    : condition.value;
  return `${qualifiedLabel} ${opLabel} ${value}`;
}

export function FilterRulesHelp() {
  return (
    <div className="relative group shrink-0">
      <button
        type="button"
        aria-label="查看筛选规则"
        className="w-7 h-7 flex items-center justify-center text-gray-400 hover:text-blue-600 rounded hover:bg-blue-50 focus:outline-none focus:ring-2 focus:ring-blue-200"
      >
        <CircleHelp className="w-4 h-4" />
      </button>
      <div
        role="tooltip"
        className="hidden group-hover:block group-focus-within:block absolute right-0 top-full z-50 mt-1 w-80 rounded border border-gray-200 bg-white p-3 text-xs leading-5 text-gray-600 shadow-lg"
      >
        <div className="font-medium text-gray-800 mb-1">架次筛选规则</div>
        <div>时间范围、飞行记录单和数据项筛选之间取交集。</div>
        <div>数据项 AND：必须在同一时间点同时满足全部条件。</div>
        <div>数据项 OR：任一时间点满足任一条件即可。</div>
        <div>至少一个约 1 秒的对齐采样点满足即命中；缺失数据不满足条件。</div>
      </div>
    </div>
  );
}

export default function FlightFilterBar({
  value,
  onChange,
  dataColumnGroups,
  dataFilter,
  onDataFilterChange,
  dataFilterLoading = false,
  dataFilterError,
}: Props) {
  const [expanded, setExpanded] = useState(false);
  const [logic, setLogic] = useState<'and' | 'or'>(value?.logic || 'and');
  const [conditions, setConditions] = useState<FlightCondition[]>(
    value?.conditions?.length ? value.conditions : []
  );
  const debounceRef = useRef<ReturnType<typeof setTimeout>>(undefined);
  const suppressEmitRef = useRef(false);
  const lastEmittedValueRef = useRef<FlightFilterSpec | null | undefined>(undefined);

  // Sync from parent when value changes externally (e.g. reset on model switch).
  useEffect(() => {
    if (value === lastEmittedValueRef.current) {
      lastEmittedValueRef.current = undefined;
      return;
    }
    suppressEmitRef.current = true;
    const timer = window.setTimeout(() => {
      if (value) {
        const valid = value.conditions.filter((c) => fieldMap.has(c.field));
        setLogic(value.logic);
        setConditions(valid);
      } else {
        setLogic('and');
        setConditions([]);
      }
    }, 0);
    return () => window.clearTimeout(timer);
  }, [value]);

  // Emit valid conditions (debounced) whenever local state changes.
  // `onChange` (setFlightFilter) is a stable useState setter, so listing it
  // here does not reset the debounce timer on parent re-renders.
  useEffect(() => {
    if (suppressEmitRef.current) {
      suppressEmitRef.current = false;
      return;
    }
    debounceRef.current = setTimeout(() => {
      const valid = conditions.filter(isCondValid);
      const nextValue = valid.length > 0 ? { logic, conditions: valid } : null;
      lastEmittedValueRef.current = nextValue;
      onChange(nextValue);
    }, 400);
    return () => { if (debounceRef.current) clearTimeout(debounceRef.current); };
  }, [conditions, logic, onChange]);

  const updateCond = (i: number, patch: Partial<FlightCondition>) => {
    setConditions((prev) => prev.map((c, j) => (j === i ? { ...c, ...patch } : c)));
  };

  const removeCond = (i: number) => {
    setConditions((prev) => prev.filter((_, j) => j !== i));
  };

  const addCond = () => {
    setConditions((prev) => [...prev, emptyCond()]);
    setExpanded(true);
  };

  const clearAll = () => {
    setConditions([]);
    setLogic('and');
    lastEmittedValueRef.current = null;
    onChange(null);
  };

  const selectField = (i: number, fieldKey: string) => {
    updateCond(i, {
      field: fieldKey,
      op: defaultOpFor(fieldKey),
      value: null,
      min_val: null,
      max_val: null,
    });
  };

  const activeConditions = conditions.filter(isCondValid);
  const activeCount = activeConditions.length;
  const dataActiveCount = dataFilter?.conditions.length ?? 0;
  const totalActiveCount = activeCount + dataActiveCount;
  const recordExpression = activeConditions.map(condSummary).join(logic === 'and' ? ' AND ' : ' OR ');
  const dataExpression = dataFilter?.conditions
    .map((condition) => dataCondSummary(condition, dataColumnGroups))
    .join(dataFilter.logic === 'and' ? ' AND ' : ' OR ') ?? '';

  return (
    <div className="mb-4 border border-gray-200 rounded-lg bg-white overflow-visible">
      {/* Collapsed bar */}
      {!expanded && (
        <div className="flex items-center rounded-lg overflow-visible">
          <button
            type="button"
            onClick={() => setExpanded(true)}
            className="min-w-0 flex-1 flex items-center gap-2 px-3 py-1.5 text-xs text-gray-500 hover:bg-gray-50 transition-colors rounded-lg"
          >
            <span className="font-mono text-gray-400">▸</span>
            <span className="shrink-0">
              架次筛选: <strong className="text-gray-700">{totalActiveCount}</strong> 条件
            </span>
            <span className="shrink-0 text-gray-400">
              记录单 {activeCount} 条 · 数据项 {dataActiveCount} 条
            </span>
            {totalActiveCount > 0 && (
              <span className="text-gray-400 ml-1 truncate">
                {recordExpression && `记录单: ${recordExpression}`}
                {recordExpression && dataExpression && ' · '}
                {dataExpression && `数据项: ${dataExpression}`}
              </span>
            )}
          </button>
        </div>
      )}

      {/* Expanded panel */}
      {expanded && (
        <div className="px-3 py-2 space-y-2">
          {/* Filter overview */}
          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={() => setExpanded(false)}
              className="text-gray-400 hover:text-gray-600 text-xs font-mono"
            >
              ▾ 收起
            </button>
            <div className="h-4 w-px bg-gray-200" />
            <span className="text-xs text-gray-500">
              <strong className="text-gray-700">{totalActiveCount}</strong> 条件
            </span>
            <span className="text-xs text-gray-400">
              记录单 {activeCount} 条 · 数据项 {dataActiveCount} 条
            </span>
          </div>

          {/* Flight record controls */}
          <div className="flex items-center gap-2 min-h-6">
            <span className="text-xs font-medium text-gray-600">飞行记录单条件</span>
            {/* Logic toggle */}
            <div className="flex text-xs">
              <button
                type="button"
                onClick={() => setLogic('and')}
                className={`px-2 py-0.5 rounded-l border ${logic === 'and' ? 'bg-blue-600 text-white border-blue-600' : 'bg-white text-gray-500 border-gray-300 hover:bg-gray-50'}`}
              >
                AND（且）
              </button>
              <button
                type="button"
                onClick={() => setLogic('or')}
                className={`px-2 py-0.5 rounded-r border-t border-b border-r ${logic === 'or' ? 'bg-blue-600 text-white border-blue-600' : 'bg-white text-gray-500 border-gray-300 hover:bg-gray-50'}`}
              >
                OR（或）
              </button>
            </div>

            <div className="flex-1" />

            <button
              type="button"
              onClick={clearAll}
              disabled={conditions.length === 0}
              className="text-xs text-gray-400 hover:text-red-500 disabled:text-gray-300 disabled:hover:text-gray-300"
            >
              清除记录单
            </button>
          </div>

          {/* Conditions */}
          {conditions.map((c, i) => {
            const field = fieldMap.get(c.field);
            const isText = field?.type === 'text';
            const isNumeric = field?.type === 'number';
            return (
              <div key={i} className="flex items-center gap-2 text-xs flex-wrap">
                {/* Field select */}
                <select
                  value={c.field}
                  onChange={(e) => selectField(i, e.target.value)}
                  className="bg-white border border-gray-300 rounded px-2 py-1 text-gray-700 w-36 focus:outline-none focus:border-blue-500"
                >
                  <option value="">选择字段...</option>
                  <optgroup label="文本">
                    {TEXT_FIELDS.map((f) => (
                      <option key={f.key} value={f.key}>{f.label}</option>
                    ))}
                  </optgroup>
                  <optgroup label="数值">
                    {NUMERIC_FIELDS.map((f) => (
                      <option key={f.key} value={f.key}>
                        {f.label}{f.unit ? ` (${f.unit})` : ''}
                      </option>
                    ))}
                  </optgroup>
                </select>

                {/* Operator: text shows a fixed "包含" label; numeric shows the button group */}
                {isText ? (
                  <span className="px-2 py-1 text-gray-500 border border-gray-200 rounded bg-gray-50">包含</span>
                ) : isNumeric ? (
                  <div className="flex">
                    {NUMERIC_OPS.map((op) => (
                      <button
                        type="button"
                        key={op.value}
                        title={op.title}
                        onClick={() => updateCond(i, { op: op.value, value: null, min_val: null, max_val: null })}
                        className={`px-2 py-1 border text-xs first:rounded-l last:rounded-r ${
                          c.op === op.value
                            ? 'bg-blue-600 text-white border-blue-600'
                            : 'bg-white text-gray-500 border-gray-300 hover:bg-gray-50'
                        }`}
                      >
                        {op.label}
                      </button>
                    ))}
                  </div>
                ) : (
                  <span className="px-2 py-1 text-gray-300 border border-dashed border-gray-200 rounded">操作符</span>
                )}

                {/* Value input */}
                {isText ? (
                  <input
                    type="text"
                    placeholder="值"
                    value={c.value ?? ''}
                    onChange={(e) => updateCond(i, { value: e.target.value })}
                    className="w-32 bg-white border border-gray-300 rounded px-2 py-1 text-gray-700 placeholder-gray-400 focus:outline-none focus:border-blue-500"
                  />
                ) : isNumeric && c.op === 'between' ? (
                  <div className="flex items-center gap-1">
                    <input
                      type="number"
                      placeholder="最小"
                      value={c.min_val ?? ''}
                      onChange={(e) => updateCond(i, { min_val: e.target.value ? Number(e.target.value) : null })}
                      className="w-20 bg-white border border-gray-300 rounded px-2 py-1 text-gray-700 placeholder-gray-400 focus:outline-none focus:border-blue-500"
                    />
                    <span className="text-gray-400">~</span>
                    <input
                      type="number"
                      placeholder="最大"
                      value={c.max_val ?? ''}
                      onChange={(e) => updateCond(i, { max_val: e.target.value ? Number(e.target.value) : null })}
                      className="w-20 bg-white border border-gray-300 rounded px-2 py-1 text-gray-700 placeholder-gray-400 focus:outline-none focus:border-blue-500"
                    />
                  </div>
                ) : isNumeric ? (
                  <input
                    type="number"
                    placeholder="值"
                    value={c.value ?? ''}
                    onChange={(e) => updateCond(i, { value: e.target.value })}
                    className="w-24 bg-white border border-gray-300 rounded px-2 py-1 text-gray-700 placeholder-gray-400 focus:outline-none focus:border-blue-500"
                  />
                ) : null}

                <button
                  type="button"
                  onClick={() => removeCond(i)}
                  className="text-gray-400 hover:text-red-500 font-bold text-sm"
                >
                  ×
                </button>
              </div>
            );
          })}

          {/* Add condition */}
          <button
            type="button"
            onClick={addCond}
            className="text-xs text-blue-600 hover:text-blue-500 flex items-center gap-1"
          >
            + 添加条件
          </button>

          <DataItemFilter
            groups={dataColumnGroups}
            value={dataFilter}
            onChange={onDataFilterChange}
            loading={dataFilterLoading}
            error={dataFilterError}
          />
        </div>
      )}
    </div>
  );
}
