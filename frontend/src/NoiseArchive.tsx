import { useState } from "react";
import {
  api,
  Empty,
  Field,
  Pager,
  url,
  useData,
  Wait,
  type Page,
} from "./shared";

const endpoint = "monitor/noise-weeks";
const archiveTab = "周度噪音报告";
const displayTime = new Intl.DateTimeFormat("zh-CN", {
  dateStyle: "medium",
  timeStyle: "short",
  timeZone: "Asia/Shanghai",
});
function time(value?: string | null) {
  if (!value) return "时间未记录";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "时间未记录" : displayTime.format(date);
}
type Week = {
  week: string;
  version: number;
  created: string;
  total: number;
  noise: number;
  sample_count: number;
};
type Source = {
  source_key: string;
  source_id: number | null;
  source__name: string | null;
  total: number;
  noise: number;
  rate: number | null;
  returned: number;
  negative_count: number;
};
type Review = {
  record_type: string;
  id: number;
  at: string;
  actor: string;
  outcome: string;
  gate: string;
  noise_reason: string;
  reason: string;
  terms: string[];
  before?: string;
  after?: string;
  before_reason?: string;
  after_reason?: string;
};
type Item = {
  key: string;
  event_id: number | null;
  source_key: string;
  source_id: number | null;
  source_name: string | null;
  url: string;
  title: string;
  origin: string;
  original: Review;
};
type Sample = Item & {
  classification: string;
  noise_reason: string;
  last_review: Review | null;
  history: Review[];
};
type Correction = Item & {
  before: string;
  after: string;
  before_reason: string;
  after_reason: string;
  review: Review;
};
type Report = {
  week: string;
  through: string;
  thresholds: { minimum_observations: number; high_noise_rate: number };
  coverage: { status: string; note: string };
  total: number;
  noise: number;
  noise_rate: number | null;
  returned: number;
  negative_count: number;
  captured: number;
  manual: number;
  legacy: number;
  sources: Source[];
  top_sources: Source[];
  reasons: { noise_reason: string; count: number; percent: number | null }[];
  negative_samples: Sample[];
  corrections: Correction[];
};
type Edition = {
  week: string;
  version: number;
  created: string;
  versions: number[];
  content: Report;
};
const classification: Record<string, string> = {
  noise: "确认噪音",
  returned: "普通退回待校准",
  clean: "非负样本",
};
const labels: Record<string, string> = {
  approved: "批准",
  rejected: "退回",
  filtered: "过滤",
  pending: "待审",
  confirmed: "确认",
  confirm: "确认",
  noise: "标为噪音",
  restore: "恢复待审",
  auto: "自动判定",
  manual: "人工录入",
  legacy: "历史审计",
  capture: "采集判定",
  approve: "批准",
  reject: "退回",
};
const show = (v?: string) => (v ? labels[v] || v : "");
const sourceName = (name: string | null) => name || "来源未记录";

const ratio = (value: number | null, base: number) =>
  base && value != null ? value + "%" : "—";
