import { useState, useEffect, useRef } from 'react';
import { Pencil, Trash2, ChevronDown, ChevronRight } from 'lucide-react';
import * as echarts from 'echarts';
import { getAlignedData, getStats, getCorrelation, getAnomaly, listPresets, createPreset, deletePreset, listFilterPresets, createFilterPreset, deleteFilterPreset, type ColumnGroup, type AlignedData, type FlightStats, type Preset, type FilterSpec, type FilterPreset } from '../api/analysis';
import { getFlight, updateFlight, deleteFlight, type Flight } from '../api/flights';
import { updateModelColumn, listAircraft, type AircraftModel, type Aircraft, type DeleteScope } from '../api/models';
import FilterBar from '../components/FilterBar';
import { deleteActionLabel, deleteScopeFor, syncStateClass, syncStateLabel } from '../syncStatus';
import { buildChartOption } from '../features/analysis/chartOptions';
import { AnomalyChart, CorrelationHeatmap, type AnomalyData } from '../features/analysis/AnalysisCharts';

interface Props {
  active: boolean;
  flights: Flight[];
  selectedFlightId: number | null;
  onSelectFlight: (id: number) => void;
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
  const [corrData, setCorrData] = useState<any>(null);
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

  // ─── Tree selector state ─────────────────────────────────
  const [treeOpen, setTreeOpen] = useState(false);
  const [treeModelId, setTreeModelId] = useState<number | null>(null);
  const [treeAircraftId, setTreeAircraftId] = useState<number | null>(null);
  const [treeAircraftList, setTreeAircraftList] = useState<Aircraft[]>([]);
  const treeRef = useRef<HTMLDivElement>(null);

  // Close tree on outside click
  useEffect(() => {
    if (!treeOpen) return;
    const onMouseDown = (e: MouseEvent) => {
      if (treeRef.current && !treeRef.current.contains(e.target as Node)) {
        setTreeOpen(false);
      }
    };
    document.addEventListener('mousedown', onMouseDown);
    return () => document.removeEventListener('mousedown', onMouseDown);
  }, [treeOpen]);

  const openTreeModel = async (modelId: number) => {
    setTreeModelId(modelId);
    setTreeAircraftId(null);
    try {
      const data = await listAircraft(modelId);
      setTreeAircraftList(data.aircraft);
    } catch { setTreeAircraftList([]); }
  };

  const openTreeAircraft = (acId: number) => {
    setTreeAircraftId(acId);
  };

  const selectTreeFlight = (flightId: number) => {
    const f = flights.find(fl => fl.id === flightId);
    if (f) {
      onSelectModel(f.model_id);
      onSelectAircraft(f.aircraft_id);
    }
    onSelectFlight(flightId);
    setTreeOpen(false);
  };

  // Search-matched flight IDs (for upward filtering of model/aircraft)
  const searchMatchedIds = (() => {
    if (!flightSearch.trim()) return null;
    const s = flightSearch.toLowerCase();
    return new Set(
      flights.filter(f =>
        f.name.toLowerCase().includes(s) || (f.aircraft_name || f.drone_id || '').toLowerCase().includes(s)
      ).map(f => f.id)
    );
  })();

  // Models with at least one flight matching search
  const visibleModels = searchMatchedIds
    ? models.filter(m => flights.some(f => f.model_id === m.id && searchMatchedIds.has(f.id)))
    : models;

  // Aircraft (for expanded model) with at least one flight matching search
  const visibleTreeAircraft = searchMatchedIds
    ? treeAircraftList.filter(a => flights.some(f => f.aircraft_id === a.id && searchMatchedIds.has(f.id)))
    : treeAircraftList;

  // Tree column flights filtered by selected aircraft + search
  const treeFlightsList = (treeAircraftId
    ? flights.filter(f => f.aircraft_id === treeAircraftId)
    : []).filter(f => {
      if (!flightSearch.trim()) return true;
      const s = flightSearch.toLowerCase();
      return f.name.toLowerCase().includes(s) || (f.aircraft_name || f.drone_id || '').toLowerCase().includes(s);
    });

