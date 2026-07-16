import type { MutableRefObject } from 'react';
import type * as echarts from 'echarts';

interface ZoomOption {
  type?: string;
  yAxisIndex?: unknown;
  start?: number;
  end?: number;
}

interface GridRect {
  x: number;
  y: number;
  width: number;
  height: number;
}

interface EChartsGridAccess {
  getModel(): {
    getComponent(type: string, index: number): {
      coordinateSystem?: { getRect?: () => GridRect };
    } | undefined;
  };
}

interface PointerOffset {
  offsetX: number;
  offsetY: number;
}

function zoomOptions(value: unknown): ZoomOption[] {
  return Array.isArray(value) ? value.filter((item): item is ZoomOption => typeof item === 'object' && item !== null) : [];
}

export function bindDoubleClickZoom(
  instance: echarts.ECharts,
  yZoomRef: MutableRefObject<{ start: number; end: number }>,
) {
  instance.getZr().on('dblclick', (event: PointerOffset) => {
    const zoom = 2;
    const minRange = 2;
    const options = instance.getOption() as Record<string, unknown>;
    const dataZoom = zoomOptions(options.dataZoom);
    const xSlider = dataZoom.find((item) => item.type === 'slider' && item.yAxisIndex === undefined);
    const ySlider = dataZoom.find((item) => item.type === 'slider' && item.yAxisIndex !== undefined);
    const xStart = xSlider?.start ?? 0;
    const xEnd = xSlider?.end ?? 100;
    const yStart = ySlider?.start ?? 0;
    const yEnd = ySlider?.end ?? 100;

    const grid = (instance as unknown as EChartsGridAccess).getModel().getComponent('grid', 0);
    const rect = grid?.coordinateSystem?.getRect?.();
    const xFraction = rect ? Math.max(0, Math.min(1, (event.offsetX - rect.x) / rect.width)) : 0.5;
    const yFraction = rect ? 1 - Math.max(0, Math.min(1, (event.offsetY - rect.y) / rect.height)) : 0.5;
    const xCenter = xStart + xFraction * (xEnd - xStart);
    const yCenter = yStart + yFraction * (yEnd - yStart);

    const xRange = xEnd - xStart;
    if (xRange > minRange) {
      const nextRange = xRange / zoom;
      instance.dispatchAction({
        type: 'dataZoom',
        dataZoomIndex: 0,
        start: Math.max(0, xCenter - nextRange / 2),
        end: Math.min(100, xCenter + nextRange / 2),
      });
    }

    const yRange = yEnd - yStart;
    if (yRange > minRange) {
      const nextRange = yRange / zoom;
      const start = Math.max(0, yCenter - nextRange / 2);
      const end = Math.min(100, yCenter + nextRange / 2);
      yZoomRef.current = { start, end };
      instance.dispatchAction({ type: 'dataZoom', dataZoomId: 'ySlider', start, end });
    }
  });
}
