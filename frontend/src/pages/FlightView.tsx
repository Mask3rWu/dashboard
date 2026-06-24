import { useState, useEffect, useRef } from 'react';
import { Pencil, Trash2, ChevronDown, ChevronRight } from 'lucide-react';
import * as echarts from 'echarts';
import {
  getFlight, getAlignedData, getAlerts, getStats, getCorrelation, getAnomaly,
  listPresets, createPreset, deletePreset,
  listFilterPresets, createFilterPreset, deleteFilterPreset,
  updateFlight, deleteFlight, updateModelColumn,
  listAircraft,
  type Flight, type ColumnGroup, type AircraftModel, type Aircraft,
  type AlignedData, type AlertItem, type FlightStats, type Preset,
  type FilterSpec, type FilterPreset,
} from '../api';
import FilterBar from '../components/FilterBar';

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
}

type ViewMode = 'chart' | 'map' | 'alerts' | 'correlation' | 'anomaly';

const ALERT_EXPLANATIONS: Record<string, string> = {
  '当前数据链传输距离': '数据链（遥控/图传）传输距离过远，可能导致信号丢失',
  '链路遥控中断': '遥控器与飞机之间的控制链路断开，飞机可能进入失控保护',
  '舵机过小': '舵机输出值低于正常范围，可能影响飞行控制精度',
  '舵机过大': '舵机输出值超出正常范围，舵机可能过载',
  '电池低电压': '机载电池电压过低，需要尽快降落',
  '电压过高': '某路电压超出安全阈值，可能损坏电子设备',
  '转速过高': '发动机转速超过安全上限，可能损坏发动机',
  '转速过低': '发动机转速异常偏低，可能导致动力不足',
  '温度过高': '某部件温度超过安全阈值',
  'GPS信号差': 'GPS定位精度下降，影响导航精度',
  '链路通信中断': '通信链路完全断开',
  '高度异常': '飞行高度数据异常波动',
  '姿态角过大': '飞机俯仰/横滚角超过安全范围',
  '电池电量低': '电池剩余电量不足，需尽快返航',
  '28V电压异常': '28V供电系统电压异常',
  '12V电压异常': '12V供电系统电压异常',
};

function explainAlert(desc: string): string {
  for (const [key, explanation] of Object.entries(ALERT_EXPLANATIONS)) {
    if (desc.includes(key)) return explanation;
  }
  return '飞行状态告警，需结合前后数据综合判断';
}