  const chartRef = useRef<HTMLDivElement>(null);
  const chartInst = useRef<echarts.ECharts | null>(null);
  const presetNameRef = useRef<HTMLInputElement>(null);
  const yZoomRef = useRef({ start: 0, end: 100 });

  // Derive current model_id from the selected flight
  const currentModelId = flights.find(f => f.id === selectedFlightId)?.model_id ?? null;

  // Track latest flight ID to abort stale async operations
  const latestFlightRef = useRef<number | null>(null);
  const alignedRequestRef = useRef(0);

  useEffect(() => {
    setSelectedColumns([]);
    setFilterSpec(null);
    setColumnGroups([]);
    setAligned(null);
    setPresets([]);
    setFilterPresets([]);
    setCorrData(null);
    setAnomalyData(null);
    setAnomalyCol('');
  }, [currentModelId]);

  // ─── Load flight data ──────────────────────────────────
  useEffect(() => {
    // Always update the ref so stale in-flight requests are aborted
    // when selectedFlightId becomes null (otherwise they'd write
    // data for a flight that is no longer selected).
    latestFlightRef.current = selectedFlightId;
    if (!selectedFlightId) {
      setAligned(null);
      return;
    }
    setLoading(true);
    setAligned(null);  // clear chart immediately when flight changes
    Promise.all([
      getFlight(selectedFlightId),
      getStats(selectedFlightId),
      currentModelId != null ? listPresets(currentModelId) : Promise.resolve({ presets: [] }),
      currentModelId != null ? listFilterPresets(currentModelId) : Promise.resolve({ presets: [] }),
    ]).then(([flightData, statsData, presetData, fpData]) => {
      // Abort if flight changed during fetch
      if (latestFlightRef.current !== selectedFlightId) return;
      setColumnGroups(flightData.columns);
      setCollapsedGroups(new Set(flightData.columns.map((g: ColumnGroup) => g.table)));
      // Initialize scale factors from column metadata
      const sf: Record<string, number> = {};
      flightData.columns.forEach((g: ColumnGroup) => {
        g.columns.forEach((c: any) => {
          sf[c.key] = c.scale_factor ?? 1.0;
        });
      });
      setScaleFactors(sf);
      setStats(statsData);
      setPresets(presetData.presets);
      setFilterPresets(fpData.presets);

      setSelectedColumns((prev) => {
        const newKeys = new Set(
          flightData.columns.flatMap((g) => g.columns.map((c) => c.key))
        );
        const kept = prev.filter((k) => newKeys.has(k));
        if (kept.length > 0) return kept;
        // First load: pick sensible defaults
        const defaults = [
          'pos.lat', 'pos.lng', 'gps.nava_alt',
          'engine.engine_rpm', 'drone_state.battery_pct',
        ];
        return defaults.filter((d) => newKeys.has(d));
      });
      setCorrData(null);
      setAnomalyData(null);
    }).catch((err) => {
      console.error('Failed to load flight data:', err);
    }).finally(() => {
      if (latestFlightRef.current === selectedFlightId) {
        setLoading(false);
      }
    });
  }, [selectedFlightId]);

  // ─── Fetch aligned data ────────────────────────────────
  useEffect(() => {
    const requestId = ++alignedRequestRef.current;
    if (!selectedFlightId || selectedColumns.length === 0) {
      setAligned(null);
      setAlignedLoading(false);
      return;
    }
    const flightId = selectedFlightId;
    setAlignedLoading(true);
    getAlignedData(flightId, selectedColumns, filterSpec ?? undefined)
      .then((data) => {
        // Abort if flight changed during fetch
        if (latestFlightRef.current === flightId && alignedRequestRef.current === requestId) {
          setAligned(data);
        }
      })
      .catch((err) => {
        console.error('Failed to fetch aligned data:', err);
        if (latestFlightRef.current === flightId && alignedRequestRef.current === requestId) {
          setAligned(null);
        }
      })
      .finally(() => {
        if (latestFlightRef.current === flightId && alignedRequestRef.current === requestId) {
          setAlignedLoading(false);
        }
      });
  }, [selectedFlightId, selectedColumns, filterSpec]);

