import * as echarts from 'echarts';
import type { AlignedData } from '../../api/analysis';

export function buildChartOption(
  aligned: AlignedData,
  normalize: boolean,
  scaleFactors: Record<string, number>,
): echarts.EChartsOption {
  const times = aligned.times || [];
  const allSeries = Object.entries(aligned.series || {});
  // Split: numeric series go to chart, text series are tooltip-only
  const numericSeries = allSeries.filter(([, s]) => s.is_numeric !== false);
  const textSeries = allSeries.filter(([, s]) => s.is_numeric === false);
  const seriesList = numericSeries; // chart uses only numeric
  const needsTextAnchor = seriesList.length === 0 && textSeries.length > 0;

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

  if (needsTextAnchor) {
    yAxes.push({
      type: 'value',
      min: 0,
      max: 1,
      axisLabel: { show: false },
      axisTick: { show: false },
      axisLine: { show: false },
      splitLine: { show: false },
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
          p.seriesName !== '__dz_indicator__' && p.seriesName !== '__filter_bg__' && p.seriesName !== '__text_anchor__');
        if (mainParams.length === 0 && textSeries.length === 0) return '';
        const anchorParam = params.find((p: any) => p.seriesName === '__text_anchor__');
        const timeIdx = mainParams[0]?.dataIndex ?? anchorParam?.dataIndex ?? -1;
        const time = mainParams[0]?.name || anchorParam?.name || (timeIdx >= 0 ? (times[timeIdx] || '') : '');
        let html = `<div class="text-xs font-mono text-gray-500">${time}</div>`;
        // Deduplicate by seriesName (ECharts may return the same series twice
        // when multiple yAxes share data — filter keeps only the first occurrence)
        const seenNames = new Set<string>();
        mainParams.forEach((p: any) => {
          if (!p.seriesName || seenNames.has(p.seriesName)) return;
          seenNames.add(p.seriesName);
          if (p.value?.[1] != null) {
            const sIdx = p.seriesIndex;
            const key = numericSeries[sIdx]?.[0] || '';
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
        if (timeIdx >= 0) {
          textSeries.forEach(([, s]) => {
            const textVal = s.text_values?.[timeIdx];
            if (textVal != null && textVal !== '') {
              html += `<div><span style="display:inline-block;width:8px;height:8px;border-radius:2px;background:#9ca3af;margin-right:4px"></span> ${s.label}: <strong>${textVal}</strong></div>`;
            }
          });
        }
        return html;
      },
    },
    legend: {
      type: 'scroll', top: 0,
      textStyle: { color: '#6b7280', fontSize: 11 },
      data: numericSeries.map(([, s]) => isNorm ? s.label : `${s.label} (${s.unit || '-'})`),
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
        };
      }),
      ...(needsTextAnchor ? [{
        name: '__text_anchor__',
        type: 'line' as const,
        yAxisIndex: 0,
        xAxisIndex: 0,
        data: times.map((t) => [t, 0]),
        showSymbol: false,
        lineStyle: { opacity: 0 },
        itemStyle: { opacity: 0 },
        emphasis: { disabled: true },
        z: -2,
      }] : []),
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