// ═══════════════════════════════════════════════════════════════
// Chart option builder — pure function, hoisted out of the
// component so it allocates fresh each call and isn't recreated
// on every render. Computes a complete ECharts option from current
// aligned data + normalization + per-column scale factors.
// ═══════════════════════════════════════════════════════════════
function buildChartOption(
  aligned: AlignedData,
  normalize: boolean,
  scaleFactors: Record<string, number>,
): echarts.EChartsOption {
  const times = aligned.times || [];
  const seriesList = Object.entries(aligned.series || {});

  const getValues = (vals: (number | null)[], key: string) => {
    const sf = scaleFactors[key] ?? 1.0;
    const scaled = vals.map((v) => (v !== null ? v * sf : null));
    if (!normalize) return scaled;
    const nums = scaled.filter((v) => v !== null) as number[];
    if (nums.length === 0) return scaled;
    const min = Math.min(...nums);
    const max = Math.max(...nums);
    const range = max - min || 1;
    return scaled.map((v) => (v !== null ? (v - min) / range : null));
  };

  const colors = ['#2563eb', '#dc2626', '#16a34a', '#ca8a04', '#7c3aed', '#0891b2', '#db2777', '#ea580c'];
  const isNorm = normalize;

  // Group series by semantic unit (e.g. ° → °_pos vs °_angle)
  type UnitGroup = { unit: string; items: [string, typeof aligned.series[string]][] };
  const unitMap = new Map<string, UnitGroup['items']>();
  seriesList.forEach(([key, s]) => {
    const raw = s.unit || '-';
    let u = raw;
    if (raw === '°') {
      const col = key.split('.').pop()?.toLowerCase() || '';
      if (col.includes('lat') || col.includes('lng')) u = '° (经纬度)';
      else u = '° (角度)';
    }
    if (!unitMap.has(u)) unitMap.set(u, []);
    unitMap.get(u)!.push([key, s]);
  });
  const unitGroups: UnitGroup[] = Array.from(unitMap.entries()).map(([unit, items]) => ({ unit, items }));

  const seriesColor = (si: number) => colors[si % colors.length];
  const unitColor = (gi: number) => colors[gi % colors.length];

  const yAxes: any[] = [];
  const keyToGroup = new Map<string, number>();

  if (isNorm) {
    yAxes.push({
      type: 'value',
      name: '归一化 (0~1)',
      nameTextStyle: { color: '#6b7280', fontSize: 11 },
      axisLabel: { color: '#9ca3af', fontSize: 10 },
      splitLine: { lineStyle: { color: '#f3f4f6' } },
    });
    seriesList.forEach(([key]) => keyToGroup.set(key, 0));
  } else {
    const AXIS_W = 50;
    unitGroups.forEach((g, gi) => {
      const side = gi % 2 === 0 ? 'left' : 'right';
      const sameSide = unitGroups.filter((_, i) => i % 2 === gi % 2 && i < gi).length;
      const color = unitColor(gi);
      yAxes.push({
        type: 'value',
        name: g.unit,
        nameTextStyle: { color, fontSize: 10, fontWeight: 'bold' as const },
        axisLabel: { color: '#9ca3af', fontSize: 10 },
        axisLine: { lineStyle: { color } },
        position: side as 'left' | 'right',
        offset: sameSide * AXIS_W,
        splitLine: { show: gi === 0, lineStyle: { color: '#f3f4f6' } },
      });
      g.items.forEach(([key]) => keyToGroup.set(key, gi));
    });
  }
  const seriesYIndex = seriesList.map(([key]) => keyToGroup.get(key)!);

  const leftUnits = isNorm ? 0 : unitGroups.filter((_, i) => i % 2 === 0).length;
  const rightUnits = isNorm ? 0 : unitGroups.filter((_, i) => i % 2 === 1).length;
  const AXIS_WIDTH = 50;
  const YZOOM_W = 24;
  const leftPad = isNorm ? 60 : 80 + (leftUnits > 0 ? (leftUnits - 1) * AXIS_WIDTH : 0);
  const rightPad = (isNorm ? 40 : 80 + (rightUnits > 0 ? (rightUnits - 1) * AXIS_WIDTH : 0)) + YZOOM_W;

  const segments = aligned.segments || [];
  const hasFilter = segments.length > 0;
  const dzIndicatorData = hasFilter
    ? times.map((_, i) => (aligned.mask?.[i] ? 1 : 0))
    : [];

  // Always use array form for grid/xAxis/yAxis. Mixing object-form
  // and array-form between consecutive setOption calls is one of the
  // triggers for the "__ec_inner_*" / "group" undefined crashes
  // in ECharts 6.1 — keeping the shape stable avoids the diff path.
  const grid = hasFilter ? [
    { left: leftPad, right: rightPad, top: 40, bottom: 60 },
    { left: leftPad, right: rightPad, bottom: 6, height: 18 },
  ] : [
    { left: leftPad, right: rightPad, top: 40, bottom: 60 },
  ];

  const xAxisArr = hasFilter ? [
    {
      type: 'category' as const, data: times, gridIndex: 0,
      axisLabel: { color: '#9ca3af', fontSize: 10, interval: Math.max(1, Math.floor(times.length / 20)) },
      axisLine: { lineStyle: { color: '#e5e7eb' } },
    },
    {
      type: 'category' as const, data: times, gridIndex: 1,
      axisLabel: { show: false }, axisTick: { show: false },
      axisLine: { show: false }, splitLine: { show: false },
    },
  ] : [
    {
      type: 'category' as const, data: times, gridIndex: 0,
      axisLabel: { color: '#9ca3af', fontSize: 10, interval: Math.max(1, Math.floor(times.length / 20)) },
      axisLine: { lineStyle: { color: '#e5e7eb' } },
    },
  ];

  const mainYAxes = yAxes.map((a) => ({ ...a, gridIndex: 0 }));
  const yAxisArr = hasFilter ? [
    ...mainYAxes,
    { type: 'value', gridIndex: 1, min: 0, max: 1, axisLabel: { show: false }, axisTick: { show: false }, axisLine: { show: false }, splitLine: { show: false } },
    { type: 'value', gridIndex: 0, min: 0, max: 1, axisLabel: { show: false }, axisTick: { show: false }, axisLine: { show: false }, splitLine: { show: false } },
  ] : mainYAxes;

  // dataZoom must explicitly list the main yAxis indices.
  // Using `yAxisIndex: 'all'` crashes ECharts when yAxis contains
  // helper axes on a different grid.
  // ECharts 6.1 has an additional quirk: a single-element array
  // `yAxisIndex: [0]` triggers a different (apparently buggy)
  // internal code path that crashes during render when other
  // components (markLine, multi-grid xAxis) are present. Use a
  // plain number when there's only one main yAxis.
  const yAxisIndexForDZ: number | number[] = mainYAxes.length === 1
    ? 0
    : mainYAxes.map((_, i) => i);

  return {
    color: seriesList.map((_, i) => seriesColor(i)),
    tooltip: {
      trigger: 'axis',
      backgroundColor: '#fff',
      borderColor: '#e5e7eb',
      textStyle: { color: '#374151', fontSize: 12 },
      formatter: (params: any) => {
        if (!Array.isArray(params)) return '';
        const mainParams = params.filter((p: any) =>
          p.seriesName !== '__dz_indicator__' && p.seriesName !== '__filter_bg__');
        if (mainParams.length === 0) return '';
        const time = mainParams[0]?.name || '';
        let html = `<div class="text-xs font-mono text-gray-500">${time}</div>`;
        mainParams.forEach((p: any) => {
          if (p.value?.[1] != null) {
            const sIdx = p.seriesIndex;
            const key = seriesList[sIdx]?.[0] || '';
            const sf = key ? (scaleFactors[key] ?? 1.0) : 1.0;
            const displayVal = Number(p.value[1]).toFixed(2);
            html += `<div>${p.marker} ${p.seriesName}: <strong>${displayVal}</strong>`;
            if (sf !== 1.0) {
              const rawVal = (Number(p.value[1]) / sf).toFixed(3);
              html += ` <span style="color:#9ca3af;font-size:10px">(原始: ${rawVal}×${sf})</span>`;
            }
            html += `</div>`;
          }
        });
        return html;
      },
    },
    legend: {
      type: 'scroll', top: 0,
      textStyle: { color: '#6b7280', fontSize: 11 },
      data: seriesList.map(([, s]) => isNorm ? s.label : `${s.label} (${s.unit || '-'})`),
    },
    grid,
    xAxis: xAxisArr,
    yAxis: yAxisArr,
    dataZoom: [
      { type: 'slider', start: 0, end: 100, height: 18, bottom: 6,
        backgroundColor: 'rgba(249,250,251,0.55)',
      },
      { type: 'inside', xAxisIndex: 0 },
      { type: 'inside', yAxisIndex: yAxisIndexForDZ, zoomOnMouseWheel: 'ctrl', id: 'yInside' },
      { type: 'slider', yAxisIndex: yAxisIndexForDZ, start: 0, end: 100, right: 2, width: 18,
        backgroundColor: 'rgba(249,250,251,0.55)', id: 'ySlider',
      },
    ],
    series: [
      ...seriesList.map(([key, s], i) => {
        const values = getValues(s.values, key);
        // Attach the alert markLine to the first line series only.
        // (Top-level markLine causes "undefined.group" crashes; it
        // must be owned by a series.)
        const isFirst = i === 0;
        const alertMarkLine = {};
        return {
          name: s.label + (isNorm ? '' : s.unit ? ` (${s.unit})` : ''),
          type: 'line' as const,
          yAxisIndex: seriesYIndex[i],
          xAxisIndex: 0,
          data: times.map((t, j) => [t, values[j]]),
          smooth: true,
          showSymbol: false,
          z: 1,
          lineStyle: { width: 1.5, color: seriesColor(i) },
          ...alertMarkLine,
        };
      }),
      ...(hasFilter ? [{
        name: '__filter_bg__',
        type: 'bar' as const,
        xAxisIndex: 0,
        yAxisIndex: mainYAxes.length + 1,
        data: dzIndicatorData,
        itemStyle: { color: 'rgba(147, 197, 253, 0.22)' },
        barWidth: '100%',
        barCategoryGap: '0%',
        tooltip: { show: false },
        silent: true,
        z: -1,
      }] : []),
      ...(hasFilter ? [{
        name: '__dz_indicator__',
        type: 'bar' as const,
        xAxisIndex: 1,
        yAxisIndex: mainYAxes.length,
        data: dzIndicatorData,
        itemStyle: { color: '#3b82f6', borderColor: '#3b82f6', opacity: 0.5 },
        barWidth: '100%',
        tooltip: { show: false },
        silent: true,
        z: 0,
      }] : []),
    ],
  };
}

