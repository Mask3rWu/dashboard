import { forwardRef, useEffect, useImperativeHandle, useRef, useState } from 'react';
import * as echarts from 'echarts';
import type { AlignedData } from '../../api/analysis';
import { bindDoubleClickZoom } from './chartInteraction';
import { buildChartOption } from './chartOptions';

export interface FlightChartHandle {
  resetZoom: () => void;
}

interface EmptyStateData {
  title: string;
  description: string;
}

interface Props {
  active: boolean;
  aligned: AlignedData | null;
  normalize: boolean;
  scaleFactors: Record<string, number>;
  selectedColumns: string[];
  emptyState: EmptyStateData | null;
}

function EmptyState({ title, description }: EmptyStateData) {
  return (
    <div className="absolute inset-0 flex items-center justify-center pointer-events-none">
      <div className="max-w-md px-6 py-5 text-center">
        <div className="mx-auto mb-3 flex h-10 w-10 items-center justify-center rounded-full border border-gray-200 bg-gray-50 text-gray-400">!</div>
        <div className="text-sm font-medium text-gray-700">{title}</div>
        <div className="mt-1 text-xs leading-5 text-gray-500">{description}</div>
      </div>
    </div>
  );
}

function ChartDebugBadge({ active, chartRef, chartInstance, aligned, selectedColumns }: {
  active: boolean;
  chartRef: React.RefObject<HTMLDivElement | null>;
  chartInstance: React.MutableRefObject<echarts.ECharts | null>;
  aligned: AlignedData | null;
  selectedColumns: string[];
}) {
  const [metrics, setMetrics] = useState({ tick: 0, width: 0, height: 0, visible: false, instanceWidth: -1, instanceHeight: -1, hasInstance: false });
  useEffect(() => {
    const sample = () => {
      const element = chartRef.current;
      const instance = chartInstance.current;
      setMetrics((current) => ({
        tick: current.tick + 1,
        width: element?.clientWidth ?? 0,
        height: element?.clientHeight ?? 0,
        visible: element?.offsetParent !== null,
        instanceWidth: instance?.getWidth?.() ?? -1,
        instanceHeight: instance?.getHeight?.() ?? -1,
        hasInstance: !!instance,
      }));
    };
    sample();
    const id = window.setInterval(sample, 250);
    return () => window.clearInterval(id);
  }, [chartInstance, chartRef]);

  const forceResize = () => {
    if (chartInstance.current) {
      try { chartInstance.current.resize(); } catch { /* ignore */ }
    }
  };
  const seriesCount = aligned ? Object.keys(aligned.series || {}).length : 0;
  const timesCount = aligned?.times?.length ?? 0;

  return (
    <div onClick={forceResize} title="Click to force chart.resize()" className="absolute bottom-2 right-2 z-50 bg-black/75 text-white text-[10px] font-mono px-2 py-1 rounded leading-tight cursor-pointer hover:bg-black/90 select-none" style={{ pointerEvents: 'auto' }}>
      <div>active:{String(active)} vis:{String(metrics.visible)}</div>
      <div>DOM:{metrics.width}×{metrics.height} inst:{metrics.hasInstance ? `${metrics.instanceWidth}×${metrics.instanceHeight}` : 'null'}</div>
      <div>data:{seriesCount}s/{timesCount}p cols:{selectedColumns.length}</div>
      <div>tick:{metrics.tick} (click→resize)</div>
    </div>
  );
}

const FlightChart = forwardRef<FlightChartHandle, Props>(function FlightChart({ active, aligned, normalize, scaleFactors, selectedColumns, emptyState }, ref) {
  const chartRef = useRef<HTMLDivElement>(null);
  const chartInstance = useRef<echarts.ECharts | null>(null);
  const yZoomRef = useRef({ start: 0, end: 100 });

  useImperativeHandle(ref, () => ({
    resetZoom: () => {
      yZoomRef.current = { start: 0, end: 100 };
      chartInstance.current?.dispatchAction({ type: 'dataZoom', dataZoomIndex: 0, start: 0, end: 100 });
      chartInstance.current?.dispatchAction({ type: 'dataZoom', dataZoomId: 'ySlider', start: 0, end: 100 });
    },
  }), []);

  useEffect(() => {
    if (!active || !chartRef.current) {
      if (chartInstance.current) {
        try { chartInstance.current.dispose(); } catch { /* ignore */ }
        chartInstance.current = null;
      }
      return;
    }

    const container = chartRef.current;
    const scrollVisibleTooltip = (event: WheelEvent) => {
      const tooltip = container.querySelector<HTMLElement>('.flight-chart-tooltip');
      if (!tooltip || tooltip.style.display === 'none' || tooltip.style.visibility === 'hidden'
        || tooltip.scrollHeight <= tooltip.clientHeight) return;

      tooltip.scrollTop += event.deltaY;
      event.preventDefault();
      event.stopPropagation();
    };
    container.addEventListener('wheel', scrollVisibleTooltip, { capture: true, passive: false });
    const applyOption = (instance: echarts.ECharts) => {
      if (!aligned) return;
      try {
        instance.setOption(buildChartOption(aligned, normalize, scaleFactors), true);
        requestAnimationFrame(() => {
          try { instance.resize(); } catch { /* ignore */ }
        });
      } catch (error) {
        console.error('setOption failed:', error);
      }
    };
    const createInstance = () => {
      if (container.clientWidth === 0 || container.clientHeight === 0) return null;
      try {
        const instance = echarts.init(container);
        bindDoubleClickZoom(instance, yZoomRef);
        return instance;
      } catch (error) {
        console.error('ECharts init failed:', error);
        return null;
      }
    };

    if (chartInstance.current) {
      try { chartInstance.current.dispose(); } catch { /* ignore */ }
      chartInstance.current = null;
    }
    chartInstance.current = createInstance();
    if (chartInstance.current) applyOption(chartInstance.current);

    const observer = new ResizeObserver(() => {
      if (!chartInstance.current) {
        chartInstance.current = createInstance();
        if (chartInstance.current) applyOption(chartInstance.current);
      } else {
        try { chartInstance.current.resize(); } catch { /* ignore */ }
      }
    });
    observer.observe(container);
    return () => {
      observer.disconnect();
      container.removeEventListener('wheel', scrollVisibleTooltip, { capture: true });
      if (chartInstance.current) {
        try { chartInstance.current.dispose(); } catch { /* ignore */ }
        chartInstance.current = null;
      }
    };
  }, [active, aligned, normalize, scaleFactors]);

  return (
    <>
      <div ref={chartRef} className="flex-1 min-h-0" />
      {emptyState && <EmptyState title={emptyState.title} description={emptyState.description} />}
      <ChartDebugBadge active={active} chartRef={chartRef} chartInstance={chartInstance} aligned={aligned} selectedColumns={selectedColumns} />
    </>
  );
});

export default FlightChart;
