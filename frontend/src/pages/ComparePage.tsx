import { useState, useEffect, useRef } from 'react';
import { ChevronDown, ChevronRight } from 'lucide-react';
import * as echarts from 'echarts';
import { getColumns, getCompare, listAircraft, type Flight, type ColumnGroup, type AircraftModel, type Aircraft } from '../api';

interface Props {
  flights: Flight[];
  models: AircraftModel[];
  selectedModelId: number | null;
  onSelectModel: (id: number) => void;
  aircraft: Aircraft[];
  selectedAircraftId: number | null;
  onSelectAircraft: (id: number) => void;
}

export default function ComparePage({
  flights, models, selectedModelId, onSelectModel,
  aircraft, selectedAircraftId, onSelectAircraft,
}: Props) {
  const [selectedFlights, setSelectedFlights] = useState<number[]>([]);
  const [selectedColumn, setSelectedColumn] = useState('');
  const [columnGroups, setColumnGroups] = useState<ColumnGroup[]>([]);
  const [allColumns, setAllColumns] = useState<{ key: string; label: string; unit: string }[]>([]);
  const [flightSearch, setFlightSearch] = useState('');
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  const chartRef = useRef<HTMLDivElement>(null);
  const chartInstance = useRef<echarts.ECharts | null>(null);
  const yZoomRef = useRef({ start: 0, end: 100 });

  // ─── Tree selector state ─────────────────────────────────
  const [treeOpen, setTreeOpen] = useState(false);
  const [treeModelId, setTreeModelId] = useState<number | null>(null);
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
    try {
      const data = await listAircraft(modelId);
      setTreeAircraftList(data.aircraft);
    } catch { setTreeAircraftList([]); }
  };

  // Click (not hover) to commit selection — hover would close the popover
  // before the user can click, and would also leak the hovered aircraft id
  // into App-level state, which can change selectedFlightId via the
  // aircraft→flight auto-pick effect.
  const selectTreeAircraft = (acId: number) => {
    onSelectModel(treeModelId!);
    onSelectAircraft(acId);
    setTreeOpen(false);
  };

  // Load all available columns from first flight
  useEffect(() => {
    if (flights.length > 0) {
      getColumns(flights[0].id).then((d) => {
        setColumnGroups(d.columns);
        const all: { key: string; label: string; unit: string }[] = [];
        d.columns.forEach((g) => g.columns.forEach((c) => all.push(c)));
        setAllColumns(all);
      });
    }
  }, [flights]);

  const toggleFlight = (id: number) => {
    setSelectedFlights((prev) =>
      prev.includes(id) ? prev.filter((f) => f !== id) : [...prev, id]
    );
  };

  const handleCompare = async () => {
    if (selectedFlights.length < 2 || !selectedColumn) return;
    try {
      const data = await getCompare(selectedFlights, selectedColumn);
      renderChart(data.series);
    } catch (err) {
      console.error('Failed to compare flights', err);
      setErrorMsg('对比分析失败，请重试');
    }
  };

  const renderChart = (series: { name: string; times_sec: number[]; values: number[]; label: string; unit: string }[]) => {
    if (!chartRef.current) return;
    if (chartInstance.current) { chartInstance.current.dispose(); }
    chartInstance.current = echarts.init(chartRef.current);

    // Double-click to zoom in centered on click position (X+Y)
    // Use zrender-level event to avoid ECharts dataZoom-inside interception
    const zr = chartInstance.current.getZr();
    zr.on('dblclick', (e: any) => {
      const ZOOM = 2;
      const MIN_RANGE = 2;
      const opt = chartInstance.current?.getOption();
      const dzList = (opt?.dataZoom as any[]) || [];
      const xSlider = dzList.find((d: any) => d.type === 'slider' && d.yAxisIndex === undefined);
      const ySlider = dzList.find((d: any) => d.type === 'slider' && (d.yAxisIndex !== undefined));
      const xStart: number = xSlider?.start ?? 0;
      const xEnd: number = xSlider?.end ?? 100;
      const yStart: number = ySlider?.start ?? 0;
      const yEnd: number = ySlider?.end ?? 100;

      // Get grid pixel bounds to map click position → zoom center
      const gridModel = (chartInstance.current as any)?.getModel().getComponent('grid', 0);
      const rect = (gridModel as any)?.coordinateSystem?.getRect?.();
      const fx = rect ? Math.max(0, Math.min(1, (e.offsetX - rect.x) / rect.width)) : 0.5;
      const fy = rect ? 1 - Math.max(0, Math.min(1, (e.offsetY - rect.y) / rect.height)) : 0.5;
      const xCenter = xStart + fx * (xEnd - xStart);
      const yCenter = yStart + fy * (yEnd - yStart);

      const xRange = xEnd - xStart;
      if (xRange > MIN_RANGE) {
        const newXRange = xRange / ZOOM;
        const newXStart = Math.max(0, xCenter - newXRange / 2);
        const newXEnd = Math.min(100, xCenter + newXRange / 2);
        chartInstance.current?.dispatchAction({
          type: 'dataZoom',
          dataZoomIndex: 0,
          start: newXStart,
          end: newXEnd,
        });
      }

      const yRange = yEnd - yStart;
      if (yRange > MIN_RANGE) {
        const newYRange = yRange / ZOOM;
        const newYStart = Math.max(0, yCenter - newYRange / 2);
        const newYEnd = Math.min(100, yCenter + newYRange / 2);
        yZoomRef.current = { start: newYStart, end: newYEnd };
        chartInstance.current?.dispatchAction({
          type: 'dataZoom',
          dataZoomId: 'ySlider',
          start: newYStart,
          end: newYEnd,
        });
      }
    });

    const colors = ['#2563eb', '#dc2626', '#16a34a', '#ca8a04', '#7c3aed', '#0891b2'];

    const option: echarts.EChartsOption = {
      color: colors,
      tooltip: {
        trigger: 'axis',
        backgroundColor: '#fff',
        borderColor: '#e5e7eb',
        textStyle: { color: '#374151' },
      },
      legend: { data: series.map((s) => s.name), top: 0, textStyle: { color: '#6b7280' } },
      grid: { left: 60, right: 64, top: 40, bottom: 50 },
      xAxis: {
        type: 'value',
        name: '时间 (秒)',
        nameTextStyle: { color: '#6b7280' },
        axisLabel: { color: '#9ca3af' },
        splitLine: { lineStyle: { color: '#f3f4f6' } },
        // Snap the axis to actual data extents so the curve reaches the
        // right edge (default 'value' axis rounds up to a nice number,
        // leaving empty space on the right).
        min: 'dataMin',
        max: 'dataMax',
      },
      yAxis: {
        type: 'value',
        name: series[0]?.unit || '',
        nameTextStyle: { color: '#6b7280' },
        axisLabel: { color: '#9ca3af' },
        splitLine: { lineStyle: { color: '#f3f4f6' } },
      },
      dataZoom: [
        { type: 'slider', start: 0, end: 100 },
        { type: 'inside', xAxisIndex: 0 },
        { type: 'inside', yAxisIndex: 0, zoomOnMouseWheel: 'ctrl', id: 'yInside' },
        { type: 'slider', yAxisIndex: 0, start: 0, end: 100, right: 2, width: 18,
          backgroundColor: 'rgba(249,250,251,0.55)', id: 'ySlider' },
      ],
      series: series.map((s) => ({
        name: s.name,
        type: 'line',
        data: s.times_sec.map((t, i) => [t, s.values[i]]),
        smooth: true,
        showSymbol: false,
      })),
    };
    chartInstance.current.setOption(option, true);
  };

  const selectedColumnObj = allColumns.find((c) => c.key === selectedColumn);

  const filteredFlights = flights.filter((f) => {
    // Filter by selected aircraft or model
    if (selectedAircraftId) {
      if (f.aircraft_id !== selectedAircraftId) return false;
    } else if (selectedModelId) {
      if (f.model_id !== selectedModelId) return false;
    }
    // Text search
    if (!flightSearch.trim()) return true;
    const s = flightSearch.toLowerCase();
    return f.name.toLowerCase().includes(s) || (f.aircraft_name || f.drone_id || '').toLowerCase().includes(s);
  });

  // Search-matched flight IDs for upward tree filtering
  const searchMatchedIds = (() => {
    if (!flightSearch.trim()) return null;
    const s = flightSearch.toLowerCase();
    return new Set(
      flights.filter(f =>
        f.name.toLowerCase().includes(s) || (f.aircraft_name || f.drone_id || '').toLowerCase().includes(s)
      ).map(f => f.id)
    );
  })();

  const visibleModels = searchMatchedIds
    ? models.filter(m => flights.some(f => f.model_id === m.id && searchMatchedIds.has(f.id)))
    : models;

  const visibleTreeAircraft = searchMatchedIds
    ? treeAircraftList.filter(a => flights.some(f => f.aircraft_id === a.id && searchMatchedIds.has(f.id)))
    : treeAircraftList;

  return (
    <div className="h-full flex flex-col p-6">
      <h2 className="text-lg font-semibold text-gray-900 mb-4">多飞行对比</h2>

      {/* Error message banner */}
      {errorMsg && (
        <div className="flex items-center gap-2 px-4 py-2 mb-4 bg-red-50 border border-red-200 rounded text-red-700 text-xs">
          <span>{errorMsg}</span>
          <button
            onClick={() => setErrorMsg(null)}
            className="ml-auto text-red-400 hover:text-red-600 font-bold"
          >
            ×
          </button>
        </div>
      )}

      {/* Tree selector: Model → Aircraft */}
      <div className="flex items-center gap-3 mb-3 relative" ref={treeRef}>
        <button
          onClick={() => setTreeOpen(!treeOpen)}
          className="flex items-center gap-1 bg-white border border-gray-300 rounded-lg pl-3 pr-2 py-1.5 text-sm hover:border-blue-400 focus:outline-none focus:border-blue-500 focus:ring-1 focus:ring-blue-500 min-w-[180px] max-w-[360px]"
        >
          {selectedModelId && selectedAircraftId ? (
            <span className="text-gray-700 truncate">
              {(() => {
                const m = models.find(mo => mo.id === selectedModelId);
                const a = aircraft.find(ac => ac.id === selectedAircraftId);
                if (m && a) return `${m.name} / ${a.name}`;
                return '选择机型/飞机...';
              })()}
            </span>
          ) : (
            <span className="text-gray-400">选择机型/飞机...</span>
          )}
          <ChevronDown className={`w-4 h-4 text-gray-400 ml-auto shrink-0 transition-transform ${treeOpen ? 'rotate-180' : ''}`} />
        </button>

        {/* Tree popover */}
        {treeOpen && (
          <div className="absolute top-full left-0 mt-1 z-50 flex bg-white border border-gray-200 rounded-lg shadow-lg max-h-[320px]">
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

            {/* Column 2: Aircraft */}
            {treeModelId && (
              <div className="w-48 overflow-y-auto py-1">
                <div className="px-3 py-1.5 text-xs text-gray-400 font-medium sticky top-0 bg-white">飞机</div>
                {visibleTreeAircraft.length === 0 ? (
                  <div className="px-3 py-2 text-xs text-gray-400">无匹配飞机</div>
                ) : (
                  visibleTreeAircraft.map((a) => (
                    <button
                      key={a.id}
                      onClick={() => selectTreeAircraft(a.id)}
                      className={`w-full text-left px-3 py-1.5 text-sm ${
                        a.id === selectedAircraftId
                          ? 'bg-blue-50 text-blue-700'
                          : 'text-gray-700 hover:bg-gray-50'
                      }`}
                    >
                      <span className="truncate block">{a.name}</span>
                    </button>
                  ))
                )}
              </div>
            )}
          </div>
        )}
      </div>

      {/* Flight toggle buttons */}
      <div className="flex items-center gap-2 mb-3">
        <input
          type="text"
          value={flightSearch}
          onChange={(e) => setFlightSearch(e.target.value)}
          placeholder="搜索架次..."
          className="bg-white border border-gray-300 rounded-lg px-3 py-1.5 text-xs text-gray-700 placeholder-gray-400 focus:outline-none focus:border-blue-500 w-44"
        />
        <span className="text-xs text-gray-400">
          {selectedFlights.length}/{filteredFlights.length} 已选
        </span>
      </div>
      <div className="flex flex-wrap gap-2 mb-4">
        {filteredFlights.length === 0 ? (
          <span className="text-xs text-gray-400">无匹配结果</span>
        ) : (
          filteredFlights.map((f) => (
          <button
            key={f.id}
            onClick={() => toggleFlight(f.id)}
            className={`px-3 py-1.5 rounded-md text-xs font-medium transition-colors ${
              selectedFlights.includes(f.id)
                ? 'bg-blue-600 text-white'
                : 'bg-white border border-gray-300 text-gray-500 hover:bg-gray-100'
            }`}
          >
            {f.aircraft_name || f.drone_id} - {f.name}
          </button>
        )))}
      </div>

      {/* Column selector */}
      <div className="flex items-center gap-4 mb-4">
        <select
          value={selectedColumn}
          onChange={(e) => setSelectedColumn(e.target.value)}
          className="bg-white border border-gray-300 rounded-lg px-4 py-2 text-sm text-gray-800 focus:outline-none focus:border-blue-500"
        >
          <option value="">选择对比指标...</option>
          {columnGroups.map((g) => (
            <optgroup key={g.table} label={g.label}>
              {g.columns.map((c) => (
                <option key={c.key} value={c.key}>
                  {c.label} {c.unit ? `(${c.unit})` : ''}
                </option>
              ))}
            </optgroup>
          ))}
        </select>
        <button
          onClick={handleCompare}
          disabled={selectedFlights.length < 2 || !selectedColumn}
          className="px-4 py-2 bg-blue-600 hover:bg-blue-500 disabled:opacity-40 rounded-lg text-sm font-medium text-white transition-colors"
        >
          对比
        </button>
        <button
          onClick={() => {
            yZoomRef.current = { start: 0, end: 100 };
            chartInstance.current?.dispatchAction({
              type: 'dataZoom',
              dataZoomIndex: 0,
              start: 0,
              end: 100,
            });
            chartInstance.current?.dispatchAction({
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
        {selectedColumnObj && (
          <span className="text-xs text-gray-400">
            {selectedColumnObj.label} {selectedColumnObj.unit && `(${selectedColumnObj.unit})`}
          </span>
        )}
      </div>

      {/* Chart */}
      <div className="flex-1 min-h-0">
        <div ref={chartRef} className="w-full h-full" />
      </div>
    </div>
  );
}
