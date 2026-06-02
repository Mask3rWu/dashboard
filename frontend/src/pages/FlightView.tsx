import { useState, useEffect, useRef } from 'react';
import * as echarts from 'echarts';
import {
  getFlight, getAlignedData, getAlerts, getStats, getCorrelation, getAnomaly,
  listPresets, createPreset, deletePreset,
  type Flight, type ColumnGroup,
  type AlignedData, type AlertItem, type FlightStats, type Preset,
} from '../api';

interface Props {
  flights: Flight[];
  selectedFlightId: number | null;
  onSelectFlight: (id: number) => void;
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

export default function FlightView({ flights, selectedFlightId, onSelectFlight }: Props) {
  // ─── State ─────────────────────────────────────────────
  const [columnGroups, setColumnGroups] = useState<ColumnGroup[]>([]);
  const [selectedColumns, setSelectedColumns] = useState<string[]>([]);
  const [aligned, setAligned] = useState<AlignedData | null>(null);
  const [alerts, setAlerts] = useState<AlertItem[]>([]);
  const [stats, setStats] = useState<FlightStats | null>(null);
  const [presets, setPresets] = useState<Preset[]>([]);
  const [normalize, setNormalize] = useState(false);
  const [refTable, setRefTable] = useState('gps_data');
  const [, setLoading] = useState(false);
  const [viewMode, setViewMode] = useState<ViewMode>('chart');
  const [anomalyCol, setAnomalyCol] = useState('');
  const [anomalyData, setAnomalyData] = useState<any>(null);
  const [corrData, setCorrData] = useState<any>(null);
  const [sidebarOpen, setSidebarOpen] = useState(true);
  const [collapsedGroups, setCollapsedGroups] = useState<Set<string>>(new Set());
  const [showMapLegend, setShowMapLegend] = useState(true);

  const chartRef = useRef<HTMLDivElement>(null);
  const chartInst = useRef<echarts.ECharts | null>(null);
  const presetNameRef = useRef<HTMLInputElement>(null);

  // ─── Load flight data ──────────────────────────────────
  useEffect(() => {
    if (!selectedFlightId) return;
    setLoading(true);
    Promise.all([
      getFlight(selectedFlightId),
      getAlerts(selectedFlightId),
      getStats(selectedFlightId),
      listPresets(),
    ]).then(([flightData, alertData, statsData, presetData]) => {
      setColumnGroups(flightData.columns);
      setAlerts(alertData.alerts);
      setStats(statsData);
      setPresets(presetData.presets);
      const defaults = [
        'pos_data.lat', 'pos_data.lng', 'gps_data.nava_alt',
        'engine_data.rpm', 'drone_state_data.battery_pct',
      ];
      const available = defaults.filter((d) =>
        flightData.columns.some((g) => g.columns.some((c) => c.key === d))
      );
      setSelectedColumns(available);
      setAligned(null);
      setCorrData(null);
      setAnomalyData(null);
    }).finally(() => setLoading(false));
  }, [selectedFlightId]);

  // ─── Fetch aligned data ────────────────────────────────
  useEffect(() => {
    if (!selectedFlightId || selectedColumns.length === 0) return;
    getAlignedData(selectedFlightId, selectedColumns, refTable).then(setAligned);
  }, [selectedFlightId, selectedColumns, refTable]);

  // ─── Chart ─────────────────────────────────────────────
  useEffect(() => {
    if (viewMode !== 'chart') {
      if (chartInst.current) { chartInst.current.dispose(); chartInst.current = null; }
      return;
    }
    if (!chartRef.current || !aligned) return;

    if (chartInst.current) { chartInst.current.dispose(); }
    chartInst.current = echarts.init(chartRef.current);

    const times = aligned.times;
    const seriesList = Object.entries(aligned.series);

    const getValues = (vals: (number | null)[]) => {
      if (!normalize) return vals;
      const nums = vals.filter((v) => v !== null) as number[];
      if (nums.length === 0) return vals;
      const min = Math.min(...nums);
      const max = Math.max(...nums);
      const range = max - min || 1;
      return vals.map((v) => (v !== null ? (v - min) / range : null));
    };

    const colors = ['#2563eb', '#dc2626', '#16a34a', '#ca8a04', '#7c3aed', '#0891b2', '#db2777', '#ea580c'];
    const isNorm = normalize;

    // Group series by semantic unit (e.g. ° → °_pos vs °_angle)
    type UnitGroup = { unit: string; items: [string, typeof aligned.series[string]][] };
    const unitMap = new Map<string, UnitGroup['items']>();
    seriesList.forEach(([key, s]) => {
      const raw = s.unit || '-';
      // Distinguish ° by semantics: lat/lng vs attitude angles
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

    // Assign a color per unit group
    const unitColor = (gi: number) => colors[gi % colors.length];

    // Build yAxis and yAxisIndex map: each unit group gets one axis
    const yAxes: echarts.EChartsOption['yAxis'] = [];
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

    // Dynamic grid margins
    const leftUnits = isNorm ? 0 : unitGroups.filter((_, i) => i % 2 === 0).length;
    const rightUnits = isNorm ? 0 : unitGroups.filter((_, i) => i % 2 === 1).length;
    const AXIS_WIDTH = 50;
    const leftPad = isNorm ? 60 : 80 + (leftUnits > 0 ? (leftUnits - 1) * AXIS_WIDTH : 0);
    const rightPad = isNorm ? 40 : 80 + (rightUnits > 0 ? (rightUnits - 1) * AXIS_WIDTH : 0);

    const option: echarts.EChartsOption = {
      color: seriesList.map((_, i) => unitColor(seriesYIndex[i])),
      tooltip: {
        trigger: 'axis',
        backgroundColor: '#fff',
        borderColor: '#e5e7eb',
        textStyle: { color: '#374151', fontSize: 12 },
        formatter: (params: any) => {
          if (!Array.isArray(params)) return '';
          const time = params[0]?.name || '';
          let html = `<div class="text-xs font-mono text-gray-500">${time}</div>`;
          params.forEach((p: any) => {
            if (p.value?.[1] != null) {
              html += `<div>${p.marker} ${p.seriesName}: <strong>${Number(p.value[1]).toFixed(2)}</strong></div>`;
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
      grid: { left: leftPad, right: rightPad, top: 40, bottom: 60 },
      xAxis: {
        type: 'category',
        data: times,
        axisLabel: { color: '#9ca3af', fontSize: 10, interval: Math.max(1, Math.floor(times.length / 20)) },
        axisLine: { lineStyle: { color: '#e5e7eb' } },
      },
      yAxis: yAxes,
      dataZoom: [
        { type: 'slider', start: 0, end: 100, height: 20, bottom: 10 },
        { type: 'inside' },
      ],
      series: seriesList.map(([, s], i) => {
        const values = getValues(s.values);
        const gi = seriesYIndex[i];
        return {
          name: s.label + (isNorm ? '' : s.unit ? ` (${s.unit})` : ''),
          type: 'line',
          yAxisIndex: seriesYIndex[i],
          data: times.map((t, j) => [t, values[j]]),
          smooth: true,
          showSymbol: false,
          lineStyle: { width: 1.5, color: unitColor(gi) },
        };
      }),
      ...(aligned.alerts.length > 0 ? {
        markLine: {
          silent: true,
          symbol: 'none',
          lineStyle: { type: 'dashed', color: '#ef4444', width: 1 },
          data: aligned.alerts
            .filter((_, idx) => idx % Math.max(1, Math.floor(aligned.alerts.length / 30)) === 0)
            .map((a) => ({ xAxis: a.time_str, label: { show: false } })),
        },
      } as any : {}),
    };

    chartInst.current.setOption(option, true);

    const handleResize = () => chartInst.current?.resize();
    window.addEventListener('resize', handleResize);
    return () => {
      window.removeEventListener('resize', handleResize);
      if (chartInst.current) { chartInst.current.dispose(); chartInst.current = null; }
    };
  }, [aligned, normalize, viewMode]);

  // Resize chart when sidebar toggles
  useEffect(() => {
    const timer = setTimeout(() => chartInst.current?.resize(), 100);
    return () => clearTimeout(timer);
  }, [sidebarOpen]);

  // ─── Column toggle ─────────────────────────────────────
  const toggleColumn = (key: string) => {
    setSelectedColumns((prev) =>
      prev.includes(key) ? prev.filter((k) => k !== key) : [...prev, key]
    );
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
    if (!name || selectedColumns.length === 0) return;
    await createPreset(name, selectedColumns);
    const data = await listPresets();
    setPresets(data.presets);
    if (presetNameRef.current) presetNameRef.current.value = '';
  };

  const loadPreset = (p: Preset) => setSelectedColumns(p.columns);
  const removePreset = async (id: number) => {
    await deletePreset(id);
    setPresets((prev) => prev.filter((p) => p.id !== id));
  };

  // ─── Analysis actions ──────────────────────────────────
  const loadCorrelation = async () => {
    if (!selectedFlightId || selectedColumns.length < 2) return;
    const data = await getCorrelation(selectedFlightId, selectedColumns);
    setCorrData(data);
    setViewMode('correlation');
  };

  const loadAnomaly = async () => {
    if (!selectedFlightId || !anomalyCol) return;
    const data = await getAnomaly(selectedFlightId, anomalyCol);
    setAnomalyData(data);
    setViewMode('anomaly');
  };

  // ─── Alert grouping ────────────────────────────────────
  const alertGroups = alerts.reduce((acc, a) => {
    const key = a.desc || '(无描述)';
    if (!acc[key]) acc[key] = [];
    acc[key].push(a);
    return acc;
  }, {} as Record<string, AlertItem[]>);

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
      <div className="flex items-center gap-4 px-4 py-2 border-b border-gray-200 bg-gray-50/80 shrink-0">
        <select
          value={selectedFlightId ?? ''}
          onChange={(e) => onSelectFlight(Number(e.target.value))}
          className="bg-white border border-gray-300 rounded-lg px-3 py-1.5 text-sm text-gray-800 focus:outline-none focus:border-blue-500 focus:ring-1 focus:ring-blue-500"
        >
          {flights.map((f) => (
            <option key={f.id} value={f.id}>
              UAV{f.drone_id} - {f.name} ({f.flight_date})
            </option>
          ))}
        </select>

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

        <select
          value={refTable}
          onChange={(e) => setRefTable(e.target.value)}
          className="bg-white border border-gray-300 rounded px-2 py-1 text-xs text-gray-500"
          title="选择时间基准数据源，其他数据按最近时间点对齐到此时间轴"
        >
          <option value="gps_data">基准:GPS</option>
          <option value="drone_state_data">基准:飞控</option>
          <option value="pos_data">基准:位置</option>
        </select>
      </div>

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
                        {group.columns.map((col) => (
                          <label
                            key={col.key}
                            className="flex items-center gap-1.5 px-1 py-0.5 rounded hover:bg-blue-50 cursor-pointer text-xs"
                          >
                            <input
                              type="checkbox"
                              checked={selectedColumns.includes(col.key)}
                              onChange={() => toggleColumn(col.key)}
                              className="rounded w-3 h-3 accent-blue-600"
                            />
                            <span className="text-gray-600 truncate flex-1">{col.label}</span>
                            {col.unit && <span className="text-gray-400 text-[10px]">{col.unit}</span>}
                          </label>
                        ))}
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          </aside>
        )}

        {/* ── Content Area ────────────────────────────── */}
        <div className="flex-1 flex flex-col min-w-0">
          {/* Chart Tab */}
          {viewMode === 'chart' && (
            <div ref={chartRef} className="flex-1 min-h-0" />
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
    if (!ref.current || !data.times) return;
    const chart = echarts.init(ref.current);
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
          data: data.anomaly_indices.map((i: number) => [i, data.values[i]]),
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

    const latSeries = aligned.series['pos_data.lat'] || aligned.series['gps_data.nava_lat'];
    const lngSeries = aligned.series['pos_data.lng'] || aligned.series['gps_data.nava_lng'];
    const altSeries = aligned.series['gps_data.nava_alt'] || aligned.series['pos_data.rel_alt'];

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
