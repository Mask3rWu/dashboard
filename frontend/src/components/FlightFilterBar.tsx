import { useState, useEffect, useRef } from 'react';
import {
  FLIGHT_FILTER_FIELDS,
  type FlightFilterCondition,
  type FlightFilterField,
  type FlightFilterSpec,
} from '../api';

type FlightCondition = FlightFilterCondition;

interface Props {
  value: FlightFilterSpec | null;
  onChange: (spec: FlightFilterSpec | null) => void;
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

export default function FlightFilterBar({ value, onChange }: Props) {
  const [expanded, setExpanded] = useState(false);
  const [logic, setLogic] = useState<'and' | 'or'>(value?.logic || 'and');
  const [conditions, setConditions] = useState<FlightCondition[]>(
    value?.conditions?.length ? value.conditions : []
  );
  const debounceRef = useRef<ReturnType<typeof setTimeout>>(undefined);
  const suppressEmitRef = useRef(false);

  // Sync from parent when value changes externally (e.g. reset on model switch).
  useEffect(() => {
    suppressEmitRef.current = true;
    if (value) {
      const valid = value.conditions.filter((c) => fieldMap.has(c.field));
      setLogic(value.logic);
      setConditions(valid);
    } else {
      setLogic('and');
      setConditions([]);
    }
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
      onChange(valid.length > 0 ? { logic, conditions: valid } : null);
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

  return (
    <div className="mb-4 border border-gray-200 rounded-lg bg-white overflow-hidden">
      {/* Collapsed bar */}
      {!expanded && (
        <button
          onClick={() => setExpanded(true)}
          className="w-full flex items-center gap-2 px-3 py-1.5 text-xs text-gray-500 hover:bg-gray-50 transition-colors"
        >
          <span className="font-mono text-gray-400">▸</span>
          {activeCount > 0 ? (
            <span>
              筛选: <strong className="text-gray-700">{activeCount}</strong> 条件
              (<span className="text-blue-600 font-medium">{logic.toUpperCase()}</span>)
            </span>
          ) : (
            <span>记录筛选</span>
          )}
          {activeCount > 0 && (
            <span className="text-gray-400 ml-1 truncate">
              - {activeConditions.map(condSummary).join(logic === 'and' ? ' AND ' : ' OR ')}
            </span>
          )}
        </button>
      )}

      {/* Expanded panel */}
      {expanded && (
        <div className="px-3 py-2 space-y-2">
          {/* Header */}
          <div className="flex items-center gap-2">
            <button
              onClick={() => setExpanded(false)}
              className="text-gray-400 hover:text-gray-600 text-xs font-mono"
            >
              ▾ 收起
            </button>
            <div className="h-4 w-px bg-gray-200" />

            {/* Logic toggle */}
            <div className="flex text-xs">
              <button
                onClick={() => setLogic('and')}
                className={`px-2 py-0.5 rounded-l border ${logic === 'and' ? 'bg-blue-600 text-white border-blue-600' : 'bg-white text-gray-500 border-gray-300 hover:bg-gray-50'}`}
              >
                AND（且）
              </button>
              <button
                onClick={() => setLogic('or')}
                className={`px-2 py-0.5 rounded-r border-t border-b border-r ${logic === 'or' ? 'bg-blue-600 text-white border-blue-600' : 'bg-white text-gray-500 border-gray-300 hover:bg-gray-50'}`}
              >
                OR（或）
              </button>
            </div>

            <div className="flex-1" />

            <button onClick={clearAll} className="text-xs text-gray-400 hover:text-red-500">清除全部</button>
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
            onClick={addCond}
            className="text-xs text-blue-600 hover:text-blue-500 flex items-center gap-1"
          >
            + 添加条件
          </button>
        </div>
      )}
    </div>
  );
}