  // ─── Chart: lifecycle ──────────────────────────────────
  //
  // Why one effect, not two:
  // Previously we had two effects — one for init/dispose (keyed on
  // viewMode/active) and one for setOption (keyed on data). The split
  // caused crashes ("Cannot read properties of undefined (reading
  // 'group')" / "__ec_inner_*") because the update effect would call
  // setOption on an instance whose internal component tree carried
  // residue from the previous flight. ECharts 6.1 + notMerge=true
  // doesn't handle dramatic shape changes (different yAxis count, unit
  // groups, hasFilter on/off) cleanly.
  //
  // The fix: dispose and re-init the chart from scratch on every
  // significant change. Implementation note: zrender dblclick handler
  // is re-bound after each init (cheap, single listener).
  //
  // Container size handling: ResizeObserver still drives resize() on
  // an existing instance, and re-init when the container first gains
  // dimensions (deferred init for keep-alive tabs).
  useEffect(() => {
    if (!active || viewMode !== 'chart' || !chartRef.current) {
      if (chartInst.current) {
        try { chartInst.current.dispose(); } catch (e) { /* ignore */ }
        chartInst.current = null;
      }
      return;
    }

    const container = chartRef.current;

    const bindDblClick = (inst: echarts.ECharts) => {
      const zr = inst.getZr();
      zr.on('dblclick', (e: any) => {
        const ZOOM = 2;
        const MIN_RANGE = 2;
        const opt = inst.getOption();
        const dzList = (opt?.dataZoom as any[]) || [];
        const xSlider = dzList.find((d: any) => d.type === 'slider' && d.yAxisIndex === undefined);
        const ySlider = dzList.find((d: any) => d.type === 'slider' && (d.yAxisIndex !== undefined));
        const xStart: number = xSlider?.start ?? 0;
        const xEnd: number = xSlider?.end ?? 100;
        const yStart: number = ySlider?.start ?? 0;
        const yEnd: number = ySlider?.end ?? 100;

        const gridModel = (inst as any)?.getModel().getComponent('grid', 0);
        const rect = (gridModel as any)?.coordinateSystem?.getRect?.();
        const fx = rect ? Math.max(0, Math.min(1, (e.offsetX - rect.x) / rect.width)) : 0.5;
        const fy = rect ? 1 - Math.max(0, Math.min(1, (e.offsetY - rect.y) / rect.height)) : 0.5;
        const xCenter = xStart + fx * (xEnd - xStart);
        const yCenter = yStart + fy * (yEnd - yStart);

        const xRange = xEnd - xStart;
        if (xRange > MIN_RANGE) {
          const newXRange = xRange / ZOOM;
          inst.dispatchAction({
            type: 'dataZoom',
            dataZoomIndex: 0,
            start: Math.max(0, xCenter - newXRange / 2),
            end: Math.min(100, xCenter + newXRange / 2),
          });
        }

        const yRange = yEnd - yStart;
        if (yRange > MIN_RANGE) {
          const newYRange = yRange / ZOOM;
          yZoomRef.current = {
            start: Math.max(0, yCenter - newYRange / 2),
            end: Math.min(100, yCenter + newYRange / 2),
          };
          inst.dispatchAction({
            type: 'dataZoom',
            dataZoomId: 'ySlider',
            start: yZoomRef.current.start,
            end: yZoomRef.current.end,
          });
        }
      });
    };

    // Build option for the current data state. Computed inside the
    // effect so it captures the latest aligned/normalize/scaleFactors
    // without needing a separate effect.
    const buildAndApply = (inst: echarts.ECharts) => {
      if (!aligned) return;
      try {
        const option = buildChartOption(aligned, normalize, scaleFactors);
        inst.setOption(option, true);
        // WebView2 quirk: canvas size sometimes lags one frame behind
        // layout when the container has just become visible. Force a
        // resize on the next frame so ECharts paints against the
        // now-flushed dimensions.
        requestAnimationFrame(() => {
          try { inst.resize(); } catch (e) { /* ignore */ }
        });
      } catch (e) {
        console.error('setOption failed:', e);
      }
    };

    // Create a fresh instance every time this effect runs. This is the
    // cornerstone of the fix: ECharts 6.1 cannot reliably diff between
    // option shapes that differ in yAxis count / unit groups / hasFilter,
    // so we never reuse an instance across data changes.
    const createInstance = () => {
      if (container.clientWidth === 0 || container.clientHeight === 0) return null;
      try {
        const inst = echarts.init(container);
        bindDblClick(inst);
        return inst;
      } catch (e) {
        console.error('ECharts init failed:', e);
        return null;
      }
    };

    // Dispose any existing instance from a previous effect run
    // (shouldn't normally happen — cleanup below handles it — but
    // defensive against StrictMode double-invoke).
    if (chartInst.current) {
      try { chartInst.current.dispose(); } catch (e) { /* ignore */ }
      chartInst.current = null;
    }

    chartInst.current = createInstance();
    if (chartInst.current) buildAndApply(chartInst.current);

    const ro = new ResizeObserver(() => {
      if (!chartInst.current) {
        // Deferred init for keep-alive: container just gained dimensions.
        chartInst.current = createInstance();
        if (chartInst.current) buildAndApply(chartInst.current);
      } else {
        try { chartInst.current.resize(); } catch (e) { /* ignore */ }
      }
    });
    ro.observe(container);

    return () => {
      ro.disconnect();
      if (chartInst.current) {
        try { chartInst.current.dispose(); } catch (e) { /* ignore */ }
        chartInst.current = null;
      }
    };
  }, [viewMode, active, aligned, normalize, scaleFactors]);

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
        onSelectFlight(remaining.length > 0 ? remaining[0].id : null as any);
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
        {/* Tree selector: Model → Aircraft → Flight */}
        <div className="flex items-center gap-2" ref={treeRef}>
          {/* Trigger button */}
          <button
            onClick={() => setTreeOpen(!treeOpen)}
            className="flex items-center gap-1 bg-white border border-gray-300 rounded-lg pl-3 pr-2 py-1.5 text-sm hover:border-blue-400 focus:outline-none focus:border-blue-500 focus:ring-1 focus:ring-blue-500 min-w-[180px] max-w-[360px]"
          >
            {selectedFlightId ? (
              <span className="text-gray-700 truncate">
                {(() => {
                  const f = flights.find(fl => fl.id === selectedFlightId);
                  const m = models.find(mo => mo.id === selectedModelId);
                  const a = aircraft.find(ac => ac.id === selectedAircraftId);
                  if (f && m && a) return `${m.name} / ${a.name} / ${f.name}`;
                  return f?.name || '选择架次...';
                })()}
              </span>
            ) : (
              <span className="text-gray-400">选择架次...</span>
            )}
            <ChevronDown className={`w-4 h-4 text-gray-400 ml-auto shrink-0 transition-transform ${treeOpen ? 'rotate-180' : ''}`} />
          </button>

