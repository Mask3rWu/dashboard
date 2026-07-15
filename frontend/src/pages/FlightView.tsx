import { useState, useEffect, useRef } from 'react';
import { getAlignedData, getStats, getCorrelation, getAnomaly, listPresets, createPreset, deletePreset, listFilterPresets, createFilterPreset, deleteFilterPreset, type ColumnGroup, type AlignedData, type FlightStats, type Preset, type FilterSpec, type FilterPreset, type AnomalyData, type CorrelationData } from '../api/analysis';
import { getFlight, updateFlight, deleteFlight, type Flight } from '../api/flights';
import { updateModelColumn, type AircraftModel, type Aircraft, type DeleteScope } from '../api/models';
import FilterBar from '../components/FilterBar';
import { deleteActionLabel, deleteScopeFor } from '../syncStatus';
import { AnomalyChart, CorrelationHeatmap } from '../features/analysis/AnalysisCharts';
import FlightChart, { type FlightChartHandle } from '../features/analysis/FlightChart';
import FlightTree from '../features/analysis/FlightTree';

interface Props {
  active: boolean;
  flights: Flight[];
  selectedFlightId: number | null;
  onSelectFlight: (id: number | null) => void;
  onFlightsChanged: () => void;
  // Three-level selection
  models: AircraftModel[];
  selectedModelId: number | null;
  onSelectModel: (id: number) => void;
  aircraft: Aircraft[];
  selectedAircraftId: number | null;
  onSelectAircraft: (id: number) => void;
  canDeleteFlights: boolean;
  canEditColumns: boolean;
  serverOnline?: boolean;
}

type ViewMode = 'chart' | 'correlation' | 'anomaly';