function exportPath(detail: Edition, kind: string) {
  return url(api + endpoint + "/" + detail.week, {
    version: String(detail.version),
    export: kind,
  });
}
function reference(row: Item) {
  const title = row.title || "标题未记录";
  return /^https?:\/\//i.test(row.url || "") ? (
    <a href={row.url} target="_blank" rel="noreferrer">
      {title} ↗
    </a>
  ) : (
    title
  );
}
function historyLine(row: Review) {
  return [
    time(row.at),
    row.actor,
    show(row.outcome),
    ({ IntakeDecision: "处置", Change: "审核", Event: "建档" }[
      row.record_type
    ] || row.record_type) +
      "记录 #" +
      row.id,
    row.gate,
    row.noise_reason,
    row.reason,
    row.terms?.length ? "命中 " + row.terms.join("、") : "",
  ]
    .filter(Boolean)
    .join(" · ");
}
function Samples({ rows }: { rows: Sample[] }) {
  const [page, setPage] = useState(1);
  if (!rows.length) return <Empty>没有符合条件的负样本</Empty>;
  return (
    <>
      {rows.slice((page - 1) * 20, page * 20).map((row) => (
        <article className="story" key={row.key}>
          <div className="meta">
            <span className="tag">
              {classification[row.classification] || row.classification}
            </span>
            <span>{sourceName(row.source_name)}</span>
            <span>{show(row.origin)}</span>
            {row.noise_reason && <span>{row.noise_reason}</span>}
          </div>
          <strong>{reference(row)}</strong>
          {!row.url && <small>未留存原文链接</small>}
          <details>
            <summary>查看判定记录</summary>
            {row.original && <p>初始判定　{historyLine(row.original)}</p>}
            {row.last_review && <p>最近复核　{historyLine(row.last_review)}</p>}
            {row.history.length > 0 && (
              <ol>
                {row.history.map((review, i) => (
                  <li key={i}>{historyLine(review)}</li>
                ))}
              </ol>
            )}
            {!row.original && !row.last_review && !row.history.length && (
              <p>未记录判定过程</p>
            )}
          </details>
        </article>
      ))}
      {rows.length > 20 && (
        <Pager page={page} size={20} count={rows.length} change={setPage} />
      )}
    </>
  );
}
function ReportContent({ edition }: { edition: Edition }) {
  const [source, setSource] = useState(""),
    [term, setTerm] = useState(""),
    [kind, setKind] = useState("");
  const report = edition.content;
  const matchingSources = report.sources.filter(
    (x) => !source || x.source_key === source,
  );
  const matches = (r: Sample | Correction) =>
    (!source || r.source_key === source) &&
    (!term.trim() ||
      [
        r.title,
        r.source_name,
        ...("noise_reason" in r
          ? [r.noise_reason]
          : [r.before_reason, r.after_reason, r.review.reason]),
        r.url,
      ].some((v) =>
        v?.toLocaleLowerCase().includes(term.trim().toLocaleLowerCase()),
      ));
  const samples = report.negative_samples.filter(
    (r) => matches(r) && (!kind || r.classification === kind),
  );
  const corrections = report.corrections.filter(matches);
  return (
    <>
      <p>
        {report.week} 至 {report.through} · 第 {edition.version} 版 · 归档于{" "}
        {time(edition.created)}（北京时间）
      </p>
      {report.coverage.note && <p className="notice">{report.coverage.note}</p>}
      <div className="metrics">
        {[
          ["判定原文", report.total],
          ["确认噪音", report.noise],
          ["噪音率", ratio(report.noise_rate, report.total)],
          ["普通退回待校准", report.returned],
        ].map(([k, v]) => (
          <div key={k}>
            <span>{k}</span>
            <strong>{v}</strong>
          </div>
        ))}
      </div>
      <p>
        采集 {report.captured} 篇 · 手工录入 {report.manual} 篇 · 历史方式未留存{" "}
        {report.legacy} 篇
      </p>
      <div className="actions">
        <a href={exportPath(edition, "report")} download>
          导出周报 CSV
        </a>
        <a href={exportPath(edition, "samples")} download>
          导出负样本 CSV
        </a>
      </div>
      <h3>高噪音信源排行</h3>
      <p>
        已判定至少 {report.thresholds.minimum_observations} 篇且噪音率达到{" "}
        {report.thresholds.high_noise_rate}% 的采集信源。
      </p>
      {report.top_sources.length ? (
        <ol>
          {report.top_sources.map((r) => (
            <li key={r.source_key}>
              {sourceName(r.source__name)} · 噪音 {r.noise} / 判定 {r.total} 篇
              · {ratio(r.rate, r.total)}
            </li>
          ))}
        </ol>
      ) : (
        <Empty>本周没有信源达到高噪音门槛</Empty>
      )}
      <h3>噪音原因排行</h3>
      <small>占比按本周确认的噪音篇数计算。</small>
      {report.reasons.length ? (
        <ol>
          {report.reasons.map((r) => (
            <li key={r.noise_reason}>
              {r.noise_reason} · {r.count} 篇 · {ratio(r.percent, report.noise)}
            </li>
          ))}
        </ol>
      ) : (
        <Empty>本周没有已归类的噪音原因</Empty>
      )}
      <h3>按来源核查</h3>
      <label>
        筛选信源
        <select
          aria-label="筛选信源"
          value={source}
          onChange={(e) => setSource(e.target.value)}
        >
          <option value="">全部信源</option>
          {report.sources.map((r) => (
            <option key={r.source_key} value={r.source_key}>
              {sourceName(r.source__name)}
            </option>
          ))}
        </select>
      </label>
      {matchingSources.length > 0 && (
        <details key={source} open={Boolean(source)}>
          <summary>查看来源明细 · {matchingSources.length} 项</summary>
          {matchingSources.map((row) => (
            <div className="row" key={row.source_key}>
              <b>{sourceName(row.source__name)}</b>
              <span>
                判定 {row.total} 篇 · 噪音 {row.noise} 篇 · 噪音率{" "}
                {ratio(row.rate, row.total)} · 普通退回 {row.returned} 篇
              </span>
            </div>
          ))}
        </details>
      )}
      {!matchingSources.length && <Empty>本周未记录信源判定</Empty>}
      <h3>负样本集 · {report.negative_count} 篇</h3>
      <div className="filters">
        <Field label="查找标题、原文或原因" value={term} onChange={setTerm} />
        <label>
          样本分类
          <select
            aria-label="样本分类"
            value={kind}
            onChange={(e) => setKind(e.target.value)}
          >
            <option value="">全部分类</option>
            <option value="noise">确认噪音</option>
            <option value="returned">普通退回待校准</option>
          </select>
        </label>
      </div>
      <p>匹配 {samples.length} 篇</p>
      <Samples key={JSON.stringify([source, term, kind])} rows={samples} />
      <h3>改判与撤回记录 · {corrections.length} 条</h3>
      {corrections.map((row, i) => (
        <article className="story" key={row.key + "-" + i}>
          <strong>{reference(row)}</strong>
          <p>
            {sourceName(row.source_name)} ·{" "}
            {classification[row.before] || row.before} {row.before_reason} →{" "}
            {classification[row.after] || row.after} {row.after_reason}
          </p>
          <p>
            {row.after === "clean" ? "撤回负样本" : "判定变更"} ·{" "}
            {historyLine(row.review)}
          </p>
        </article>
      ))}
      {!corrections.length && <Empty>当前条件下没有改判或撤回记录</Empty>}
    </>
  );
}