          {/* Tree popover */}
          {treeOpen && (
            <div className="absolute top-full left-4 mt-1 z-50 flex bg-white border border-gray-200 rounded-lg shadow-lg max-h-[320px]">
              {/* Column 1: Models */}
              <div className="w-44 border-r border-gray-100 overflow-y-auto py-1">
                <div className="px-3 py-1.5 text-xs text-gray-400 font-medium sticky top-0 bg-white">机型</div>
                {visibleModels.length === 0 ? (
                  <div className="px-3 py-2 text-xs text-gray-400">无匹配机型</div>
                ) : (
                  visibleModels.map((m) => (
                    <button
                      key={m.id}
                      onMouseEnter={() => openTreeModel(m.id)}
                      className={`w-full text-left px-3 py-1.5 text-sm flex items-center justify-between ${
                        treeModelId === m.id
                          ? 'bg-blue-50 text-blue-700'
                          : 'text-gray-700 hover:bg-gray-50'
                      }`}
                    >
                      <span className="truncate">{m.name}</span>
                      <ChevronRight className="w-3.5 h-3.5 text-gray-300 shrink-0" />
                    </button>
                  ))
                )}
              </div>

              {/* Column 2: Aircraft (visible when model selected) */}
              {treeModelId && (
                <div className="w-44 border-r border-gray-100 overflow-y-auto py-1">
                  <div className="px-3 py-1.5 text-xs text-gray-400 font-medium sticky top-0 bg-white">飞机</div>
                  {visibleTreeAircraft.length === 0 ? (
                    <div className="px-3 py-2 text-xs text-gray-400">无匹配飞机</div>
                  ) : (
                    visibleTreeAircraft.map((a) => (
                      <button
                        key={a.id}
                        onMouseEnter={() => openTreeAircraft(a.id)}
                        className={`w-full text-left px-3 py-1.5 text-sm flex items-center justify-between ${
                          treeAircraftId === a.id
                            ? 'bg-blue-50 text-blue-700'
                            : 'text-gray-700 hover:bg-gray-50'
                        }`}
                      >
                        <span className="truncate">{a.name}</span>
                        <ChevronRight className="w-3.5 h-3.5 text-gray-300 shrink-0" />
                      </button>
                    ))
                  )}
                </div>
              )}

              {/* Column 3: Flights (visible when aircraft selected) */}
              {treeAircraftId && (
                <div className="w-52 overflow-y-auto py-1">
                  <div className="px-3 py-1.5 text-xs text-gray-400 font-medium sticky top-0 bg-white">架次</div>
                  {treeFlightsList.length === 0 ? (
                    <div className="px-3 py-2 text-xs text-gray-400">无架次</div>
                  ) : (
                    treeFlightsList.map((f) => (
                      <button
                        key={f.id}
                        onClick={() => selectTreeFlight(f.id)}
                        className={`w-full text-left px-3 py-1.5 text-sm ${
                          f.id === selectedFlightId
                            ? 'bg-blue-50 text-blue-700'
                            : 'text-gray-700 hover:bg-gray-50'
                        }`}
                      >
                        <span className="flex items-center gap-2 min-w-0">
                          <span className="truncate">{f.name}</span>
                          <span className={`shrink-0 text-[10px] px-1.5 py-0.5 rounded border ${syncStateClass(f.sync_state)}`}>
                            {syncStateLabel(f.sync_state)}
                          </span>
                        </span>
                      </button>
                    ))
                  )}
                </div>
              )}
            </div>
          )}