export default function FlightView({
  active,
  flights, selectedFlightId, onSelectFlight, onFlightsChanged,
  models, selectedModelId, onSelectModel,
  aircraft, selectedAircraftId, onSelectAircraft,
}: Props) {
  // ─── State ─────────────────────────────────────────────
  const [columnGroups, setColumnGroups] = useState<ColumnGroup[]>([]);
  const [selectedColumns, setSelectedColumns] = useState<string[]>([]);
  const [aligned, setAligned] = useState<AlignedData | null>(null);
  const [alerts, setAlerts] = useState<AlertItem[]>([]);
  const [stats, setStats] = useState<FlightStats | null>(null);
  const [presets, setPresets] = useState<Preset[]>([]);
  const [normalize, setNormalize] = useState(false);
  const [refTable, setRefTable] = useState('gps');
  const [, setLoading] = useState(false);
  const [viewMode, setViewMode] = useState<ViewMode>('chart');
  const [anomalyCol, setAnomalyCol] = useState('');
  const [anomalyData, setAnomalyData] = useState<any>(null);
  const [corrData, setCorrData] = useState<any>(null);
  const [sidebarOpen, setSidebarOpen] = useState(true);
  const [collapsedGroups, setCollapsedGroups] = useState<Set<string>>(new Set());
  const [showMapLegend, setShowMapLegend] = useState(true);
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
        f.name.toLowerCase().includes(s) || (f.aircraft_serial || f.drone_id || '').toLowerCase().includes(s)
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
      return f.name.toLowerCase().includes(s) || (f.aircraft_serial || f.drone_id || '').toLowerCase().includes(s);
    });

  const chartRef = useRef<HTMLDivElement>(null);
  const chartInst = useRef<echarts.ECharts | null>(null);
  const presetNameRef = useRef<HTMLInputElement>(null);
  const yZoomRef = useRef({ start: 0, end: 100 });

  // Derive current model_id from the selected flight
  const currentModelId = flights.find(f => f.id === selectedFlightId)?.model_id ?? null;

  // Track latest flight ID to abort stale async operations
  const latestFlightRef = useRef<number | null>(null);

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
      getAlerts(selectedFlightId),
      getStats(selectedFlightId),
      currentModelId != null ? listPresets(currentModelId) : Promise.resolve({ presets: [] }),
      currentModelId != null ? listFilterPresets(currentModelId) : Promise.resolve({ presets: [] }),
    ]).then(([flightData, alertData, statsData, presetData, fpData]) => {
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
      setAlerts(alertData.alerts);
      setStats(statsData);
      setPresets(presetData.presets);
      setFilterPresets(fpData.presets);

      // Preserve previously selected columns that still exist in the new flight.
      // Only fall back to defaults on first load (no prior selection).
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
    if (!selectedFlightId || selectedColumns.length === 0) return;
    const flightId = selectedFlightId;
    getAlignedData(flightId, selectedColumns, refTable, 0.5, filterSpec ?? undefined)
      .then((data) => {
        // Abort if flight changed during fetch
        if (latestFlightRef.current === flightId) {
          setAligned(data);
        }
      })
      .catch((err) => {
        console.error('Failed to fetch aligned data:', err);
        if (latestFlightRef.current === flightId) {
          setAligned(null);
        }
      });
  }, [selectedFlightId, selectedColumns, refTable, filterSpec]);

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

  // ─── Alert grouping ────────────────────────────────────
  const alertGroups = alerts.reduce((acc, a) => {
    const key = a.desc || '(无描述)';
    if (!acc[key]) acc[key] = [];
    acc[key].push(a);
    return acc;
  }, {} as Record<string, AlertItem[]>);

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

  const handleDeleteFlight = async (id: number) => {
    try {
      await deleteFlight(id);
      if (selectedFlightId === id) {
        const remaining = flights.filter((f) => f.id !== id);
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
    { key: 'map', label: '轨迹地图' },
    { key: 'alerts', label: `告警 (${alerts.length})` },
    { key: 'correlation', label: '相关性' },
    { key: 'anomaly', label: '异常检测' },
  ];

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
                  if (f && m && a) return `${m.name} / ${a.serial_number} / ${f.name}`;
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
                        <span className="truncate">{a.serial_number}{a.name ? ` (${a.name})` : ''}</span>
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
                        <span className="truncate block">{f.name}</span>
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
          {selectedFlightId && deletingFlightId !== selectedFlightId && (
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
        {deletingFlightId && (
          <div className="flex items-center gap-1">
            <span className="text-xs text-gray-500">确认删除?</span>
            <button
              type="button"
              onClick={() => handleDeleteFlight(deletingFlightId)}
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

        <select
          value={refTable}
          onChange={(e) => setRefTable(e.target.value)}
          className="bg-white border border-gray-300 rounded px-2 py-1 text-xs text-gray-500"
          title="选择时间基准数据源"
        >
          <option value="gps">基准:GPS</option>
          <option value="drone_state">基准:飞控</option>
          <option value="pos">基准:位置</option>
        </select>
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
          <span className="text-gray-500">最大高度: <strong className="text-gray-800">{stats.max_altitude}m</strong></span>
          <span className="text-gray-500">最大速度: <strong className="text-gray-800">{stats.max_speed}m/s</strong></span>
          <span className="text-gray-500">平均转速: <strong className="text-gray-800">{stats.avg_rpm}RPM</strong></span>
          <span className="text-gray-500">最高转速: <strong className="text-gray-800">{stats.max_rpm}RPM</strong></span>
          <span className="text-gray-500">油耗: <strong className="text-gray-800">{stats.fuel_start}→{stats.fuel_end}L</strong></span>
          <span className="text-gray-500">电量: <strong className="text-gray-800">{stats.battery_start}→{stats.battery_end}%</strong></span>
          <span className="text-gray-500">告警: <strong className="text-amber-500">{stats.alert_count}</strong></span>
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
                        <span>{group.label}</span>
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
                          const showInput = isEditing || (isHovered && !hasScale) || hasScale;
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
                            {showBadge && (
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
                            )}

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
              <ChartDebugBadge
                active={active}
                chartRef={chartRef}
                chartInst={chartInst}
                aligned={aligned}
                selectedColumns={selectedColumns}
              />
            </>
          )}

          {/* Map Tab */}
          {viewMode === 'map' && aligned && (
            <div className="flex-1 flex flex-col min-h-0 relative">
              <TrajectoryMap aligned={aligned} alerts={alerts} />
              {showMapLegend && (
                <div className="absolute bottom-4 left-4 bg-white/95 border border-gray-200 rounded-lg px-3 py-2 text-xs z-10 shadow-sm">
                  <div className="text-gray-500 mb-1 font-medium">高度 (m)</div>
                  <div className="flex items-center gap-2">
                    <span className="w-16 h-2 rounded" style={{ background: 'linear-gradient(90deg, #22c55e, #eab308, #ef4444)' }} />
                  </div>
                  <div className="flex justify-between text-gray-400 text-[10px] mt-0.5">
                    <span>低</span><span>高</span>
                  </div>
                  <div className="flex items-center gap-2 mt-2">
                    <span className="w-2 h-2 rounded-full bg-green-500 inline-block" />
                    <span className="text-gray-400">起点</span>
                    <span className="w-2 h-2 rounded-full bg-red-500 inline-block ml-2" />
                    <span className="text-gray-400">终点</span>
                    <span className="w-2 h-2 rounded-full bg-amber-500 inline-block ml-2" />
                    <span className="text-gray-400">告警</span>
                  </div>
                  <button onClick={() => setShowMapLegend(false)} className="text-gray-400 hover:text-gray-600 mt-1 text-[10px]">
                    隐藏图例
                  </button>
                </div>
              )}
            </div>
          )}

          {/* Alerts Tab */}
          {viewMode === 'alerts' && (
            <div className="flex-1 overflow-y-auto p-4 space-y-4">
              <div className="flex gap-6 text-sm mb-4">
                <div className="bg-gray-50 rounded-lg px-4 py-2 border border-gray-200">
                  <span className="text-gray-500">告警总数：</span>
                  <strong className="text-amber-500">{alerts.length}</strong>
                </div>
                <div className="bg-gray-50 rounded-lg px-4 py-2 border border-gray-200">
                  <span className="text-gray-500">告警类型：</span>
                  <strong className="text-gray-800">{Object.keys(alertGroups).length}</strong>
                </div>
              </div>
              {Object.entries(alertGroups)
                .sort(([, a], [, b]) => b.length - a.length)
                .map(([desc, items]) => (
                  <div key={desc} className="bg-white border border-gray-200 rounded-lg overflow-hidden">
                    <div className="px-4 py-2 bg-gray-50 flex items-center justify-between">
                      <div>
                        <span className="text-amber-600 text-sm font-medium">{desc}</span>
                        <span className="text-gray-400 text-xs ml-2">({items.length} 次)</span>
                      </div>
                      <span className="text-gray-400 text-xs">
                        {items[0]?.time_str} → {items[items.length - 1]?.time_str}
                      </span>
                    </div>
                    <div className="px-4 py-2 text-xs text-gray-500 border-t border-gray-100">
                      💡 {explainAlert(desc)}
                    </div>
                    <div className="px-4 py-1 text-xs text-gray-400 border-t border-gray-100 grid grid-cols-5 gap-2">
                      {items.length <= 10 ? (
                        items.map((a, i) => (
                          <span key={i} className="font-mono">{a.time_str}</span>
                        ))
                      ) : (
                        <>
                          {items.slice(0, 5).map((a, i) => (
                            <span key={i} className="font-mono">{a.time_str}</span>
                          ))}
                          <span className="text-gray-400 col-span-5 text-center">... 省略 {items.length - 10} 次 ...</span>
                          {items.slice(-5).map((a, i) => (
                            <span key={i} className="font-mono">{a.time_str}</span>
                          ))}
                        </>
                      )}
                    </div>
                  </div>
                ))}
            </div>
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

function CorrelationHeatmap({ data }: { data: { labels: string[]; matrix: number[][] } }) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!ref.current) return;
    const chart = echarts.init(ref.current);
    chart.setOption({
      tooltip: { formatter: (p: any) => `${p.name}: <strong>${p.value?.[2]?.toFixed(3)}</strong>` },
      grid: { left: 120, right: 60, top: 20, bottom: 80 },
      xAxis: {
        type: 'category', data: data.labels,
        axisLabel: { color: '#6b7280', fontSize: 10, rotate: 45 },
      },
      yAxis: {
        type: 'category', data: data.labels,
        axisLabel: { color: '#6b7280', fontSize: 10 },
      },
      visualMap: {
        min: -1, max: 1,
        inRange: { color: ['#3b82f6', '#f9fafb', '#ef4444'] },
        textStyle: { color: '#6b7280' },
        orient: 'horizontal', bottom: 10,
      },
      series: [{
        type: 'heatmap',
        data: data.matrix.flatMap((row, i) =>
          row.map((v, j) => [j, i, v])
        ),
        label: { show: false },
      }],
    });
    return () => chart.dispose();
  }, [data]);
  return <div ref={ref} className="w-full h-full" />;
}

function AnomalyChart({ data }: { data: any }) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!ref.current || !data.times || !data.values) return;
    const chart = echarts.init(ref.current);
    const anomalyPoints = data.anomaly_indices && data.values
      ? data.anomaly_indices.map((i: number) => [i, data.values[i]])
      : [];
    chart.setOption({
      tooltip: { trigger: 'axis' },
      legend: { data: ['原始值', '上界', '下界', '异常点'], textStyle: { color: '#6b7280' } },
      xAxis: { type: 'category', data: data.times, axisLabel: { show: false } },
      yAxis: {
        type: 'value', name: data.unit,
        nameTextStyle: { color: '#6b7280' },
        axisLabel: { color: '#9ca3af' },
        splitLine: { lineStyle: { color: '#f3f4f6' } },
      },
      dataZoom: [{ type: 'slider' }, { type: 'inside' }],
      series: [
        { name: '原始值', type: 'line', data: data.values, smooth: true, showSymbol: false, lineStyle: { width: 1, color: '#2563eb' } },
        { name: '上界', type: 'line', data: data.upper_bound, lineStyle: { type: 'dashed', color: '#f59e0b', width: 1 }, showSymbol: false },
        { name: '下界', type: 'line', data: data.lower_bound, lineStyle: { type: 'dashed', color: '#f59e0b', width: 1 }, showSymbol: false },
        {
          name: '异常点', type: 'scatter',
          data: anomalyPoints,
          symbolSize: 6, itemStyle: { color: '#ef4444' },
        },
      ],
    });
    return () => chart.dispose();
  }, [data]);
  return <div ref={ref} className="w-full h-full" />;
}

