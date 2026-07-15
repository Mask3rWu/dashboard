import { useState, useEffect, useEffectEvent, useMemo, useRef } from 'react';
import type { FilterCondition, FilterSpec, FilterPreset, ColumnGroup } from '../api/analysis';

interface Props {
  /** Only columns currently selected in the chart (grouped by data type) */
  columnGroups: ColumnGroup[];
  filterSpec: FilterSpec | null;
  onChange: (spec: FilterSpec | null) => void;
  filterPresets: FilterPreset[];
  onSavePreset: (name: string) => void;
  onLoadPreset: (preset: FilterPreset) => void;
  onDeletePreset: (id: number) => void;
}

const OPS: { value: FilterCondition['op']; label: string }[] = [
  { value: 'gt', label: '>' },
  { value: 'gte', label: '≥' },
  { value: 'lt', label: '<' },
  { value: 'lte', label: '≤' },
  { value: 'eq', label: '=' },
  { value: 'between', label: '~' },
];

function emptyCond(): FilterCondition {
  return { column: '', op: 'gt', value: null, min_val: null, max_val: null };
}

export default function FilterBar({
  columnGroups, filterSpec, onChange, filterPresets, onSavePreset, onLoadPreset, onDeletePreset,
}: Props) {
  const [expanded, setExpanded] = useState(false);
  const [logic, setLogic] = useState<'and' | 'or'>(filterSpec?.logic || 'and');
  const [conditions, setConditions] = useState<FilterCondition[]>(
    filterSpec?.conditions?.length ? filterSpec.conditions : []
  );
  const presetNameRef = useRef<HTMLInputElement>(null);
  const debounceRef = useRef<ReturnType<typeof setTimeout>>(undefined);
  const suppressEmitRef = useRef(false);
  const emitChange = useEffectEvent(onChange);

  // Build flat lookup: key -> {label, unit}
  const colMap = useMemo(() => {
    const result = new Map<string, { label: string; unit: string }>();
    columnGroups.forEach((g) => g.columns.forEach((c) => result.set(c.key, { label: c.label, unit: c.unit })));
    return result;
  }, [columnGroups]);
  const availableColumnKey = useMemo(() => columnGroups
    .flatMap((g) => g.columns.map((c) => c.key))
    .join('\u0001'), [columnGroups]);
  const validConditions = useEffectEvent((items: FilterCondition[]) => items.filter((condition) => {
    if (!condition.column || !colMap.has(condition.column)) return false;
    if (condition.op === 'between') return condition.min_val != null && condition.max_val != null;
    return condition.value != null;
  }));

  // Sync from parent when filterSpec changes externally (e.g. preset loaded)
  useEffect(() => {
    suppressEmitRef.current = true;
    const timer = window.setTimeout(() => {
      if (filterSpec) {
        const nextConditions = filterSpec.conditions.filter((condition) => colMap.has(condition.column));
        setLogic(filterSpec.logic);
        setConditions(nextConditions);
        if (nextConditions.length !== filterSpec.conditions.length) {
          emitChange(nextConditions.length > 0 ? { logic: filterSpec.logic, conditions: nextConditions } : null);
        }
      } else {
        setLogic('and');
        setConditions([]);
      }
    }, 0);
    return () => window.clearTimeout(timer);
  }, [filterSpec, availableColumnKey, colMap]);

  // Emit valid conditions when state changes (replaces side effects in state updaters)
  useEffect(() => {
    if (suppressEmitRef.current) {
      suppressEmitRef.current = false;
      return;
    }
    if (debounceRef.current) clearTimeout(debounceRef.current);
    debounceRef.current = setTimeout(() => {
      const valid = validConditions(conditions);
      if (valid.length === 0) {
        emitChange(null);
      } else {
        emitChange({ logic, conditions: valid });
      }
    }, 400);
  }, [conditions, logic]);

  // Cleanup debounce timer on unmount
  useEffect(() => {
    return () => {
      if (debounceRef.current) clearTimeout(debounceRef.current);
    };
  }, []);

  const updateCond = (i: number, patch: Partial<FilterCondition>) => {
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

  const handleSavePreset = () => {
    const name = presetNameRef.current?.value?.trim();
    if (!name) return;
    onSavePreset(name);
    if (presetNameRef.current) presetNameRef.current.value = '';
  };

  const visibleFilterConditions = filterSpec?.conditions?.filter((c) => colMap.has(c.column)) || [];
  const activeCount = visibleFilterConditions.length;

  // Only show groups that have selected columns
  const visibleGroups = columnGroups.filter((g) => g.columns.length > 0);

  return (
    <div className="border-b border-gray-200 bg-white shrink-0">
      {/* Collapsed bar */}
      {!expanded && (
        <button
          onClick={() => setExpanded(true)}
          className="w-full flex items-center gap-2 px-4 py-1 text-xs text-gray-500 hover:bg-gray-50 transition-colors"
        >
          <span className="font-mono text-gray-400">▸</span>
          {activeCount > 0 ? (
            <span>
              筛选: <strong className="text-gray-700">{activeCount}</strong> 条件
              (<span className="text-blue-600 font-medium">{filterSpec?.logic?.toUpperCase()}</span>)
            </span>
          ) : (
            <span>筛选</span>
          )}
          {activeCount > 0 && (
            <span className="text-gray-400 ml-1">
              - {visibleFilterConditions.map((c) => {
                const info = colMap.get(c.column);
                const opLabel = OPS.find((o) => o.value === c.op)?.label || c.op;
                const val = c.op === 'between' ? `${c.min_val}~${c.max_val}` : c.value;
                return `${info?.label || c.column} ${opLabel} ${val}`;
              }).join(filterSpec?.logic === 'and' ? ' AND ' : ' OR ')}
            </span>
          )}
        </button>
      )}

      {/* Expanded panel */}
      {expanded && (
        <div className="px-4 py-2 space-y-2">
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
          {conditions.map((c, i) => (
            <div key={i} className="flex items-center gap-2 text-xs">
              {/* Column select with optgroup */}
              <select
                value={c.column}
                onChange={(e) => updateCond(i, { column: e.target.value })}
                className="bg-white border border-gray-300 rounded px-2 py-1 text-gray-700 w-48 focus:outline-none focus:border-blue-500"
              >
                <option value="">选择列...</option>
                {visibleGroups.map((g) => (
                  <optgroup key={g.table} label={g.label}>
                    {g.columns.map((col) => (
                      <option key={col.key} value={col.key}>
                        {col.label} {col.unit ? `(${col.unit})` : ''}
                      </option>
                    ))}
                  </optgroup>
                ))}
              </select>

              {/* Operator */}
              <div className="flex">
                {OPS.map((op) => (
                  <button
                    key={op.value}
                    onClick={() => updateCond(i, { op: op.value, value: null, min_val: null, max_val: null })}
                    title={op.value === 'gte' ? '大于等于' : op.value === 'lte' ? '小于等于' : op.value === 'gt' ? '大于' : op.value === 'lt' ? '小于' : op.value === 'eq' ? '等于' : '范围'}
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

              {/* Value input */}
              {c.op === 'between' ? (
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
              ) : (
                <input
                  type="number"
                  placeholder="值"
                  value={c.value ?? ''}
                  onChange={(e) => updateCond(i, { value: e.target.value ? Number(e.target.value) : null })}
                  className="w-24 bg-white border border-gray-300 rounded px-2 py-1 text-gray-700 placeholder-gray-400 focus:outline-none focus:border-blue-500"
                />
              )}

              <button
                onClick={() => removeCond(i)}
                className="text-gray-400 hover:text-red-500 font-bold text-sm"
              >
                ×
              </button>
            </div>
          ))}

          {/* Add condition */}
          <button
            onClick={addCond}
            className="text-xs text-blue-600 hover:text-blue-500 flex items-center gap-1"
          >
            + 添加条件
          </button>

          {/* Filter presets */}
          <div className="flex items-center gap-2 pt-1 border-t border-gray-100">
            <span className="text-[10px] text-gray-400">筛选预设:</span>
            <div className="flex items-center gap-1">
              {filterPresets.map((p) => (
                <span key={p.id} className="flex items-center gap-0.5">
                  <button
                    onClick={() => onLoadPreset(p)}
                    className="text-xs px-2 py-0.5 bg-white border border-gray-300 hover:bg-blue-50 rounded text-gray-600"
                  >
                    {p.name}
                  </button>
                  <button
                    onClick={() => onDeletePreset(p.id)}
                    className="text-gray-400 hover:text-red-500 text-xs font-bold"
                  >
                    ×
                  </button>
                </span>
              ))}
            </div>
            <input
              ref={presetNameRef}
              placeholder="名称..."
              className="w-20 bg-white border border-gray-300 rounded px-2 py-0.5 text-xs text-gray-700 placeholder-gray-400 focus:outline-none focus:border-blue-500"
              onKeyDown={(e) => e.key === 'Enter' && handleSavePreset()}
            />
            <button
              onClick={handleSavePreset}
              className="text-xs px-2 py-0.5 bg-blue-600 text-white hover:bg-blue-500 rounded"
            >
              保存
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
