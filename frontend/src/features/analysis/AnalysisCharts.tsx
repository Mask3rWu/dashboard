import { useEffect, useRef } from 'react';
import * as echarts from 'echarts';
import type { AnomalyData, CorrelationData } from '../../api/analysis';

interface HeatmapTooltipParam {
  name?: string;
  value?: [number, number, number];
}

export function CorrelationHeatmap({ data }: { data: CorrelationData }) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!ref.current) return;
    const chart = echarts.init(ref.current);
    chart.setOption({
      tooltip: { formatter: (param: HeatmapTooltipParam) => `${param.name}: <strong>${param.value?.[2]?.toFixed(3)}</strong>` },
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

export function AnomalyChart({ data }: { data: AnomalyData }) {
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
