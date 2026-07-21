import * as echarts from 'echarts';
import type { AlignedData } from '../../api/analysis';

const LINE_TYPES = ['solid', 'dashed', 'dotted'] as const;

type NumericValues = (number | null)[];

function hashString(value: string): number {
  let hash = 2166136261;
  for (let i = 0; i < value.length; i += 1) {
    hash ^= value.charCodeAt(i);
    hash = Math.imul(hash, 16777619);
  }
  return hash >>> 0;
}

// A field keeps its color when other fields are added or removed. Using a
// fractional hue gives substantially more distinct colors than a short list
// that wraps after a few series.
function stableColor(identity: string): string {
  const hash = hashString(identity);
  const hue = (hash % 36000) / 100;
  const saturation = 66 + ((hash >>> 9) % 15);
  const lightness = 35 + ((hash >>> 17) % 11);
  const normalizedSaturation = saturation / 100;
  const normalizedLightness = lightness / 100;
  const chroma = (1 - Math.abs(2 * normalizedLightness - 1)) * normalizedSaturation;
  const hueSegment = hue / 60;
  const second = chroma * (1 - Math.abs((hueSegment % 2) - 1));
  const match = normalizedLightness - chroma / 2;
  const [red, green, blue] = hueSegment < 1 ? [chroma, second, 0]
    : hueSegment < 2 ? [second, chroma, 0]
      : hueSegment < 3 ? [0, chroma, second]
        : hueSegment < 4 ? [0, second, chroma]
          : hueSegment < 5 ? [second, 0, chroma]
            : [chroma, 0, second];
  const toHex = (channel: number) => Math.round((channel + match) * 255).toString(16).padStart(2, '0');
  return `#${toHex(red)}${toHex(green)}${toHex(blue)}`;
}

