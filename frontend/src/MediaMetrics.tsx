import React, { useState } from "react";
import { request, useAction, Notice } from "./shared";
import type { Media } from "./shared";

export const metricKinds = ["发行量", "营收", "用户数"];

export function MetricTable({
  media,
  period = "",
}: {
  media: Media;
  period?: string;
}) {
  const metrics = media.metrics || [];
  return (
    <table className="indicator-table">
      <caption>公开经营指标</caption>
      <thead>
        <tr>
          <th>指标</th>
          <th>数值及单位</th>
          <th>统计期末</th>
          <th>出处</th>
        </tr>
      </thead>
      <tbody>
        {metricKinds.flatMap((kind) => {
          const entries = metrics.filter(
            (m) => m.kind === kind && (!period || m.day === period),
          );
          return entries.length
            ? entries.map((m) => (
                <tr key={kind + m.day}>
                  <th>{kind}</th>
                  <td>
                    {m.value} {m.unit}
                  </td>
                  <td>{m.day}</td>
                  <td>
                    <a href={m.source_url} target="_blank" rel="noreferrer">
                      原文 ↗
                    </a>
                  </td>
                </tr>
              ))
            : [
                <tr key={kind}>
                  <th>{kind}</th>
                  <td colSpan={3}>暂无已核实数据</td>
                </tr>,
              ];
        })}
      </tbody>
    </table>
  );
}

export function MetricEditor({
  media,
  refresh,
}: {
  media: Media;
  refresh: () => void;
}) {
  const a = useAction(),
    [kind, setKind] = useState(metricKinds[0]);
  return (
    <details>
      <summary>维护经营指标</summary>
      <form
        className="filters"
        onSubmit={(e) => {
          e.preventDefault();
          const fields = Object.fromEntries(new FormData(e.currentTarget));
          void a.run(async () => {
            await request("media/" + media.id, { ...fields, action: "metric" });
            refresh();
          }, "经营指标已保存");
        }}
      >
        <label>
          指标
          <select
            name="kind"
            value={kind}
            onChange={(e) => setKind(e.target.value)}
          >
            {metricKinds.map((k) => (
              <option key={k}>{k}</option>
            ))}
          </select>
        </label>
        <label>
          统计期末
          <input name="day" type="date" required />
        </label>
        <label>
          已核实数值
          <input name="value" type="number" min="0" step="0.0001" required />
        </label>
        <label>
          口径和单位
          <input
            name="unit"
            maxLength={16}
            required
            placeholder={
              kind === "营收"
                ? "如 万元人民币"
                : kind === "用户数"
                  ? "如 月活用户数（人）"
                  : "如 日均发行份数"
            }
          />
        </label>
        <label>
          原始数据出处
          <input name="source_url" type="url" required />
        </label>
        <button disabled={a.busy}>保存经营指标</button>
      </form>
      <Notice text={a.notice} />
    </details>
  );
}