// ═══════════════════════════════════════════════════════════
// Trajectory Map
// ═══════════════════════════════════════════════════════════

function TrajectoryMap({ aligned, alerts }: { aligned: AlignedData; alerts: AlertItem[] }) {
  const containerRef = useRef<HTMLDivElement>(null);
  const mapInst = useRef<any>(null);

  useEffect(() => {
    if (!containerRef.current || !aligned) return;

    const latSeries = aligned.series['pos.lat'] || aligned.series['gps.nava_lat'];
    const lngSeries = aligned.series['pos.lng'] || aligned.series['gps.nava_lng'];
    const altSeries = aligned.series['gps.nava_alt'] || aligned.series['pos.rel_alt'];

    if (!latSeries || !lngSeries) return;

    const points: [number, number][] = [];
    const alts: number[] = [];
    for (let i = 0; i < latSeries.values.length; i++) {
      if (latSeries.values[i] != null && lngSeries.values[i] != null) {
        points.push([latSeries.values[i]!, lngSeries.values[i]!]);
        alts.push(altSeries?.values[i] ?? 0);
      }
    }
    if (points.length < 2) return;

    if (mapInst.current) { mapInst.current.remove(); mapInst.current = null; }

    const L = (window as any).L;
    if (!L) {
      renderCanvasMapFull(containerRef.current, points, alts, aligned, alerts);
      return;
    }

    const map = L.map(containerRef.current).setView([points[0][0], points[0][1]], 14);
    L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
      maxZoom: 19,
      attribution: '&copy; OpenStreetMap',
    }).addTo(map);

    const maxAlt = Math.max(...alts.filter((a) => a != null));
    const minAlt = Math.min(...alts.filter((a) => a != null));
    const altRange = maxAlt - minAlt || 1;

    const getColor = (alt: number) => {
      const ratio = (alt - minAlt) / altRange;
      if (ratio < 0.5) {
        const r = Math.round(510 * ratio);
        return `rgb(${r},255,50)`;
      } else {
        const g = Math.round(255 * (2 - 2 * ratio));
        return `rgb(255,${g},50)`;
      }
    };

    for (let i = 0; i < points.length - 1; i++) {
      L.polyline([points[i], points[i + 1]], {
        color: getColor(alts[i]),
        weight: 3,
        opacity: 0.8,
      }).addTo(map);
    }

    L.circleMarker(points[0], { radius: 6, color: '#22c55e', fillColor: '#22c55e', fillOpacity: 1 })
      .addTo(map).bindPopup('起点');
    L.circleMarker(points[points.length - 1], { radius: 6, color: '#ef4444', fillColor: '#ef4444', fillOpacity: 1 })
      .addTo(map).bindPopup('终点');

    alerts.forEach((a) => {
      const idx = aligned.times.indexOf(a.time_str);
      if (idx >= 0 && idx < points.length) {
        L.circleMarker(points[idx], { radius: 4, color: '#f59e0b', fillColor: '#f59e0b', fillOpacity: 0.8 })
          .addTo(map).bindPopup(a.desc || '告警');
      }
    });

    mapInst.current = map;
    return () => {
      if (mapInst.current) { mapInst.current.remove(); mapInst.current = null; }
    };
  }, [aligned, alerts]);

  return <div ref={containerRef} className="w-full h-full bg-gray-100" />;
}