function escapeHtml(value: unknown): string {
  return String(value)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#039;');
}

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

  const getValues = (vals: NumericValues, key: string): NumericValues => {
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

  const plottedValues = new Map<string, NumericValues>(
    seriesList.map(([key, series]) => [key, getValues(series.values, key)]),
  );

  const getNiceExtent = (items: UnitGroup['items']): [number, number] => {
    let dataMin = Infinity;
    let dataMax = -Infinity;
    items.forEach(([key]) => {
      plottedValues.get(key)?.forEach((value) => {
        if (value === null || !Number.isFinite(value)) return;
        dataMin = Math.min(dataMin, value);
        dataMax = Math.max(dataMax, value);
      });
    });
    if (!Number.isFinite(dataMin) || !Number.isFinite(dataMax)) return [0, 1];

    // ECharts dataZoom maps percentages against the raw data extent, while an
    // automatic value axis is displayed with a nice-expanded extent. Freezing
    // that nice extent makes every Y axis use the same normalized viewport.
    const scale = echarts.helper.createScale(
      [Math.min(0, dataMin), Math.max(0, dataMax)],
      { type: 'value' },
    );
    return scale.getExtent() as [number, number];
  };

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

  const seriesColor = (key: string) => stableColor(`series:${key}`);
  const unitColor = (unit: string) => stableColor(`unit:${unit}`);

  const yAxes: echarts.YAXisComponentOption[] = [];
  const keyToGroup = new Map<string, number>();

  if (isNorm) {
    yAxes.push({
      type: 'value',
      name: '归一化 (0~1)',
      min: 0,
      max: 1,
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
      const color = unitColor(g.unit);
      const [axisMin, axisMax] = getNiceExtent(g.items);
      yAxes.push({
        type: 'value',
        name: g.unit,
        min: axisMin,
        max: axisMax,
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

  const mainYAxes: echarts.YAXisComponentOption[] = yAxes.map((a) => ({ ...a, gridIndex: 0 }));
  const yAxisArr: echarts.YAXisComponentOption[] = hasFilter ? [
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
    color: seriesList.map(([key]) => seriesColor(key)),
    tooltip: {
      trigger: 'axis',
      renderMode: 'html',
      confine: true,
      enterable: true,
      className: 'flight-chart-tooltip',
      backgroundColor: 'rgba(255,255,255,0.84)',
      borderColor: '#e5e7eb',
      textStyle: { color: '#374151', fontSize: 12 },
      extraCssText: 'box-sizing:border-box;max-width:420px;max-height:min(480px,calc(100vh - 32px));overflow-y:auto;overflow-x:hidden;padding:8px 10px;line-height:18px;backdrop-filter:blur(2px);',
      formatter: (params: echarts.TooltipComponentFormatterCallbackParams) => {
        if (!Array.isArray(params)) return '';
        const tooltipItems = params as echarts.DefaultLabelFormatterCallbackParams[];
        const mainParams = tooltipItems.filter((p) =>
          p.seriesName !== '__dz_indicator__' && p.seriesName !== '__filter_bg__' && p.seriesName !== '__text_anchor__');
        if (mainParams.length === 0 && textSeries.length === 0) return '';
        const anchorParam = tooltipItems.find((p) => p.seriesName === '__text_anchor__');
        const timeIdx = mainParams[0]?.dataIndex ?? anchorParam?.dataIndex ?? -1;
        const time = mainParams[0]?.name || anchorParam?.name || (timeIdx >= 0 ? (times[timeIdx] || '') : '');
        let html = `<div style="position:sticky;top:-8px;z-index:1;margin:-8px -10px 5px;padding:8px 10px 5px;background:rgba(255,255,255,0.92);border-bottom:1px solid #f3f4f6;color:#6b7280;font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:11px">${escapeHtml(time)}</div>`;
        // Deduplicate by seriesName (ECharts may return the same series twice
        // when multiple yAxes share data — filter keeps only the first occurrence)
        const seenNames = new Set<string>();
        mainParams.forEach((p) => {
          if (!p.seriesName || seenNames.has(p.seriesName)) return;
          seenNames.add(p.seriesName);
          const pointValue = Array.isArray(p.value) ? p.value[1] : null;
          if (pointValue != null) {
            const sIdx = p.seriesIndex ?? -1;
            const key = numericSeries[sIdx]?.[0] || '';
            const sf = key ? (scaleFactors[key] ?? 1.0) : 1.0;
            const displayVal = Number(pointValue).toFixed(2);
            html += `<div style="display:flex;gap:4px;align-items:baseline"><span style="flex:none">${p.marker}</span><span style="min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="${escapeHtml(p.seriesName)}">${escapeHtml(p.seriesName)}:</span><strong style="margin-left:auto;white-space:nowrap">${displayVal}</strong>`;
            if (sf !== 1.0) {
              const rawVal = (Number(pointValue) / sf).toFixed(3);
              html += ` <span style="color:#9ca3af;font-size:10px;white-space:nowrap">(原始: ${rawVal}&times;${sf})</span>`;
            }
            html += `</div>`;
          }
        });
        if (timeIdx >= 0) {
          textSeries.forEach(([, s]) => {
            const textVal = s.text_values?.[timeIdx];
            if (textVal != null && textVal !== '') {
              html += `<div style="display:flex;gap:4px;align-items:baseline"><span style="display:inline-block;flex:none;width:8px;height:8px;border-radius:2px;background:#9ca3af"></span><span style="min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="${escapeHtml(s.label)}">${escapeHtml(s.label)}:</span><strong style="margin-left:auto;white-space:nowrap">${escapeHtml(textVal)}</strong></div>`;
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
      { type: 'inside', yAxisIndex: yAxisIndexForDZ, zoomOnMouseWheel: 'ctrl', filterMode: 'none', id: 'yInside' },
      { type: 'slider', yAxisIndex: yAxisIndexForDZ, start: 0, end: 100, right: 2, width: 18,
        backgroundColor: 'rgba(249,250,251,0.55)', filterMode: 'none', id: 'ySlider',
      },
    ],
    series: [
      ...seriesList.map(([key, s], i) => {
        const values = plottedValues.get(key) ?? [];
        return {
          name: s.label + (isNorm ? '' : s.unit ? ` (${s.unit})` : ''),
          type: 'line' as const,
          yAxisIndex: seriesYIndex[i],
          xAxisIndex: 0,
          data: times.map((t, j) => [t, values[j]]),
          smooth: true,
          showSymbol: false,
          z: 1,
          lineStyle: { width: 1.5, type: LINE_TYPES[Math.floor(i / 12) % LINE_TYPES.length], color: seriesColor(key) },
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
