import { useEffect, useMemo, useRef, useState } from 'react';
import { Check, ChevronDown, ChevronRight, Search, X } from 'lucide-react';
import type { FilterCondition, FilterSpec } from '../api/analysis';
import type { ColumnDetail, DataTypeGroup } from '../api/models';

interface Props {
  groups: DataTypeGroup[];
  value: FilterSpec | null;
  onChange: (spec: FilterSpec | null) => void;
  loading?: boolean;
  error?: string | null;
}

interface ColumnOption {
  key: string;
  label: string;
  unit: string;
  originalName: string;
}

interface NumericGroup {
  key: string;
  label: string;
  columns: ColumnOption[];
}

const OPS: { value: FilterCondition['op']; label: string; title: string }[] = [
  { value: 'gt', label: '>', title: '大于' },
  { value: 'gte', label: '≥', title: '大于等于' },
  { value: 'lt', label: '<', title: '小于' },
  { value: 'lte', label: '≤', title: '小于等于' },
  { value: 'eq', label: '=', title: '等于' },
  { value: 'between', label: '~', title: '范围' },
];

function isNumericColumn(column: ColumnDetail): boolean {
  return column.is_numeric;
}

function emptyCondition(): FilterCondition {
  return { column: '', op: 'gt', value: null, min_val: null, max_val: null };
}

function isConditionValid(condition: FilterCondition, columns: Map<string, ColumnOption>): boolean {
  if (!condition.column || !columns.has(condition.column)) return false;
  if (condition.op === 'between') {
    return condition.min_val != null && condition.max_val != null && condition.min_val <= condition.max_val;
  }
  return condition.value != null && Number.isFinite(condition.value);
}

