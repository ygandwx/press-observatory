import React, { useEffect, useRef, useState } from "react";
import * as echarts from "echarts/core";
import { BarChart, LineChart } from "echarts/charts";
import { GridComponent, TooltipComponent } from "echarts/components";
import { CanvasRenderer } from "echarts/renderers";
import {
  Heading,
  Wait,
  Empty,
  Notice,
  Story,
  day,
  request,
  url,
  useAction,
  useData,
} from "./shared";
import type { Dict, EventRow, Session } from "./shared";
echarts.use([
  BarChart,
  LineChart,
  GridComponent,
  TooltipComponent,
  CanvasRenderer,
]);
type Stats = {
  cards: {
    name: string;
    value: string | number;
    change?: number;
    note?: string;
    path?: string;
    filters: Dict;
  }[];
  regions: Record<string, number>;
  types: Record<string, number>;
  categories: Record<string, number>;
  changes: Record<string, number>;
  trend: Record<string, number>;
  period: Dict;
  focused: EventRow[];
  insight: string;
  alerts: { category: string; change: number }[];
};
function Graph({
  values,
  line = false,
}: {
  values: Record<string, number>;
  line?: boolean;
}) {
  const el = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!el.current || !Object.keys(values).length) return;
    const c = echarts.init(el.current);
    c.setOption({
      grid: { top: 16, left: 32, right: 15, bottom: 50 },
      tooltip: { trigger: "axis" },
      xAxis: {
        type: "category",
        data: Object.keys(values).map((k) => (line ? k.slice(5) : k)),
        axisLabel: { color: "#58667a", rotate: line ? 0 : 22 },
        axisLine: { lineStyle: { color: "#dce4e9" } },
      },
      yAxis: {
        type: "value",
        minInterval: 1,
        splitLine: { lineStyle: { color: "#edf0f3" } },
      },
      series: [
        {
          type: line ? "line" : "bar",
          data: Object.values(values),
          smooth: true,
          barMaxWidth: 34,
          itemStyle: { color: line ? "#487b8c" : "#375c7b" },
          areaStyle: line ? { opacity: 0.1 } : undefined,
        },
      ],
    });
    const observer = new ResizeObserver(() => c.resize());
    observer.observe(el.current);
    return () => {
      observer.disconnect();
      c.dispose();
    };
  }, [values, line]);
  return Object.keys(values).length ? (
    <div
      className="graph"
      ref={el}
      role="img"
      aria-label={Object.entries(values)
        .map(([k, v]) => k + " " + v)
        .join("，")}
    />
  ) : (
    <Empty>当前区间暂无分布数据</Empty>
  );
}
export default function Dashboard({ s }: { s: Session }) {
  const [days, setDays] = useState("7"),
    d = useData<Stats>("dashboard?days=" + days),
    a = useAction();
  return (
    <>
      <Heading title="领导看板" sub="全国纸媒业态每日动态总览">
        {s.rights.includes("edit") && (
          <button
            className="red"
            disabled={a.busy}
            onClick={() =>
              a.run(async () => {
                const result = await request<{ jobs: number[] }>(
                  "monitor/collect",
                  { key: crypto.randomUUID() },
                );
                if (!result.jobs.length)
                  throw new Error("请先配置并启用信源。");
                location.href = "/monitor?tab=采集概览";
              }, "已投递")
            }
          >
            一键采集入库
          </button>
        )}
        <a className="button" href="/summary">
          查看今日日报 ↗
        </a>
      </Heading>
      {!d.data || d.error ? (
        <Wait error={d.error} retry={d.refresh} />
      ) : (
        <>
          <div className="metrics">
            {d.data.cards.map((c) => {
              const inside = (
                <>
                  <span>{c.name}</span>
                  <strong>{c.value}</strong>
                  <small>
                    {c.change !== undefined
                      ? "较昨日 " + (c.change > 0 ? "+" : "") + c.change + " 件"
                      : c.note || "在库记录"}
                  </small>
                </>
              );
              return c.path ? (
                <a key={c.name} href={url(c.path, c.filters)}>
                  {inside}
                </a>
              ) : (
                <div key={c.name}>{inside}</div>
              );
            })}
          </div>
          <div className="section-title">
            <h2>变化研判</h2>
            <div className="seg">
              {["7", "30", "90"].map((n) => (
                <button
                  key={n}
                  aria-pressed={n === days}
                  onClick={() => setDays(n)}
                >
                  {n} 天
                </button>
              ))}
            </div>
          </div>
          {d.data.alerts.length > 0 && (
            <section className="panel">
              <h2>变化提醒</h2>
              <small>较上一等长区间变化至少 3 件且达到 50%</small>
              {d.data.alerts.map((x) => (
                <a
                  className="row"
                  key={x.category}
                  href={url("/news", {
                    ...d.data!.period,
                    category: x.category,
                  })}
                >
                  {x.category} · {x.change > 0 ? "+" : ""}
                  {x.change} 件 ↗
                </a>
              ))}
            </section>
          )}
          <div className="insight">
            <div>
              <span className="kicker">AI 变化研判</span>
              <p>{d.data.insight || "当前区间暂无研判摘要"}</p>
              <small>
                按北京时间统计首次入库事件 · {d.data.period.start} 至 {day()}
              </small>
            </div>
            {s.rights.includes("edit") && (
              <button
                disabled={a.busy}
                onClick={() =>
                  a.run(async () => {
                    await request("dashboard?days=" + days, {});
                    d.refresh();
                  }, "研判已更新")
                }
              >
                生成研判
              </button>
            )}
          </div>
          <Notice text={a.notice} />
          <div className="section-title">
            <h2>动态分类导航</h2>
            <span>较上一个等长区间</span>
          </div>
          <div className="categories">
            {Object.entries(d.data.categories).map(([k, v]) => (
              <a
                key={k}
                href={url("/news", { ...d.data!.period, category: k })}
              >
                <span>{k}</span>
                <strong>{v}</strong>
                <small>
                  {d.data!.changes[k] > 0 ? "+" : ""}
                  {d.data!.changes[k]} 件
                </small>
              </a>
            ))}
          </div>
          <div className="charts">
            {Object.entries({
              动态趋势: d.data.trend,
              事件类型分布: d.data.categories,
              品类分布: d.data.types,
              地区分布: d.data.regions,
            }).map(([k, v]) => (
              <section className="panel" key={k}>
                <h2>{k}</h2>
                <Graph values={v} line={k === "动态趋势"} />
              </section>
            ))}
          </div>
          <section className="panel">
            <h2>重点关注动态</h2>
            {d.data.focused.length ? (
              d.data.focused.map((e) => <Story key={e.id} e={e} />)
            ) : (
              <Empty>暂无已标星或高重要度动态</Empty>
            )}
          </section>
        </>
      )}
    </>
  );
}
