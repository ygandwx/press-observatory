import React, { useState } from "react";
import {
  Heading,
  Notice,
  Wait,
  Empty,
  Field,
  Select,
  states,
  query,
  request,
  useAction,
  useData,
} from "./shared";
import CollectButton from "./CollectButton";
import { News } from "./News";
import type { Session, Dict, EventRow } from "./shared";
type Source = {
  id: number;
  name: string;
  url: string;
  media_id: number | null;
  adapter: string;
  enabled: boolean;
  trusted: boolean;
  scheduled: boolean;
  level: string;
};
type Rule = {
  id: number;
  word: string;
  kind: string;
  category: string;
  enabled: boolean;
  hits: number;
};
type Job = {
  id: number;
  source__name: string;
  trigger: string;
  state: string;
  message: string;
  progress: number;
  created: string;
  attempts: number;
  counts: Record<string, number>;
};
type MonitorData = {
  coverage: Record<string, number>;
  sources: Source[];
  rules: Rule[];
  runs: Job[];
  adapters: string[];
  policy: { enabled: boolean; threshold: number; low: number };
  missing: { name: string; media: number; channel: string }[];
  slots: number;
  pending: number;
  media: { id: number; name: string }[];
};
const tabs = [
  "采集概览",
  "覆盖度面板",
  "信源配置",
  "关键词管理",
  "自动入库设置",
  "人工巡检",
  "待审核队列",
  "动态录入",
];
function Editors({
  tab,
  d,
  refresh,
  s,
}: {
  tab: string;
  d: MonitorData;
  refresh: () => void;
  s: Session;
}) {
  const [f, set] = useState<Dict>({}),
    [on, enable] = useState(true),
    [trust, trusted] = useState(false),
    [schedule, setSchedule] = useState(false),
    a = useAction();
  const source = tab === "信源配置",
    cfg = s.rights.includes("configure");
  const change = (k: string, v: string) => set({ ...f, [k]: v });
  function edit(item: Source | Rule) {
    const v = Object.fromEntries(
      Object.entries(item).map(([k, x]) => [k, String(x ?? "")]),
    );
    set({ ...v, media: v.media_id || "" });
    enable(item.enabled);
    setSchedule("scheduled" in item && item.scheduled);
    trusted("trusted" in item && item.trusted);
  }
  function clear() {
    set({});
    enable(true);
    trusted(false);
    setSchedule(false);
  }
  return (
    <>
      <h2>{tab}</h2>
      {cfg && (
        <form
          className="filters"
          onSubmit={(e) => {
            e.preventDefault();
            void a.run(async () => {
              await request(
                "monitor/" + (source ? "source" : "rule"),
                source
                  ? {
                      name: f.name,
                      url: f.url,
                      adapter: f.adapter || "html",
                      level: f.level || "补充信源",
                      media: f.media || null,
                      enabled: on,
                      trusted: trust,
                      scheduled: schedule,
                      ...(f.id ? { id: f.id } : {}),
                    }
                  : {
                      word: f.word,
                      kind: f.kind || "主体词",
                      category: f.category || "",
                      enabled: on,
                      ...(f.id ? { id: f.id } : {}),
                    },
              );
              clear();
              refresh();
            });
          }}
        >
          {source ? (
            <>
              <Field
                required
                label="信源名称"
                value={f.name || ""}
                onChange={(v) => change("name", v)}
              />
              <Field
                required
                label="来源网址"
                type="url"
                value={f.url || ""}
                onChange={(v) => change("url", v)}
              />
              <Select
                label="接入方式"
                value={f.adapter || "html"}
                options={d.adapters}
                onChange={(v) => change("adapter", v)}
              />
              <Field
                label="信源层级"
                value={f.level || ""}
                onChange={(v) => change("level", v)}
              />
              <label>
                关联机构
                <select
                  value={f.media || ""}
                  onChange={(e) => change("media", e.target.value)}
                >
                  <option value="">未关联</option>
                  {d.media.map((m) => (
                    <option key={m.id} value={m.id}>
                      {m.name}
                    </option>
                  ))}
                </select>
              </label>
              <label className="check">
                <input
                  type="checkbox"
                  checked={trust}
                  onChange={(e) => trusted(e.target.checked)}
                />
                可信信源
              </label>
              <label className="check">
                <input
                  type="checkbox"
                  checked={schedule}
                  onChange={(e) => setSchedule(e.target.checked)}
                />
                纳入定时采集
              </label>
            </>
          ) : (
            <>
              <Field
                required
                label="关键词"
                value={f.word || ""}
                onChange={(v) => change("word", v)}
              />
              <Select
                label="匹配角色"
                value={f.kind || "主体词"}
                options={["主体词", "动作词", "排除词"]}
                onChange={(v) => change("kind", v)}
              />
              <Select
                label="关联分类"
                value={f.category || ""}
                options={Object.keys(s.catalog.events)}
                onChange={(v) => change("category", v)}
              />
            </>
          )}
          <label className="check">
            <input
              type="checkbox"
              checked={on}
              onChange={(e) => enable(e.target.checked)}
            />
            启用
          </label>
          <div className="actions">
            <button className="primary" disabled={a.busy}>
              {f.id ? "保存修改" : "添加"}
            </button>
            {f.id && (
              <button type="button" onClick={clear}>
                取消
              </button>
            )}
          </div>
        </form>
      )}
      <Notice text={a.notice} />
      {source
        ? d.sources.map((item) => (
            <div className="row" key={item.id}>
              <div className="grow">
                <b>{item.name}</b>
                <small>
                  {item.adapter} · {item.level} ·{" "}
                  {item.enabled ? "启用" : "已暂停"}
                </small>
              </div>
              {cfg && <button onClick={() => edit(item)}>编辑</button>}
            </div>
          ))
        : d.rules.map((item) => (
            <div className="row" key={item.id}>
              <b>{item.word}</b>
              <span>
                {item.kind} · 命中 {item.hits} 次 ·{" "}
                {item.enabled ? "启用" : "暂停"}
              </span>
              {cfg && <button onClick={() => edit(item)}>编辑</button>}
            </div>
          ))}
    </>
  );
}
function Policy({
  d,
  s,
  refresh,
}: {
  d: MonitorData;
  s: Session;
  refresh: () => void;
}) {
  const [enabled, setEnabled] = useState(d.policy.enabled),
    [threshold, setThreshold] = useState(String(d.policy.threshold)),
    [low, setLow] = useState(String(d.policy.low)),
    a = useAction();
  return (
    <>
      <h2>自动入库设置</h2>
      <p>
        可信来源、可核验链接、分类置信度和相似事件检查共同决定是否自动入库。
      </p>
      <fieldset disabled={!s.rights.includes("configure")}>
        <label className="check">
          <input
            type="checkbox"
            checked={enabled}
            onChange={(e) => setEnabled(e.target.checked)}
          />
          启用 AI 自动入库
        </label>
        <label>
          放行门槛
          <select
            value={threshold}
            onChange={(e) => setThreshold(e.target.value)}
          >
            {["85", "70", "55"].map((n, i) => (
              <option key={n} value={n}>
                {["严格", "标准", "宽松"][i]} · ≥ {n} 分
              </option>
            ))}
          </select>
        </label>
        <Field
          type="number"
          label="低于此分过滤"
          value={low}
          onChange={setLow}
        />
        <button
          disabled={a.busy}
          onClick={() =>
            a.run(async () => {
              await request("monitor/policy", { enabled, threshold, low });
              refresh();
            })
          }
        >
          保存设置
        </button>
      </fieldset>
      <Notice text={a.notice} />
    </>
  );
}
function Entry({ s, d }: { s: Session; d: MonitorData }) {
  const [f, set] = useState<Dict>({}),
    a = useAction();
  const ch = (k: string, v: string) => set({ ...f, [k]: v });
  return (
    <>
      <h2>动态录入</h2>
      <form
        className="filters"
        onSubmit={(e) => {
          e.preventDefault();
          void a.run(async () => {
            const row = await request<EventRow>("events", {
              ...f,
              category: f.category || "其他类",
              importance: f.importance || "常规",
            });
            location.href = "/news/" + row.id;
          });
        }}
      >
        <Field
          label="真实原文链接"
          type="url"
          required
          value={f.url || ""}
          onChange={(v) => ch("url", v)}
        />
        <label className="wide">
          原文片段
          <textarea
            value={f.text || ""}
            onChange={(e) => ch("text", e.target.value)}
          />
        </label>
        <button
          type="button"
          disabled={a.busy || (!f.text && !f.url)}
          onClick={() =>
            a.run(async () => {
              const row = await request<Dict>("monitor/extract", {
                text: f.text,
                url: f.url,
              });
              set({
                ...f,
                ...Object.fromEntries(
                  [
                    "title",
                    "summary",
                    "category",
                    "importance",
                    "subtype",
                    "occurred",
                  ]
                    .filter((k) => typeof row[k] === "string")
                    .map((k) => [k, row[k]]),
                ),
              });
            }, "已提取，请核对后提交")
          }
        >
          AI 提取
        </button>
        <Field
          label="标题"
          required
          value={f.title || ""}
          onChange={(v) => ch("title", v)}
        />
        <Field
          label="发生日"
          type="date"
          value={f.occurred || ""}
          onChange={(v) => ch("occurred", v)}
        />
        <Select
          label="事件大类"
          options={Object.keys(s.catalog.events)}
          value={f.category || "其他类"}
          onChange={(v) => set({ ...f, category: v, subtype: "" })}
        />
        <Select
          label="事件子项"
          options={s.catalog.events[f.category || "其他类"] || []}
          value={f.subtype || ""}
          onChange={(v) => ch("subtype", v)}
        />
        <Select
          label="重要度"
          options={s.catalog.importance}
          value={f.importance || "常规"}
          onChange={(v) => ch("importance", v)}
        />
        <label>
          关联机构
          <select
            value={f.media || ""}
            onChange={(e) => ch("media", e.target.value)}
          >
            <option value="">未关联</option>
            {d.media.map((m) => (
              <option key={m.id} value={m.id}>
                {m.name}
              </option>
            ))}
          </select>
        </label>
        <label className="wide">
          摘要
          <textarea
            value={f.summary || ""}
            onChange={(e) => ch("summary", e.target.value)}
          />
        </label>
        <button disabled={a.busy} className="primary">
          保存至待审核队列
        </button>
      </form>
      <Notice text={a.notice} />
    </>
  );
}
export default function Monitor({ s }: { s: Session }) {
  const [tab, setTab] = useState(
      tabs.includes(query.tab) ? query.tab : tabs[0],
    ),
    [note, setNote] = useState(""),
    [source, setSource] = useState(""),
    d = useData<MonitorData>("monitor", 8000),
    a = useAction();
  if (!d.data || d.error) return <Wait error={d.error} retry={d.refresh} />;
  const m = d.data,
    editor = s.rights.includes("edit");
  const op = (action: string, body: unknown) =>
    a.run(async () => {
      await request("monitor/" + action, body);
      d.refresh();
    }, "已记录");
  return (
    <>
      <Heading
        title="监测中心"
        sub="编辑工作台 · 信源管理、采集覆盖与内容审核"
      />
      <div className="tabs">
        {tabs.map((t) => (
          <button
            key={t}
            aria-pressed={t === tab}
            onClick={() => {
              setTab(t);
              history.replaceState(
                null,
                "",
                "/monitor?tab=" + encodeURIComponent(t),
              );
            }}
          >
            {t}
          </button>
        ))}
      </div>
      <Notice text={a.notice} />
      <section className="panel">
        {tab === tabs[0] && (
          <>
            <div className="section-title">
              <h2>采集概览</h2>
              {editor && (
                <div className="actions">
                  <select
                    aria-label="选择采集信源"
                    value={source}
                    onChange={(e) => setSource(e.target.value)}
                  >
                    <option value="">全部启用信源</option>
                    {m.sources
                      .filter((x) => x.enabled)
                      .map((x) => (
                        <option key={x.id} value={x.id}>
                          {x.name}
                        </option>
                      ))}
                  </select>
                  <CollectButton
                    source={source}
                    disabled={!m.sources.some((x) => x.enabled)}
                    refresh={d.refresh}
                  />
                </div>
              )}
            </div>
            <p>
              北京时间 08:00、18:00 定时投递 · {m.sources.length} 个信源 ·
              待审核 {m.pending ?? "—"} 件
            </p>
            <h3>最近采集记录</h3>
            {!m.runs.length && <Empty>暂无采集记录</Empty>}
            {m.runs.map((j) => (
              <div className="row" key={j.id}>
                <div className="grow">
                  <b>{j.source__name}</b>
                  <small>
                    {j.trigger} · {j.created.slice(0, 16)} · {states[j.state]} ·{" "}
                    {j.progress}%
                  </small>
                  <small>
                    {j.message}{" "}
                    {Object.entries(j.counts)
                      .map(([k, v]) => k + " " + v)
                      .join(" · ")}
                  </small>
                </div>
                {editor && ["failed", "manual"].includes(j.state) && (
                  <button onClick={() => op("retry", { id: j.id })}>
                    重试
                  </button>
                )}
              </div>
            ))}
          </>
        )}
        {tab === tabs[1] && (
          <>
            <h2>覆盖度与五件套补全</h2>
            <div className="snapshot">
              {Object.entries(m.coverage).map(([k, v]) => (
                <div key={k}>
                  <strong>{v}</strong>
                  <small>{k}</small>
                </div>
              ))}
            </div>
            <p>
              已补全 {m.slots - m.missing.length} / {m.slots} 个渠道。
            </p>
            {m.missing.map((x) => (
              <div className="row" key={x.media + x.channel}>
                <b>{x.name}</b>
                <span>{x.channel} 待补</span>
                <a href={"/archives/" + x.media + (editor ? "?edit=1" : "")}>
                  {editor ? "去补全" : "查看"}
                </a>
              </div>
            ))}
            {!m.missing.length && <Empty>暂无待补项</Empty>}
          </>
        )}
        {[tabs[2], tabs[3]].includes(tab) && (
          <Editors key={tab} tab={tab} d={m} s={s} refresh={d.refresh} />
        )}
        {tab === tabs[4] && <Policy d={m} s={s} refresh={d.refresh} />}
        {tab === tabs[5] && (
          <>
            <h2>人工巡检</h2>
            {editor && (
              <Field label="巡检结论" value={note} onChange={setNote} />
            )}{" "}
            {m.runs
              .filter((j) =>
                ["manual", "failed", "inspected"].includes(j.state),
              )
              .map((j) => (
                <div className="row" key={j.id}>
                  <div>
                    <b>{j.source__name}</b>
                    <small>{j.message}</small>
                  </div>
                  {editor && j.state !== "inspected" && (
                    <button
                      disabled={!note.trim()}
                      onClick={() => op("inspect", { id: j.id, note })}
                    >
                      记录结论
                    </button>
                  )}
                </div>
              ))}
            {!m.runs.some((j) =>
              ["manual", "failed", "inspected"].includes(j.state),
            ) && <Empty>暂无巡检任务</Empty>}
          </>
        )}
        {tab === tabs[6] && <News s={s} pending />}
        {tab === tabs[7] &&
          (editor ? (
            <Entry s={s} d={m} />
          ) : (
            <Empty>当前账号未获编辑权限</Empty>
          ))}
      </section>
    </>
  );
}