          {/* Search */}
          <input
            type="text"
            value={flightSearch}
            onChange={(e) => setFlightSearch(e.target.value)}
            placeholder="搜索架次..."
            className="bg-white border border-gray-300 rounded-lg px-2 py-1.5 text-xs text-gray-700 placeholder-gray-400 focus:outline-none focus:border-blue-500 w-32"
          />

          {/* Rename button */}
          {selectedFlightId && editingFlightId !== selectedFlightId && (
            <button
              onClick={() => {
                const f = flights.find((fl) => fl.id === selectedFlightId);
                if (f) startRename(f);
              }}
              className="text-gray-400 hover:text-blue-500 text-xs px-1.5 py-1 rounded hover:bg-gray-100 shrink-0"
              title="重命名"
            >
              <Pencil className="w-4 h-4" />
            </button>
          )}

          {/* Delete button */}
          {selectedFlightId && canDeleteFlights && deletingFlightId !== selectedFlightId && (
            <button
              onClick={() => setDeletingFlightId(selectedFlightId)}
              className="text-gray-400 hover:text-red-500 px-1.5 py-1 rounded hover:bg-red-50 shrink-0 flex items-center"
              title="删除"
            >
              <Trash2 className="w-4 h-4" />
            </button>
          )}
        </div>

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
            yZoomRef.current = { start: 0, end: 100 };
            chartInst.current?.dispatchAction({
              type: 'dataZoom',
              dataZoomIndex: 0,
              start: 0,
              end: 100,
            });
            chartInst.current?.dispatchAction({
              type: 'dataZoom',
              dataZoomId: 'ySlider',
              start: 0,
              end: 100,
            });
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
            <>
              <div ref={chartRef} className="flex-1 min-h-0" />
              {chartEmptyState && (
                <EmptyState
                  title={chartEmptyState.title}
                  description={chartEmptyState.description}
                />
              )}
              <ChartDebugBadge
                active={active}
                chartRef={chartRef}
                chartInst={chartInst}
                aligned={aligned}
                selectedColumns={selectedColumns}
              />
            </>
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
// ═══════════════════════════════════════════════════════════

function EmptyState({ title, description }: { title: string; description: string }) {
  return (
    <div className="absolute inset-0 flex items-center justify-center pointer-events-none">
      <div className="max-w-md px-6 py-5 text-center">
        <div className="mx-auto mb-3 flex h-10 w-10 items-center justify-center rounded-full border border-gray-200 bg-gray-50 text-gray-400">
          !
        </div>
        <div className="text-sm font-medium text-gray-700">{title}</div>
        <div className="mt-1 text-xs leading-5 text-gray-500">{description}</div>
      </div>
    </div>
  );
}

// In-app debug HUD — no DevTools needed.
// Sticks to the bottom-right of the chart area, updates ~4×/sec,
// shows the values needed to diagnose the "chart blank after tab
// switch" bug: container dimensions, instance liveness, data
// presence, etc. Click to force a resize.
function ChartDebugBadge({
  active,
  chartRef,
  chartInst,
  aligned,
  selectedColumns,
}: {
  active: boolean;
  chartRef: React.RefObject<HTMLDivElement | null>;
  chartInst: React.MutableRefObject<echarts.ECharts | null>;
  aligned: AlignedData | null;
  selectedColumns: string[];
}) {
  const [tick, setTick] = useState(0);
  useEffect(() => {
    const id = setInterval(() => setTick((t) => t + 1), 250);
    return () => clearInterval(id);
  }, []);

  const el = chartRef.current;
  const w = el?.clientWidth ?? 0;
  const h = el?.clientHeight ?? 0;
  const visible = el ? (el.offsetParent !== null) : false;
  const inst = chartInst.current;
  const instW = inst ? (inst.getWidth?.() ?? -1) : -1;
  const instH = inst ? (inst.getHeight?.() ?? -1) : -1;
  const seriesCount = aligned ? Object.keys(aligned.series || {}).length : 0;
  const timesCount = aligned?.times?.length ?? 0;

  const forceResize = () => {
    if (inst) {
      try { inst.resize(); } catch { /* ignore */ }
    }
  };

  return (
    <div
      onClick={forceResize}
      title="Click to force chart.resize()"
      className="absolute bottom-2 right-2 z-50 bg-black/75 text-white text-[10px] font-mono px-2 py-1 rounded leading-tight cursor-pointer hover:bg-black/90 select-none"
      style={{ pointerEvents: 'auto' }}
    >
      <div>active:{String(active)} vis:{String(visible)}</div>
      <div>DOM:{w}×{h} inst:{inst ? `${instW}×${instH}` : 'null'}</div>
      <div>data:{seriesCount}s/{timesCount}p cols:{selectedColumns.length}</div>
      <div>tick:{tick} (click→resize)</div>
    </div>
  );
}
