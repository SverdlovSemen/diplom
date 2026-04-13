import React from "react";
import {
  ColorType,
  LineSeries,
  createChart,
  type IChartApi,
  type ISeriesApi,
  type LineData,
  type Time,
  type UTCTimestamp,
} from "lightweight-charts";

import type { Measurement } from "../api/measurements";

function toLineData(points: Measurement[]): LineData<Time>[] {
  const sorted = [...points]
    .filter((m) => m.value != null && Number.isFinite(m.value))
    .sort((a, b) => new Date(a.captured_at).getTime() - new Date(b.captured_at).getTime());
  let lastTime = -Infinity;
  return sorted.map((m) => {
    let t = new Date(m.captured_at).getTime() / 1000;
    if (t <= lastTime) t = lastTime + 1e-6;
    lastTime = t;
    return { time: t as UTCTimestamp, value: m.value as number };
  });
}

type Props = {
  points: Measurement[];
  unit: string;
  height?: number;
};

export function MeasurementDynamicsChart({ points, unit, height = 320 }: Props): React.ReactElement {
  const containerRef = React.useRef<HTMLDivElement>(null);
  const chartRef = React.useRef<IChartApi | null>(null);
  const seriesRef = React.useRef<ISeriesApi<"Line", Time> | null>(null);
  const pointsRef = React.useRef(points);
  pointsRef.current = points;

  React.useLayoutEffect(() => {
    const el = containerRef.current;
    if (!el) return;

    const chart = createChart(el, {
      layout: {
        background: { type: ColorType.Solid, color: "#ffffff" },
        textColor: "#334155",
      },
      grid: { vertLines: { color: "#e2e8f0" }, horzLines: { color: "#e2e8f0" } },
      rightPriceScale: { borderColor: "#cbd5e1" },
      timeScale: { borderColor: "#cbd5e1", timeVisible: true, secondsVisible: true },
    });
    const series = chart.addSeries(LineSeries, {
      color: "#0f172a",
      lineWidth: 2,
      title: unit ? `value, ${unit}` : "value",
    });
    chartRef.current = chart;
    seriesRef.current = series;

    const data = toLineData(pointsRef.current);
    series.setData(data);
    if (data.length > 0) chart.timeScale().fitContent();

    const applySize = () => {
      chart.applyOptions({ width: el.clientWidth, height });
    };
    applySize();
    const ro = new ResizeObserver(applySize);
    ro.observe(el);

    return () => {
      ro.disconnect();
      seriesRef.current = null;
      chartRef.current = null;
      chart.remove();
    };
  }, [height, unit]);

  React.useEffect(() => {
    const chart = chartRef.current;
    const series = seriesRef.current;
    if (!chart || !series) return;
    const data = toLineData(points);
    series.setData(data);
    if (data.length > 0) chart.timeScale().fitContent();
  }, [points]);

  return <div ref={containerRef} className="w-full" style={{ height }} />;
}
