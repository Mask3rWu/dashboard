import { useState, useEffect, useRef } from 'react';
import * as echarts from 'echarts';
import { getColumns, getCompare, type Flight, type ColumnGroup } from '../api';

interface Props {
  flights: Flight[];
}

export default function ComparePage({ flights }: Props) {
  const [selectedFlights, setSelectedFlights] = useState<number[]>([]);
  const [selectedColumn, setSelectedColumn] = useState('');
  const [columnGroups, setColumnGroups] = useState<ColumnGroup[]>([]);
  const [allColumns, setAllColumns] = useState<{ key: string; label: string; unit: string }[]>([]);
  const [flightSearch, setFlightSearch] = useState('');
  const chartRef = useRef<HTMLDivElement>(null);
  const chartInstance = useRef<echarts.ECharts | null>(null);

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
    const data = await getCompare(selectedFlights, selectedColumn);
    renderChart(data.series);
  };

  const renderChart = (series: { name: string; times_sec: number[]; values: number[]; label: string; unit: string }[]) => {
    if (!chartRef.current) return;
    if (chartInstance.current) { chartInstance.current.dispose(); }
    chartInstance.current = echarts.init(chartRef.current);

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
      grid: { left: 60, right: 40, top: 40, bottom: 50 },
      xAxis: {
        type: 'value',
        name: '时间 (秒)',
        nameTextStyle: { color: '#6b7280' },
        axisLabel: { color: '#9ca3af' },
        splitLine: { lineStyle: { color: '#f3f4f6' } },
      },
      yAxis: {
        type: 'value',
        name: series[0]?.unit || '',
        nameTextStyle: { color: '#6b7280' },
        axisLabel: { color: '#9ca3af' },
        splitLine: { lineStyle: { color: '#f3f4f6' } },
      },
      dataZoom: [{ type: 'slider', start: 0, end: 100 }],
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
    if (!flightSearch.trim()) return true;
    const s = flightSearch.toLowerCase();
    return f.name.toLowerCase().includes(s) || (f.aircraft_serial || f.drone_id || '').toLowerCase().includes(s);
  });

  return (
    <div className="h-full flex flex-col p-6">
      <h2 className="text-lg font-semibold text-gray-900 mb-4">多飞行对比</h2>

      {/* Flight selector */}
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
            UAV{f.aircraft_serial || f.drone_id} - {f.name}
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