function DataColumnTreeSelect({
  groups,
  value,
  onSelect,
}: {
  groups: NumericGroup[];
  value: string;
  onSelect: (key: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState('');
  const [expandedGroups, setExpandedGroups] = useState<Set<string>>(new Set());
  const rootRef = useRef<HTMLDivElement>(null);

  const selected = groups.flatMap((group) => group.columns).find((column) => column.key === value);
  const filteredGroups = useMemo(() => {
    const needle = query.trim().toLowerCase();
    if (!needle) return groups;
    return groups.map((group) => ({
      ...group,
      columns: group.columns.filter((column) =>
        `${group.label} ${column.label} ${column.originalName} ${column.unit}`.toLowerCase().includes(needle)),
    })).filter((group) => group.columns.length > 0);
  }, [groups, query]);

  useEffect(() => {
    const closeOnOutsideClick = (event: MouseEvent) => {
      if (!rootRef.current?.contains(event.target as Node)) setOpen(false);
    };
    document.addEventListener('mousedown', closeOnOutsideClick);
    return () => document.removeEventListener('mousedown', closeOnOutsideClick);
  }, []);

  const toggleGroup = (key: string) => {
    setExpandedGroups((previous) => {
      const next = new Set(previous);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  };

  return (
    <div ref={rootRef} className="relative w-56 shrink-0">
      <button
        type="button"
        onClick={() => setOpen((current) => !current)}
        aria-expanded={open}
        className="w-full h-8 flex items-center justify-between gap-2 bg-white border border-gray-300 rounded px-2 text-left text-xs text-gray-700 hover:border-gray-400 focus:outline-none focus:border-blue-500"
      >
        <span className={`truncate ${selected ? '' : 'text-gray-400'}`}>
          {selected ? `${selected.label}${selected.unit ? ` (${selected.unit})` : ''}` : '选择数据项...'}
        </span>
        <ChevronDown className="w-3.5 h-3.5 text-gray-400 shrink-0" />
      </button>

      {open && (
        <div className="absolute left-0 top-full z-40 mt-1 w-80 border border-gray-200 rounded bg-white shadow-lg overflow-hidden">
          <div className="relative p-2 border-b border-gray-100">
            <Search className="absolute left-4 top-4 w-3.5 h-3.5 text-gray-400" />
            <input
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="搜索显示名或原始列名"
              autoFocus
              className="w-full h-8 pl-8 pr-7 border border-gray-300 rounded text-xs text-gray-700 placeholder-gray-400 focus:outline-none focus:border-blue-500"
            />
            {query && (
              <button
                type="button"
                onClick={() => setQuery('')}
                aria-label="清除搜索"
                className="absolute right-4 top-4 text-gray-400 hover:text-gray-600"
              >
                <X className="w-3.5 h-3.5" />
              </button>
            )}
          </div>
          <div className="max-h-72 overflow-y-auto py-1">
            {filteredGroups.length === 0 ? (
              <div className="px-3 py-6 text-center text-xs text-gray-400">没有匹配的数据项</div>
            ) : filteredGroups.map((group) => {
              const expanded = query.trim() !== '' || expandedGroups.has(group.key);
              return (
                <div key={group.key}>
                  <button
                    type="button"
                    onClick={() => toggleGroup(group.key)}
                    className="w-full h-8 flex items-center gap-2 px-3 text-left text-xs font-medium text-gray-600 hover:bg-gray-50"
                  >
                    {expanded
                      ? <ChevronDown className="w-3.5 h-3.5 text-gray-400" />
                      : <ChevronRight className="w-3.5 h-3.5 text-gray-400" />}
                    <span className="truncate flex-1">{group.label}</span>
                    <span className="text-[10px] font-normal text-gray-400">{group.columns.length}</span>
                  </button>
                  {expanded && group.columns.map((column) => (
                    <button
                      type="button"
                      key={column.key}
                      onClick={() => {
                        onSelect(column.key);
                        setOpen(false);
                        setQuery('');
                      }}
                      className={`w-full min-h-9 flex items-center gap-2 pl-9 pr-3 py-1 text-left hover:bg-blue-50 ${column.key === value ? 'bg-blue-50' : ''}`}
                    >
                      <span className="min-w-0 flex-1">
                        <span className="block truncate text-xs text-gray-700">{column.label}</span>
                        {column.originalName !== column.label && (
                          <span className="block truncate text-[10px] text-gray-400 font-mono">{column.originalName}</span>
                        )}
                      </span>
                      {column.unit && <span className="text-[10px] text-gray-400 shrink-0">{column.unit}</span>}
                      {column.key === value && <Check className="w-3.5 h-3.5 text-blue-600 shrink-0" />}
                    </button>
                  ))}
                </div>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}

export default function DataItemFilter({ groups, value, onChange, loading = false, error }: Props) {
  const numericGroups = useMemo<NumericGroup[]>(() => groups.map((group) => ({
    key: group.data_type_key,
    label: group.label,
    columns: group.columns.filter(isNumericColumn).map((column) => ({
      key: `${group.data_type_key}.${column.column_name}`,
      label: column.display_label || column.column_name,
      unit: column.unit || '',
      originalName: column.column_name,
    })),
  })).filter((group) => group.columns.length > 0), [groups]);

  const columnMap = useMemo(() => new Map(
    numericGroups.flatMap((group) => group.columns).map((column) => [column.key, column]),
  ), [numericGroups]);
  const availableColumnsKey = useMemo(
    () => numericGroups.flatMap((group) => group.columns.map((column) => column.key)).join('\u0001'),
    [numericGroups],
  );
  const [logic, setLogic] = useState<'and' | 'or'>(value?.logic || 'and');
  const [conditions, setConditions] = useState<FilterCondition[]>(value?.conditions || []);
  const suppressEmitRef = useRef(false);
  const debounceRef = useRef<ReturnType<typeof setTimeout>>(undefined);
  const lastEmittedValueRef = useRef<FilterSpec | null | undefined>(undefined);

  useEffect(() => {
    if (value === lastEmittedValueRef.current) {
      lastEmittedValueRef.current = undefined;
      return;
    }
    suppressEmitRef.current = true;
    const timer = window.setTimeout(() => {
      if (value) {
        setLogic(value.logic);
        setConditions(value.conditions.filter((condition) => columnMap.has(condition.column)));
      } else {
        setLogic('and');
        setConditions([]);
      }
    }, 0);
    return () => window.clearTimeout(timer);
  }, [value, availableColumnsKey, columnMap]);

  useEffect(() => {
    if (suppressEmitRef.current) {
      suppressEmitRef.current = false;
      return;
    }
    if (debounceRef.current) window.clearTimeout(debounceRef.current);
    debounceRef.current = window.setTimeout(() => {
      const valid = conditions.filter((condition) => isConditionValid(condition, columnMap));
      const nextValue = valid.length > 0 ? { logic, conditions: valid } : null;
      lastEmittedValueRef.current = nextValue;
      onChange(nextValue);
    }, 400);
    return () => {
      if (debounceRef.current) window.clearTimeout(debounceRef.current);
    };
  }, [conditions, logic, columnMap, onChange]);

  const updateCondition = (index: number, patch: Partial<FilterCondition>) => {
    setConditions((previous) => previous.map((condition, currentIndex) =>
      currentIndex === index ? { ...condition, ...patch } : condition));
  };

  const clear = () => {
    setConditions([]);
    setLogic('and');
    lastEmittedValueRef.current = null;
    onChange(null);
  };

  return (
    <div className="mt-3 pt-3 border-t border-gray-200 space-y-2">
      <div className="flex items-center gap-2 min-h-6">
        <span className="text-xs font-medium text-gray-600">数据项条件</span>
        <div className="flex text-xs ml-2">
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
        {loading && <span className="text-[10px] text-blue-600">正在筛选...</span>}
        <button
          type="button"
          onClick={clear}
          disabled={conditions.length === 0}
          className="text-xs text-gray-400 hover:text-red-500 disabled:text-gray-300 disabled:hover:text-gray-300"
        >
          清除数据项
        </button>
      </div>

      {conditions.map((condition, index) => (
        <div key={index} className="flex items-center gap-2 text-xs flex-wrap">
          <DataColumnTreeSelect
            groups={numericGroups}
            value={condition.column}
            onSelect={(column) => updateCondition(index, {
              column,
              op: 'gt',
              value: null,
              min_val: null,
              max_val: null,
            })}
          />
          <div className="flex">
            {OPS.map((op) => (
              <button
                type="button"
                key={op.value}
                title={op.title}
                onClick={() => updateCondition(index, { op: op.value, value: null, min_val: null, max_val: null })}
                className={`h-8 px-2 border text-xs first:rounded-l last:rounded-r ${
                  condition.op === op.value
                    ? 'bg-blue-600 text-white border-blue-600'
                    : 'bg-white text-gray-500 border-gray-300 hover:bg-gray-50'
                }`}
              >
                {op.label}
              </button>
            ))}
          </div>
          {condition.op === 'between' ? (
            <div className="flex items-center gap-1">
              <input
                type="number"
                placeholder="最小"
                value={condition.min_val ?? ''}
                onChange={(event) => updateCondition(index, { min_val: event.target.value === '' ? null : Number(event.target.value) })}
                className="w-20 h-8 bg-white border border-gray-300 rounded px-2 text-gray-700 placeholder-gray-400 focus:outline-none focus:border-blue-500"
              />
              <span className="text-gray-400">~</span>
              <input
                type="number"
                placeholder="最大"
                value={condition.max_val ?? ''}
                onChange={(event) => updateCondition(index, { max_val: event.target.value === '' ? null : Number(event.target.value) })}
                className="w-20 h-8 bg-white border border-gray-300 rounded px-2 text-gray-700 placeholder-gray-400 focus:outline-none focus:border-blue-500"
              />
            </div>
          ) : (
            <input
              type="number"
              placeholder="值"
              value={condition.value ?? ''}
              onChange={(event) => updateCondition(index, { value: event.target.value === '' ? null : Number(event.target.value) })}
              className="w-24 h-8 bg-white border border-gray-300 rounded px-2 text-gray-700 placeholder-gray-400 focus:outline-none focus:border-blue-500"
            />
          )}
          <button
            type="button"
            onClick={() => setConditions((previous) => previous.filter((_, currentIndex) => currentIndex !== index))}
            aria-label="删除数据项条件"
            className="w-7 h-7 flex items-center justify-center text-gray-400 hover:text-red-500 rounded hover:bg-red-50"
          >
            <X className="w-3.5 h-3.5" />
          </button>
        </div>
      ))}

      <button
        type="button"
        onClick={() => setConditions((previous) => [...previous, emptyCondition()])}
        disabled={numericGroups.length === 0}
        className="text-xs text-blue-600 hover:text-blue-500 disabled:text-gray-300"
      >
        + 添加条件
      </button>
      {numericGroups.length === 0 && <span className="ml-2 text-[10px] text-gray-400">当前机型没有数值数据项</span>}
      {error && <div className="text-xs text-red-600 bg-red-50 border border-red-100 rounded px-2 py-1.5">{error}</div>}
    </div>
  );
}