function renderCanvasMapFull(
  container: HTMLElement,
  points: [number, number][],
  alts: number[],
  aligned: AlignedData,
  alerts: AlertItem[],
) {
  const canvas = document.createElement('canvas');
  const W = container.clientWidth;
  const H = container.clientHeight;
  canvas.width = W;
  canvas.height = H;
  canvas.style.width = '100%';
  canvas.style.height = '100%';
  container.innerHTML = '';
  container.appendChild(canvas);
  const ctx = canvas.getContext('2d');
  if (!ctx) return;

  const lats = points.map((p) => p[0]);
  const lngs = points.map((p) => p[1]);
  const minLat = Math.min(...lats), maxLat = Math.max(...lats);
  const minLng = Math.min(...lngs), maxLng = Math.max(...lngs);
  const latR = maxLat - minLat || 0.001;
  const lngR = maxLng - minLng || 0.001;

  const margin = 40;
  const sx = (lng: number) => margin + ((lng - minLng) / lngR) * (W - margin * 2);
  const sy = (lat: number) => H - margin - ((lat - minLat) / latR) * (H - margin * 2);

  const maxAlt = Math.max(...alts.filter((a) => a != null), 1);
  const minAlt = Math.min(...alts.filter((a) => a != null), 0);
  const altR = maxAlt - minAlt || 1;

  ctx.fillStyle = '#f3f4f6';
  ctx.fillRect(0, 0, W, H);

  // Grid
  ctx.strokeStyle = '#e5e7eb';
  ctx.lineWidth = 0.5;
  for (let i = 0; i < 10; i++) {
    const x = margin + (i / 9) * (W - margin * 2);
    const y = margin + (i / 9) * (H - margin * 2);
    ctx.beginPath(); ctx.moveTo(x, margin); ctx.lineTo(x, H - margin); ctx.stroke();
    ctx.beginPath(); ctx.moveTo(margin, y); ctx.lineTo(W - margin, y); ctx.stroke();
  }

  // Trajectory
  for (let i = 0; i < points.length - 1; i++) {
    const ratio = (alts[i] - minAlt) / altR;
    const r = ratio < 0.5 ? Math.round(510 * ratio) : 255;
    const g = ratio < 0.5 ? 255 : Math.round(255 * (2 - 2 * ratio));
    ctx.strokeStyle = `rgb(${r},${g},50)`;
    ctx.lineWidth = 2;
    ctx.beginPath();
    ctx.moveTo(sx(points[i][1]), sy(points[i][0]));
    ctx.lineTo(sx(points[i + 1][1]), sy(points[i + 1][0]));
    ctx.stroke();
  }

  // Start / End
  ctx.fillStyle = '#22c55e';
  ctx.beginPath(); ctx.arc(sx(points[0][1]), sy(points[0][0]), 5, 0, Math.PI * 2); ctx.fill();
  ctx.strokeStyle = '#fff'; ctx.lineWidth = 1.5; ctx.stroke();
  ctx.fillStyle = '#ef4444';
  ctx.beginPath(); ctx.arc(sx(points[points.length - 1][1]), sy(points[points.length - 1][0]), 5, 0, Math.PI * 2); ctx.fill();
  ctx.strokeStyle = '#fff'; ctx.stroke();

  // Alerts
  alerts.forEach((a) => {
    const idx = aligned.times.indexOf(a.time_str);
    if (idx >= 0 && idx < points.length) {
      ctx.fillStyle = '#f59e0b';
      ctx.beginPath(); ctx.arc(sx(points[idx][1]), sy(points[idx][0]), 3, 0, Math.PI * 2); ctx.fill();
    }
  });

  // Legend
  const lx = W - 120, ly = margin;
  ctx.fillStyle = '#ffffffcc';
  ctx.fillRect(lx - 5, ly - 5, 100, 65);
  ctx.strokeStyle = '#e5e7eb';
  ctx.lineWidth = 1;
  ctx.strokeRect(lx - 5, ly - 5, 100, 65);
  const grad = ctx.createLinearGradient(lx, 0, lx + 60, 0);
  grad.addColorStop(0, '#22c55e');
  grad.addColorStop(0.5, '#eab308');
  grad.addColorStop(1, '#ef4444');
  ctx.fillStyle = grad;
  ctx.fillRect(lx, ly, 60, 8);
  ctx.fillStyle = '#6b7280';
  ctx.font = '10px sans-serif';
  ctx.fillText('高', lx + 64, ly + 8);
  ctx.fillText('低', lx - 12, ly + 8);
}