// ═══════════════════════════════════════════════════════════════
// Chart option builder — pure function, hoisted out of the
// component so it allocates fresh each call and isn't recreated
// on every render. Computes a complete ECharts option from current
// aligned data + normalization + per-column scale factors.
// ═══════════════════════════════════════════════════════════════
export default function FlightView({
  active,
  flights, selectedFlightId, onSelectFlight, onFlightsChanged,
  models, selectedModelId, onSelectModel,
  aircraft, selectedAircraftId, onSelectAircraft,
  canDeleteFlights,
  canEditColumns,
  serverOnline = true,
}: Props) {
  // ─── State ─────────────────────────────────────────────
  const [columnGroups, setColumnGroups] = useState<ColumnGroup[]>([]);
  const [selectedColumns, setSelectedColumns] = useState<string[]>([]);
  const [aligned, setAligned] = useState<AlignedData | null>(null);
  const [stats, setStats] = useState<FlightStats | null>(null);
  const [presets, setPresets] = useState<Preset[]>([]);
  const [normalize, setNormalize] = useState(false);
  const [loading, setLoading] = useState(false);
  const [alignedLoading, setAlignedLoading] = useState(false);
  const [viewMode, setViewMode] = useState<ViewMode>('chart');
  const [anomalyCol, setAnomalyCol] = useState('');
  const [anomalyData, setAnomalyData] = useState<AnomalyData | null>(null);
  const [corrData, setCorrData] = useState<CorrelationData | null>(null);
  const [sidebarOpen, setSidebarOpen] = useState(true);
  const [collapsedGroups, setCollapsedGroups] = useState<Set<string>>(new Set());
  const [filterSpec, setFilterSpec] = useState<FilterSpec | null>(null);
  const [filterPresets, setFilterPresets] = useState<FilterPreset[]>([]);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);

  // Per-column scale factor: key -> multiplier (default 1.0)
  const [scaleFactors, setScaleFactors] = useState<Record<string, number>>({});
  const [hoveredCol, setHoveredCol] = useState<string | null>(null);
  const [editingCol, setEditingCol] = useState<string | null>(null);
  const [draftScale, setDraftScale] = useState<number>(1.0);
  const editInputRef = useRef<HTMLInputElement>(null);
  const skipBlurRef = useRef(false);
  const persistTimers = useRef<Record<string, ReturnType<typeof setTimeout>>>({});

  // ─── Flight management state ───────────────────────────
  const [flightSearch, setFlightSearch] = useState('');
  const [editingFlightId, setEditingFlightId] = useState<number | null>(null);
  const [editName, setEditName] = useState('');
  const [deletingFlightId, setDeletingFlightId] = useState<number | null>(null);


  const flightChartRef = useRef<FlightChartHandle>(null);
  const presetNameRef = useRef<HTMLInputElement>(null);

  // Derive current model_id from the selected flight
  const currentModelId = flights.find(f => f.id === selectedFlightId)?.model_id ?? null;

  // Track latest flight ID to abort stale async operations
  const latestFlightRef = useRef<number | null>(null);
  const alignedRequestRef = useRef(0);

  useEffect(() => {
    const timer = window.setTimeout(() => {
      setSelectedColumns([]);
      setFilterSpec(null);
      setColumnGroups([]);
      setAligned(null);
      setPresets([]);
      setFilterPresets([]);
      setCorrData(null);
      setAnomalyData(null);
      setAnomalyCol('');
    }, 0);
    return () => window.clearTimeout(timer);
  }, [currentModelId]);

  // ─── Load flight data ──────────────────────────────────
  useEffect(() => {
    // Always update the ref so stale in-flight requests are aborted
    // when selectedFlightId becomes null (otherwise they'd write
    // data for a flight that is no longer selected).
    latestFlightRef.current = selectedFlightId;
    let cancelled = false;
    if (!selectedFlightId) {
      const timer = window.setTimeout(() => setAligned(null), 0);
      return () => {
        cancelled = true;
        window.clearTimeout(timer);
      };
    }
    const flightId = selectedFlightId;
    const modelId = currentModelId;
    const timer = window.setTimeout(() => {
      if (cancelled || latestFlightRef.current !== flightId) return;
      setLoading(true);
      setAligned(null);  // clear chart immediately when flight changes
      Promise.all([
        getFlight(flightId),
        getStats(flightId),
        modelId != null ? listPresets(modelId) : Promise.resolve({ presets: [] }),
        modelId != null ? listFilterPresets(modelId) : Promise.resolve({ presets: [] }),
      ]).then(([flightData, statsData, presetData, fpData]) => {
        if (cancelled || latestFlightRef.current !== flightId) return;
        setColumnGroups(flightData.columns);
        setCollapsedGroups(new Set(flightData.columns.map((g: ColumnGroup) => g.table)));
        const sf: Record<string, number> = {};
        flightData.columns.forEach((g: ColumnGroup) => {
          g.columns.forEach((c) => {
            sf[c.key] = c.scale_factor ?? 1.0;
          });
        });
        setScaleFactors(sf);
        setStats(statsData);
        setPresets(presetData.presets);
        setFilterPresets(fpData.presets);

        setSelectedColumns((prev) => {
          const newKeys = new Set(flightData.columns.flatMap((g) => g.columns.map((c) => c.key)));
          const kept = prev.filter((key) => newKeys.has(key));
          if (kept.length > 0) return kept;
          const defaults = ['pos.lat', 'pos.lng', 'gps.nava_alt', 'engine.engine_rpm', 'drone_state.battery_pct'];
          return defaults.filter((key) => newKeys.has(key));
        });
        setCorrData(null);
        setAnomalyData(null);
      }).catch((error) => {
        console.error('Failed to load flight data:', error);
      }).finally(() => {
        if (!cancelled && latestFlightRef.current === flightId) setLoading(false);
      });
    }, 0);
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [selectedFlightId, currentModelId]);

  // ─── Fetch aligned data ────────────────────────────────
  useEffect(() => {
    const requestId = ++alignedRequestRef.current;
    let cancelled = false;
    if (!selectedFlightId || selectedColumns.length === 0) {
      const timer = window.setTimeout(() => {
        setAligned(null);
        setAlignedLoading(false);
      }, 0);
      return () => {
        cancelled = true;
        window.clearTimeout(timer);
      };
    }
    const flightId = selectedFlightId;
    const columns = selectedColumns;
    const filter = filterSpec ?? undefined;
    const timer = window.setTimeout(() => {
      if (cancelled) return;
      setAlignedLoading(true);
      getAlignedData(flightId, columns, filter)
        .then((data) => {
          if (!cancelled && latestFlightRef.current === flightId && alignedRequestRef.current === requestId) setAligned(data);
        })
        .catch((error) => {
          console.error('Failed to fetch aligned data:', error);
          if (!cancelled && latestFlightRef.current === flightId && alignedRequestRef.current === requestId) setAligned(null);
        })
        .finally(() => {
          if (!cancelled && latestFlightRef.current === flightId && alignedRequestRef.current === requestId) setAlignedLoading(false);
        });
    }, 0);
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [selectedFlightId, selectedColumns, filterSpec]);

  // ─── Column toggle ─────────────────────────────────────
  const toggleColumn = (key: string) => {
    setSelectedColumns((prev) =>
      prev.includes(key) ? prev.filter((k) => k !== key) : [...prev, key]
    );
  };

  // ─── Scale factor persistence (debounced) ──────────────────
  const handleScaleChange = (key: string, value: number) => {
    if (!canEditColumns) return;
    const safeVal = isNaN(value) || value === 0 ? 1.0 : value;
    setScaleFactors((prev) => ({ ...prev, [key]: safeVal }));

    // Debounced persist to backend
    const timerKey = `sf_${key}`;
    if (persistTimers.current[timerKey]) {
      clearTimeout(persistTimers.current[timerKey]);
    }
    persistTimers.current[timerKey] = setTimeout(() => {
      if (currentModelId == null) return;
      const dotIdx = key.indexOf('.');
      if (dotIdx <= 0) return;
      const dtKey = key.slice(0, dotIdx);
      const colName = key.slice(dotIdx + 1);
      updateModelColumn(currentModelId, dtKey, colName, { scale_factor: safeVal }).catch(() => {});
    }, 300);
  };

  const toggleGroup = (group: ColumnGroup) => {
    const groupKeys = group.columns.map((c) => c.key);
    const allSelected = groupKeys.every((k) => selectedColumns.includes(k));
    if (allSelected) {
      setSelectedColumns((prev) => prev.filter((k) => !groupKeys.includes(k)));
    } else {
      setSelectedColumns((prev) => [...new Set([...prev, ...groupKeys])]);
    }
  };

  const toggleCollapse = (table: string) => {
    setCollapsedGroups((prev) => {
      const next = new Set(prev);
      if (next.has(table)) next.delete(table); else next.add(table);
      return next;
    });
  };

  // ─── Presets ───────────────────────────────────────────
  const savePreset = async () => {
    const name = presetNameRef.current?.value?.trim();
    if (!name || selectedColumns.length === 0 || currentModelId == null) return;
    try {
      await createPreset(currentModelId, name, selectedColumns);
      const data = await listPresets(currentModelId);
      setPresets(data.presets);
      if (presetNameRef.current) presetNameRef.current.value = '';
    } catch (err) {
      console.error('Failed to save preset', err);
      setErrorMsg('保存预设失败，请重试');
    }
  };

  const loadPreset = (p: Preset) => setSelectedColumns(p.columns);
  const removePreset = async (id: number) => {
    try {
      await deletePreset(id);
      setPresets((prev) => prev.filter((p) => p.id !== id));
    } catch (err) {
      console.error('Failed to delete preset', err);
      setErrorMsg('删除预设失败，请重试');
    }
  };

  // ─── Analysis actions ──────────────────────────────────
  const loadCorrelation = async () => {
    if (!selectedFlightId || selectedColumns.length < 2) return;
    try {
      const data = await getCorrelation(selectedFlightId, selectedColumns);
      setCorrData(data);
      setViewMode('correlation');
    } catch (err) {
      console.error('Failed to load correlation', err);
      setErrorMsg('加载相关性分析失败，请重试');
    }
  };

  const loadAnomaly = async () => {
    if (!selectedFlightId || !anomalyCol) return;
    try {
      const data = await getAnomaly(selectedFlightId, anomalyCol);
      setAnomalyData(data);
      setViewMode('anomaly');
    } catch (err) {
      console.error('Failed to load anomaly', err);
      setErrorMsg('加载异常检测失败，请重试');
    }
  };

  // ─── Flight management helpers ─────────────────────────
  const handleRename = async (id: number) => {
    if (!editName.trim()) { setEditingFlightId(null); return; }
    try {
      await updateFlight(id, editName.trim());
      setEditingFlightId(null);
      onFlightsChanged();
    } catch (err) {
      console.error('Failed to rename flight', err);
      setErrorMsg('重命名失败，请重试');
    }
  };

  const handleDeleteFlight = async (flight: Flight) => {
    try {
      await deleteFlight(flight.id, deleteScopeFor(flight, serverOnline) as DeleteScope);
      if (selectedFlightId === flight.id) {
        const remaining = flights.filter((f) => f.id !== flight.id);
        onSelectFlight(remaining.length > 0 ? remaining[0].id : null);
      }
      setDeletingFlightId(null);
      onFlightsChanged();
    } catch (err) {
      console.error('Failed to delete flight', err);
      setErrorMsg('删除架次失败，请重试');
    }
  };

  const startRename = (f: Flight) => {
    setEditingFlightId(f.id);
    setEditName(f.name);
  };

  // ─── Render ────────────────────────────────────────────
  const TAB_DEFS: { key: ViewMode; label: string }[] = [
    { key: 'chart', label: '时序图' },
    { key: 'correlation', label: '相关性' },
    { key: 'anomaly', label: '异常检测' },
  ];
  const deletingFlight = deletingFlightId ? flights.find((f) => f.id === deletingFlightId) : null;
  const hasAnyColumns = columnGroups.some((g) => g.columns.length > 0);
  const hasAlignedData = !!aligned
    && (aligned.times?.length ?? 0) > 0
    && Object.keys(aligned.series || {}).length > 0;
  const chartEmptyState = (() => {
    if (loading || alignedLoading) return null;
    if (!selectedFlightId) {
      return {
        title: '请选择一个架次',
        description: flights.length > 0 ? '从上方架次选择器中选择需要分析的飞行数据。' : '当前还没有可分析的飞行数据。',
      };
    }
    if (!hasAnyColumns) {
      return {
        title: '该架次没有可展示的数据',
        description: '导入记录存在，但没有解析出有效数据列。常见原因是数据文件仅包含表头，或文件中没有有效数据行。',
      };
    }
    if (selectedColumns.length === 0) {
      return {
        title: '请选择数据列',
        description: '从左侧“数据列”中勾选至少一列后查看时序图。',
      };
    }
    if (!hasAlignedData) {
      return {
        title: '当前选择没有可展示的数据',
        description: filterSpec
          ? '所选数据列在当前筛选条件下没有匹配的数据点。可以调整筛选条件或选择其他数据列。'
          : '所选数据列没有有效数据点。可以选择其他数据列，或检查该架次的原始数据文件。',
      };
    }
    return null;
  })();

  return (
    <div className="h-full flex flex-col">
      {/* ── Toolbar ────────────────────────────────────── */}
      <div className="flex items-center gap-4 px-4 py-2 border-b border-gray-200 bg-gray-50/80 shrink-0 flex-wrap relative">
        <FlightTree
          flights={flights}
          models={models}
          aircraft={aircraft}
          selectedFlightId={selectedFlightId}
          selectedModelId={selectedModelId}
          selectedAircraftId={selectedAircraftId}
          search={flightSearch}
          editingFlightId={editingFlightId}
          deletingFlightId={deletingFlightId}
          canDeleteFlights={canDeleteFlights}
          onSearchChange={setFlightSearch}
          onSelectFlight={onSelectFlight}
          onSelectModel={onSelectModel}
          onSelectAircraft={onSelectAircraft}
          onStartRename={startRename}
          onRequestDelete={setDeletingFlightId}
        />

        {/* Inline rename input */}
        {editingFlightId && (
          <div className="flex items-center gap-1">
            <input
              type="text"
              value={editName}
              onChange={(e) => setEditName(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter') handleRename(editingFlightId);
                if (e.key === 'Escape') setEditingFlightId(null);
              }}
              className="bg-white border border-blue-400 rounded px-2 py-1 text-xs text-gray-800 focus:outline-none focus:border-blue-500 w-40"
              autoFocus
            />
            <button
              type="button"
              onClick={() => handleRename(editingFlightId)}
              className="text-xs px-2 py-1 bg-blue-600 text-white rounded hover:bg-blue-500"
            >
              保存
            </button>
            <button
              type="button"
              onClick={() => setEditingFlightId(null)}
              className="text-xs px-2 py-1 bg-gray-200 text-gray-600 rounded hover:bg-gray-300"
            >
              取消
            </button>
          </div>
        )}

        {/* Inline delete confirmation */}
        {deletingFlight && canDeleteFlights && (
          <div className="flex items-center gap-1">
            <span className="text-xs text-gray-500">{deleteActionLabel(deletingFlight, serverOnline)}?</span>
            <button
              type="button"
              onClick={() => handleDeleteFlight(deletingFlight)}
              className="text-xs px-2 py-1 bg-red-600 text-white rounded hover:bg-red-500"
            >
              是
            </button>
            <button
              type="button"
              onClick={() => setDeletingFlightId(null)}
              className="text-xs px-2 py-1 bg-gray-200 text-gray-600 rounded hover:bg-gray-300"
            >
              否
            </button>
          </div>
        )}

        <div className="h-5 w-px bg-gray-300" />

        {/* View mode tabs */}
        <div className="flex gap-1">
          {TAB_DEFS.map(({ key, label }) => (
            <button
              key={key}
              onClick={() => setViewMode(key)}
              className={`px-3 py-1 text-xs rounded transition-colors ${
                viewMode === key
                  ? 'bg-blue-600 text-white'
                  : 'bg-white border border-gray-300 text-gray-600 hover:bg-gray-100'
              }`}
            >
              {label}
            </button>
          ))}
        </div>

        <div className="flex-1" />

        <label className="flex items-center gap-1.5 text-xs text-gray-500 cursor-pointer" title="将所有列映射到 0~1 范围，消除量纲差异，方便对比趋势">
          <input
            type="checkbox"
            checked={normalize}
            onChange={(e) => setNormalize(e.target.checked)}
            className="rounded accent-blue-600"
          />
          归一化
        </label>

        <button
          onClick={() => {
            flightChartRef.current?.resetZoom();
          }}
          className="px-2 py-0.5 text-xs rounded border border-gray-300 text-gray-500 hover:bg-gray-100 transition-colors"
          title="双击图表可放大，点击此按钮重置缩放"
        >
          ↺ 重置
        </button>
      </div>

      {/* ── Error message banner ─────────────────────────── */}
      {errorMsg && (
        <div className="flex items-center gap-2 px-4 py-2 bg-red-50 border-b border-red-200 text-red-700 text-xs">
          <span>{errorMsg}</span>
          <button
            onClick={() => setErrorMsg(null)}
            className="ml-auto text-red-400 hover:text-red-600 font-bold"
          >
            ×
          </button>
        </div>
      )}

      {/* ── Filter bar ──────────────────────────────────── */}
      <FilterBar
        columnGroups={columnGroups.map((g) => ({
          ...g,
          columns: g.columns.filter((c) => selectedColumns.includes(c.key)),
        })).filter((g) => g.columns.length > 0)}
        filterSpec={filterSpec}
        onChange={setFilterSpec}
        filterPresets={filterPresets}
        onSavePreset={async (name) => {
          if (!filterSpec || currentModelId == null) return;
          await createFilterPreset(currentModelId, name, filterSpec);
          const data = await listFilterPresets(currentModelId);
          setFilterPresets(data.presets);
        }}
        onLoadPreset={(preset) => setFilterSpec(preset.config)}
        onDeletePreset={async (id) => {
          await deleteFilterPreset(id);
          setFilterPresets((prev) => prev.filter((p) => p.id !== id));
        }}
      />

      {/* ── Stats bar ──────────────────────────────────── */}
      {stats && viewMode === 'chart' && (
        <div className="flex items-center gap-6 px-4 py-2 border-b border-gray-200 bg-gray-50/50 shrink-0 text-xs flex-wrap">
          <button
            onClick={() => setSidebarOpen(!sidebarOpen)}
            className={`shrink-0 px-2 py-0.5 rounded text-xs transition-colors ${
              sidebarOpen
                ? 'text-gray-400 hover:text-gray-600 hover:bg-gray-100'
                : 'bg-blue-50 text-blue-600 font-medium'
            }`}
            title={sidebarOpen ? '隐藏左侧筛选栏' : '显示左侧筛选栏'}
          >
            {sidebarOpen ? '◀ 收起筛选' : '▶ 展开筛选'}
          </button>
          <span className="text-gray-500">时长: <strong className="text-gray-800">{Math.round(stats.duration_sec / 60)}min</strong></span>
        </div>
      )}

      {/* ── Main content ───────────────────────────────── */}
      <div className="flex-1 flex min-h-0">
        {/* ── Sidebar ────────────────────────────────── */}
        {sidebarOpen && (
          <aside className="w-60 shrink-0 border-r border-gray-200 overflow-y-auto bg-gray-50/50 flex flex-col">
            {/* Presets section */}
            <div className="p-3 pb-2 border-b border-gray-200">
              <div className="text-xs font-medium text-gray-500 mb-2">预设管理</div>
              {/* Saved presets */}
              {presets.length > 0 && (
                <div className="flex flex-wrap gap-1 mb-2">
                  {presets.map((p) => (
                    <span key={p.id} className="flex items-center gap-0.5">
                      <button
                        onClick={() => loadPreset(p)}
                        className="text-xs px-2 py-0.5 bg-white border border-gray-300 hover:bg-blue-50 hover:border-blue-300 rounded text-gray-700 transition-colors"
                      >
                        {p.name}
                      </button>
                      <button
                        onClick={() => removePreset(p.id)}
                        className="text-gray-400 hover:text-red-500 text-xs font-bold px-0.5"
                      >
                        ×
                      </button>
                    </span>
                  ))}
                </div>
              )}
              {/* Save new */}
              <div className="flex gap-1">
                <input
                  ref={presetNameRef}
                  placeholder="输入预设名称..."
                  className="flex-1 bg-white border border-gray-300 rounded px-2 py-1 text-xs text-gray-700 placeholder-gray-400 focus:outline-none focus:border-blue-500"
                  onKeyDown={(e) => e.key === 'Enter' && savePreset()}
                />
                <button
                  onClick={savePreset}
                  className="text-xs px-2 py-1 bg-blue-600 text-white hover:bg-blue-500 rounded transition-colors shrink-0"
                >
                  保存
                </button>
              </div>
            </div>

            {/* Column groups */}
            <div className="flex-1 overflow-y-auto p-3 space-y-1">
              <div className="text-xs font-medium text-gray-500 mb-2">数据列</div>
              {columnGroups.map((group) => {
                const groupKeys = group.columns.map((c) => c.key);
                const selectedCount = groupKeys.filter((k) => selectedColumns.includes(k)).length;
                const isCollapsed = collapsedGroups.has(group.table);
                return (
                  <div key={group.table} className="border border-gray-200 rounded-lg overflow-hidden bg-white">
                    {/* Group header */}
                    <div className="flex items-center gap-1 px-2 py-1.5 bg-gray-50">
                      <button
                        onClick={() => toggleCollapse(group.table)}
                        className="text-gray-400 hover:text-gray-600 text-[10px] w-4 shrink-0 transition-transform"
                        style={{ transform: isCollapsed ? 'rotate(-90deg)' : 'rotate(0deg)' }}
                      >
                        ▼
                      </button>
                      <button
                        onClick={() => toggleGroup(group)}
                        className="flex items-center justify-between flex-1 text-left text-xs font-medium text-gray-600 hover:text-gray-800"
                      >
                        <span className="min-w-0">
                          <span className="block truncate">{group.label}</span>
                          <span className="block text-[10px] font-normal text-gray-400">{group.row_count ?? 0}行</span>
                        </span>
                        <span className="text-gray-400 text-[10px]">
                          {selectedCount}/{group.columns.length}
                        </span>
                      </button>
                    </div>
                    {/* Group columns */}
                    {!isCollapsed && (
                      <div className="px-2 py-1 space-y-0.5 border-t border-gray-100">
                        {group.columns.map((col) => {
                          const scale = scaleFactors[col.key] ?? 1.0;
                          const isEditing = editingCol === col.key;
                          const isHovered = hoveredCol === col.key;
                          const hasScale = scale !== 1.0;

                          // Show input when: actively editing, OR hovered (with no scale), OR scale is set
                          const showInput = canEditColumns && (isEditing || (isHovered && !hasScale) || hasScale);
                          // Show badge when: scale is set AND not currently editing
                          const showBadge = hasScale && !isEditing;

                          const startEdit = () => {
                            setDraftScale(scale);
                            setEditingCol(col.key);
                            setTimeout(() => editInputRef.current?.select(), 0);
                          };
                          const commitEdit = (value: number) => {
                            handleScaleChange(col.key, value);
                            setEditingCol(null);
                            skipBlurRef.current = true;
                          };
                          const cancelEdit = () => {
                            setEditingCol(null);
                            skipBlurRef.current = true;
                          };

                          return (
                          <div
                            key={col.key}
                            onMouseEnter={() => setHoveredCol(col.key)}
                            onMouseLeave={() => { setHoveredCol(null); }}
                            onClick={() => toggleColumn(col.key)}
                            className="flex items-center gap-1.5 px-1 py-0.5 rounded hover:bg-blue-50 cursor-pointer text-xs"
                          >
                            <input
                              type="checkbox"
                              checked={selectedColumns.includes(col.key)}
                              onChange={() => toggleColumn(col.key)}
                              onClick={(e) => e.stopPropagation()}
                              className="rounded w-3 h-3 accent-blue-600 shrink-0"
                            />
                            <span className="text-gray-600 truncate flex-1">{col.label}</span>

                            {/* Scale input — isolated from parent onClick */}
                            {showInput && (
                              <span className="flex items-center gap-0.5 shrink-0" onClick={(e) => e.stopPropagation()}>
                                {/* × button — resets to 1.0 in either mode */}
                                <button
                                  type="button"
                                  onClick={(e) => {
                                    e.stopPropagation();
                                    if (isEditing) {
                                      commitEdit(1.0);
                                    } else {
                                      handleScaleChange(col.key, 1.0);
                                    }
                                  }}
                                  className="text-gray-400 hover:text-red-500 text-[10px] leading-none w-3.5 h-3.5 flex items-center justify-center rounded hover:bg-red-50"
                                  title="重置缩放系数为 1"
                                >
                                  ×
                                </button>
                                <input
                                  type="number"
                                  step="any"
                                  ref={isEditing ? editInputRef : undefined}
                                  value={isEditing ? (draftScale || '') : scale}
                                  onChange={(e) => {
                                    if (isEditing) {
                                      const v = parseFloat(e.target.value);
                                      if (!isNaN(v)) setDraftScale(v);
                                    }
                                  }}
                                  onFocus={() => { if (!isEditing) startEdit(); }}
                                  onKeyDown={(e) => {
                                    if (e.key === 'Enter') {
                                      e.preventDefault();
                                      commitEdit(isNaN(draftScale) || draftScale === 0 ? 1.0 : draftScale);
                                    }
                                    if (e.key === 'Escape') cancelEdit();
                                  }}
                                  onBlur={() => {
                                    setTimeout(() => {
                                      if (skipBlurRef.current) {
                                        skipBlurRef.current = false;
                                        return;
                                      }
                                      if (editingCol === col.key) {
                                        handleScaleChange(col.key, isNaN(draftScale) || draftScale === 0 ? 1.0 : draftScale);
                                        setEditingCol(null);
                                      }
                                    }, 0);
                                  }}
                                  onClick={(e) => e.stopPropagation()}
                                  className="w-10 px-0.5 py-px border border-gray-300 rounded text-[10px] text-center focus:outline-none focus:border-blue-400"
                                />
                              </span>
                            )}

                            {/* Badge: click to edit */}
                            {showBadge && (canEditColumns ? (
                              <button
                                type="button"
                                onClick={(e) => {
                                  e.stopPropagation();
                                  startEdit();
                                }}
                                className="text-blue-600 text-[10px] font-medium bg-blue-50 hover:bg-blue-100 px-1 rounded shrink-0"
                                title="点击编辑缩放系数"
                              >
                                ×{Number(scale.toFixed(2))}
                              </button>
                            ) : (
                              <span className="text-blue-600 text-[10px] font-medium bg-blue-50 px-1 rounded shrink-0">
                                ×{Number(scale.toFixed(2))}
                              </span>
                            ))}

                            {!showInput && col.unit && (
                              <span className="text-gray-400 text-[10px] shrink-0">{col.unit}</span>
                            )}
                          </div>
                          );
                        })}
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          </aside>
        )}

        {/* ── Content Area ────────────────────────────── */}
        <div className="flex-1 flex flex-col min-w-0 relative">
          {/* Chart Tab */}
          {viewMode === 'chart' && (
            <FlightChart
              ref={flightChartRef}
              active={active}
              aligned={aligned}
              normalize={normalize}
              scaleFactors={scaleFactors}
              selectedColumns={selectedColumns}
              emptyState={chartEmptyState}
            />
          )}

          {/* Correlation Tab */}
          {viewMode === 'correlation' && (
            <div className="flex-1 flex flex-col p-4">
              <div className="flex items-center gap-4 mb-4">
                <button onClick={loadCorrelation} className="px-4 py-2 bg-blue-600 hover:bg-blue-500 text-white rounded-lg text-sm transition-colors">
                  计算相关性
                </button>
                <span className="text-xs text-gray-400">选择 {selectedColumns.length} 个列，点击计算 Pearson 相关系数矩阵</span>
              </div>
              {corrData && (
                <div className="flex-1">
                  <CorrelationHeatmap data={corrData} />
                </div>
              )}
            </div>
          )}

          {/* Anomaly Tab */}
          {viewMode === 'anomaly' && (
            <div className="flex-1 flex flex-col p-4">
              <div className="flex items-center gap-4 mb-4">
                <select
                  value={anomalyCol}
                  onChange={(e) => setAnomalyCol(e.target.value)}
                  className="bg-white border border-gray-300 rounded-lg px-3 py-1.5 text-sm text-gray-800 focus:outline-none focus:border-blue-500"
                >
                  <option value="">选择检测列...</option>
                  {columnGroups.map((g) =>
                    g.columns.map((c) => (
                      <option key={c.key} value={c.key}>{g.label} / {c.label}</option>
                    ))
                  )}
                </select>
                <button onClick={loadAnomaly} disabled={!anomalyCol}
                  className="px-4 py-2 bg-blue-600 hover:bg-blue-500 disabled:opacity-40 text-white rounded-lg text-sm transition-colors">
                  检测
                </button>
              </div>
              {anomalyData && (
                <div className="flex-1">
                  <AnomalyChart data={anomalyData} />
                </div>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

// ═══════════════════════════════════════════════════════════
// Sub-components
