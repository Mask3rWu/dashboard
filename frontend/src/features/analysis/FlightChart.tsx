import { forwardRef, useEffect, useImperativeHandle, useRef } from 'react';
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

const FlightChart = forwardRef<FlightChartHandle, Props>(function FlightChart({ active, aligned, normalize, scaleFactors, emptyState }, ref) {
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
    </>
  );
});

export default FlightChart;