function WeekDetail({
  week,
  initialVersion,
  changed,
}: {
  week: string;
  initialVersion: string;
  changed: (version: string) => void;
}) {
  const [version, setVersion] = useState(initialVersion);
  const detail = useData<Edition>(url(endpoint + "/" + week, { version }));
  if (!detail.data || detail.error)
    return (
      <>
        <Wait error={detail.error} retry={detail.refresh} />
        {detail.error && version && (
          <button
            onClick={() => {
              setVersion("");
              changed("");
            }}
          >
            查看最新版本
          </button>
        )}
      </>
    );
  const edition = detail.data;
  return (
    <>
      <label>
        归档版本
        <select
          aria-label="归档版本"
          value={String(edition.version)}
          onChange={(e) => {
            setVersion(e.target.value);
            changed(e.target.value);
          }}
        >
          {edition.versions.map((v) => (
            <option key={v} value={v}>
              第 {v} 版
            </option>
          ))}
        </select>
      </label>
      <ReportContent
        key={edition.week + "-" + edition.version}
        edition={edition}
      />
    </>
  );
}

export default function NoiseArchive() {
  const initial = new URLSearchParams(location.search);
  const [page, setPage] = useState(1),
    [selected, setSelected] = useState(initial.get("week") || ""),
    [openVersion, setOpenVersion] = useState(initial.get("version") || "");
  const weeks = useData<Page<Week>>(endpoint + "?page=" + page);
  function navigate(week: string, version: string) {
    history.replaceState(
      null,
      "",
      url("/monitor", { tab: archiveTab, week, version }),
    );
  }
  const list = weeks.data;
  const week = selected || list?.items[0]?.week || "";
  return (
    <section className="panel" style={{ overflowWrap: "anywhere" }}>
      <h2>周度噪音报告</h2>
      {!list || weeks.error ? (
        <Wait error={weeks.error} retry={weeks.refresh} />
      ) : !list.count ? (
        <Empty>尚无已归档的周度噪音报告</Empty>
      ) : (
        <>
          <label>
            归档周
            <select
              aria-label="归档周"
              value={week}
              onChange={(e) => {
                setSelected(e.target.value);
                setOpenVersion("");
                navigate(e.target.value, "");
              }}
            >
              {selected && !list.items.some((w) => w.week === selected) && (
                <option value={selected}>{selected}</option>
              )}
              {list.items.map((w) => (
                <option key={w.week} value={w.week}>
                  {w.week} 起 · 第 {w.version} 版
                </option>
              ))}
            </select>
          </label>
          {list.count > list.size && (
            <Pager
              count={list.count}
              page={page}
              size={list.size}
              change={(p) => {
                setPage(p);
                setSelected("");
                setOpenVersion("");
                navigate("", "");
              }}
            />
          )}
          {week && (
            <WeekDetail
              key={week}
              week={week}
              initialVersion={openVersion}
              changed={(v) => navigate(week, v)}
            />
          )}
        </>
      )}
    </section>
  );
}
