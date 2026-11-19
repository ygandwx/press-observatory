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
import NoiseArchive from "./NoiseArchive";
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
  zone: string;
  stop_reason: string;
  latest?: Job;
  profile: { section?: string; cadence?: string; [key: string]: unknown };
  effective_cadence: string;
  scope_reason: string;
  collectible: boolean;
  noise_week?: {
    total: number;
    noise: number;
    rate: number;
    high_noise: boolean;
  };
};
type Rule = {
  id: number;
  word: string;
  kind: string;
  category: string;
  media_category: string;
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
type NoiseReport = {
  total: number;
  noise: number;
  feedback: number;
  sources: {
    source_id: number;
    source__name: string;
    total: number;
    noise: number;
    rate: number;
  }[];
  reasons: { noise_reason: string; count: number }[];
};
type EditorialStats = {
  minimum_observations: number;
  high_noise_rate: number;
  failed_names: string[];
  daily: NoiseReport;
  weekly: NoiseReport;
  oldest_pending: string | null;
  pending_hours: number | null;
  failed_sources: number;
  by_category: { category: string; observed: number; target: number | null }[];
  decisions: {
    title: string;
    url: string;
    outcome: string;
    noise_reason: string;
    filtered_by: string;
    terms: string[];
    at: string;
    actor: string;
    source_zone: string;
  }[];
};
type MonitorData = {
  editorial: EditorialStats;
  coverage: Record<string, number>;
  sources: Source[];
  rules: Rule[];
  runs: Job[];
  adapters: string[];
  section_options: Record<string, string[]>;
  policy: {
    enabled: boolean;
    ai_enabled: boolean;
    threshold: number;
    low: number;
  };
  missing: { name: string; media: number; channel: string }[];
  slots: number;
  pending: number;
  media: { id: number; name: string }[];
};
const archiveTab = "周度噪音报告";
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
    set({
      ...v,
      media: v.media_id || "",
      section: "profile" in item ? item.profile.section || "" : "",
      cadence: "profile" in item ? item.profile.cadence || "twice_daily" : "",
    });
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
                      profile: {
                        ...(d.sources.find((x) => String(x.id) === f.id)
                          ?.profile || {}),
                        ...(f.section ? { section: f.section } : {}),
                        cadence: f.cadence || "twice_daily",
                      },
                      zone: f.zone || "",
                      stop_reason: f.stop_reason || "",
                      level: f.level || "补充信源",
                      media: f.media || null,
                      enabled: on,
                      trusted: trust,
                      scheduled: schedule,
                      ...(f.id ? { id: f.id } : {}),
                    }
                  : {
                      word: f.word,
                      media_category: f.media_category || "",
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
              <Select
                label="信源类型"
                value={f.zone || ""}
                options={Object.keys(d.section_options)}
                onChange={(v) => set({ ...f, zone: v, section: "" })}
              />
              <Select
                label="已核实栏目"
                value={f.section || ""}
                options={d.section_options[f.zone || ""] || []}
                onChange={(v) => change("section", v)}
              />
              <label>
                采集频次
                <select
                  value={f.cadence || "twice_daily"}
                  onChange={(e) => change("cadence", e.target.value)}
                >
                  <option value="twice_daily">每日两次</option>
                  <option value="daily">每日一次</option>
                  <option value="weekly">每周一次</option>
                </select>
              </label>
              <Field
                label="停用或降频原因"
                value={f.stop_reason || ""}
                onChange={(v) => change("stop_reason", v)}
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
                label="适用媒体品类"
                value={f.media_category || ""}
                options={Object.keys(s.catalog.categories)}
                onChange={(v) => change("media_category", v)}
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
                  {item.enabled ? "启用" : "已暂停"} ·{" "}
                  {item.zone || "待核实栏目"} {item.stop_reason}
                  <small>
                    {item.scheduled ? "已配置定时" : "手动采集"} ·{" "}
                    {item.collectible ? "当前可采" : "当前受限"} · 近七日{" "}
                    {item.noise_week
                      ? `噪音 ${item.noise_week.noise} / ${item.noise_week.total} 篇`
                      : "暂无可判定原文"}
                  </small>
                  {item.scope_reason && (
                    <small>当前不可采 · {item.scope_reason}</small>
                  )}
                  {item.latest && (
                    <small>
                      最近采集 {new Date(item.latest.created).toLocaleString()}{" "}
                      · {states[item.latest.state] || item.latest.state} · 发现{" "}
                      {item.latest.counts["发现"] || 0} 条 ·{" "}
                      {item.latest.message}
                    </small>
                  )}
                </small>
              </div>
              {cfg && (
                <>
                  <button onClick={() => edit(item)}>编辑</button>
                  <button
                    onClick={() =>
                      a.run(async () => {
                        await request("monitor/archive-source", {
                          id: item.id,
                        });
                        refresh();
                      })
                    }
                  >
                    移出采集
                  </button>
                </>
              )}
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
    [ai, setAI] = useState(d.policy.ai_enabled),
    [targets, setTargets] = useState<Record<string, string>>(
      Object.fromEntries(
        d.editorial.by_category
          .filter((r) => r.target != null)
          .map((r) => [r.category, String(r.target)]),
      ),
    ),
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
        <label className="check">
          <input
            type="checkbox"
            checked={ai}
            onChange={(e) => setAI(e.target.checked)}
          />
          启用 AI 分析
        </label>
        <Field
          label="放行门槛"
          type="number"
          value={threshold}
          onChange={setThreshold}
        />
        <p>
          低于 {low} 分过滤，{low} 至 {Number(threshold) - 1} 分人工复核，达到{" "}
          {threshold} 分核对自动入库条件。
        </p>
        {d.editorial.by_category.map((r) => (
          <Field
            key={r.category}
            label={r.category + "每日目标"}
            type="number"
            value={targets[r.category] || ""}
            onChange={(v) => setTargets({ ...targets, [r.category]: v })}
          />
        ))}
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
              await request("monitor/policy", {
                enabled,
                ai_enabled: ai,
                threshold,
                low,
                category_targets: Object.fromEntries(
                  Object.entries(targets).filter(([, v]) => v !== ""),
                ),
              });
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
                capture_method: "AI 辅助录入",
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
  const availableTabs = s.role === "运营编辑" ? [...tabs, archiveTab] : tabs;
  const [tab, setTab] = useState(
      availableTabs.includes(query.tab) ? query.tab : tabs[0],
    ),
    [note, setNote] = useState(""),
    [source, setSource] = useState(""),
    d = useData<MonitorData>(tab === archiveTab ? null : "monitor", 8000),
    a = useAction();
  const navigation = (
    <>
      <Heading
        title="监测中心"
        sub="编辑工作台 · 信源管理、采集覆盖与内容审核"
      />
      <div className="tabs">
        {availableTabs.map((t) => (
          <button
            key={t}
            aria-pressed={t === tab}
            onClick={() => {
              if (t === tab) return;
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
    </>
  );
  if (tab === archiveTab)
    return (
      <>
        {navigation}
        <NoiseArchive />
      </>
    );
  if (!d.data || d.error)
    return (
      <>
        {navigation}
        <Wait error={d.error} retry={d.refresh} />
      </>
    );
  const m = d.data,
    editor = s.rights.includes("edit");
  const op = (action: string, body: unknown) =>
    a.run(async () => {
      await request("monitor/" + action, body);
      d.refresh();
    }, "已记录");
  return (
    <>
      {navigation}
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
            <p>
              每个信源在所选时段内按原文链接去重。近七日至少{" "}
              {m.editorial.minimum_observations} 篇、噪音率达{" "}
              {m.editorial.high_noise_rate}%
              的定时源暂按每周一次采集，人工采集仍可使用。
            </p>
            {m.sources
              .filter((x) => x.scheduled && x.noise_week?.high_noise)
              .map((x) => (
                <p key={x.id}>
                  {x.name} · 本周降频 · 噪音 {x.noise_week!.noise} /{" "}
                  {x.noise_week!.total}
                </p>
              ))}
            <p>
              最近一次采集失败的信源 {m.editorial.failed_sources} 个
              {m.editorial.failed_names.length
                ? "（" + m.editorial.failed_names.join("、") + "）"
                : ""}
              。最早待审{" "}
              {m.editorial.oldest_pending
                ? new Date(m.editorial.oldest_pending).toLocaleString() +
                  "（已等待 " +
                  m.editorial.pending_hours +
                  " 小时）"
                : "暂无"}
              。
            </p>
            {m.editorial.by_category.map((r) => (
              <p key={r.category}>
                {r.category} · 今日采集 {r.observed} / {r.target ?? "未设目标"}
              </p>
            ))}
            {[
              ["今日", m.editorial.daily],
              ["近七日", m.editorial.weekly],
            ].map(([label, report]) => {
              const r = report as NoiseReport;
              return (
                <details key={String(label)} open={label === "近七日"}>
                  <summary>
                    {String(label)}噪音结构 · {r.noise} / {r.total} 条
                  </summary>
                  <p>
                    人工回标 {r.feedback} 条。
                    {r.reasons
                      .map((x) => x.noise_reason + " " + x.count + " 条")
                      .join(" · ")}
                  </p>
                  {r.sources.length ? (
                    r.sources.map((x) => (
                      <p key={x.source_id}>
                        {x.source__name} · {x.noise}/{x.total} · {x.rate}%
                      </p>
                    ))
                  ) : (
                    <Empty>本期尚无采集判定</Empty>
                  )}
                </details>
              );
            })}
            <details>
              <summary>最近逐条判定与排除词</summary>
              {m.editorial.decisions.map((x, i) => (
                <p key={i}>
                  {x.title} · {x.outcome} · {x.noise_reason || x.filtered_by} ·{" "}
                  {x.terms.join("、")} · {x.source_zone} · {x.actor} ·{" "}
                  {new Date(x.at).toLocaleString()}
                </p>
              ))}
            </details>
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
