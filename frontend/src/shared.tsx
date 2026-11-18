import React, { useCallback, useEffect, useRef, useState } from "react";
export type Dict = Record<string, string>;
export type Catalog = {
  market_sections: string[];
  events: Record<string, string[]>;
  categories: Record<string, string[]>;
  channels: string[];
  levels: string[];
  focus: string[];
  importance: string[];
};
export type Session = {
  username: string;
  role: string;
  rights: string[];
  catalog: Catalog;
  csrf: string;
};
export type Media = {
  digest_section: string;
  description: string;
  archive_status: string;
  high_priority: boolean;
  origin_id?: string;
  verification?: {
    record?: {
      date: string;
      collection_note?: string;
      review_history?: { field: string; value: string; url: string; basis: string; date: string; reason?: string }[];
      related_channels?: { field: string; name: string; relationship: string; entity?: string; url: string; note: string }[];
      facts: {
        field: string;
        value: string;
        url: string;
        basis: string;
        date: string;
      }[];
      checks: Record<string, string>;
      searches: { url: string; note: string }[];
      candidates: { url: string; note: string }[];
    };
  };
  reviewed_sources?: {
    id: number;
    name: string;
    url: string;
    enabled: boolean;
    scheduled: boolean;
  }[];
  origin_record?: {
    url?: string;
    metadata?: Record<string, { text: string; links: string[][] }>;
    form?: Record<string, unknown>;
  };
  id: number;
  name: string;
  owner: string;
  level: string;
  category: string;
  subcategory: string;
  region: string;
  kind: string;
  focus: string;
  tags: string[];
  channels: Dict;
  reference: string;
  timeline?: EventRow[];
  count?: number;
  following?: boolean;
};
export type EventRow = {
  id: number;
  title: string;
  summary: string;
  state: string;
  category: string;
  subtype: string;
  importance: string;
  occurred: string;
  approved: string;
  created: string;
  creator: string;
  auto: boolean;
  score: number | null;
  reasoning: string;
  version: number;
  starred: boolean;
  tags: string[];
  media: Pick<Media, "id" | "name" | "region" | "category">[];
  articles: { id: number; title: string; url: string; status: string }[];
  merged: { id: number; title: string; version: number }[];
  history?: { action: string; actor: string; at: string; detail: unknown }[];
};
export type Page<T> = { items: T[]; count: number; size: number; page: number };
export const api = "/api/v1/";
export const query = Object.fromEntries(new URLSearchParams(location.search));
export function url(path: string, filters: Dict = {}) {
  const q = new URLSearchParams(
    Object.entries(filters).filter(([, v]) => v !== "" && v != null),
  );
  return path + (q.size ? "?" + q : "");
}
export const day = () =>
  new Intl.DateTimeFormat("sv-SE", { timeZone: "Asia/Shanghai" }).format(
    new Date(),
  );
function errorMessage(data: unknown): string {
  if (typeof data === "string") return data;
  if (Array.isArray(data)) return data.map(errorMessage).join("；");
  if (data && typeof data === "object")
    return Object.entries(data)
      .map(([k, v]) => (k === "detail" ? "" : k + "：") + errorMessage(v))
      .join("；");
  return "请求失败";
}
export async function request<T>(
  path: string,
  body?: unknown,
  method?: string,
): Promise<T> {
  const csrf =
    document.cookie
      .split("; ")
      .find((x) => x.startsWith("csrftoken="))
      ?.split("=")[1] || "";
  const response = await fetch(api + path, {
    method: method || (body === undefined ? "GET" : "POST"),
    credentials: "same-origin",
    headers: { "Content-Type": "application/json", "X-CSRFToken": csrf },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const data = await response
    .json()
    .catch(() => ({ detail: "服务未就绪，请重试。" }));
  if (!response.ok) throw new Error(errorMessage(data));
  return data;
}
export function useData<T>(path: string | null, interval = 0) {
  const [data, setData] = useState<T | null>(null),
    [error, setError] = useState(""),
    [tick, bump] = useState(0);
  const refresh = useCallback(() => bump((x) => x + 1), []);
  useEffect(() => {
    setData(null);
    setError("");
  }, [path]);
  useEffect(() => {
    if (!path) return;
    let active = true;
    const read = () =>
      request<T>(path)
        .then((d) => {
          if (active) {
            setData(d);
            setError("");
          }
        })
        .catch((e) => {
          if (active) setError(e.message);
        });
    void read();
    const timer = interval ? setInterval(read, interval) : undefined;
    return () => {
      active = false;
      clearInterval(timer);
    };
  }, [path, tick, interval]);
  return { data, error, refresh };
}
export function useAction() {
  const [notice, setNotice] = useState(""),
    [busy, setBusy] = useState(false);
  const lock = useRef(false);
  async function run(fn: () => Promise<void>, ok = "已保存") {
    if (lock.current) return;
    lock.current = true;
    setBusy(true);
    setNotice("");
    try {
      await fn();
      setNotice(ok);
    } catch (e) {
      setNotice(e instanceof Error ? e.message : "操作失败");
    } finally {
      lock.current = false;
      setBusy(false);
    }
  }
  return { run, notice, busy };
}
export const Notice = ({ text }: { text: string }) =>
  text ? (
    <p className="notice" role="status">
      {text}
    </p>
  ) : null;
export function Wait({ error, retry }: { error: string; retry: () => void }) {
  return (
    <div className="empty" role="status">
      {error || "正在读取数据…"}{" "}
      {error && <button onClick={retry}>重试</button>}
    </div>
  );
}
export function Empty({ children }: { children: React.ReactNode }) {
  return <div className="empty">{children}</div>;
}
export function Heading({
  title,
  sub,
  children,
}: {
  title: string;
  sub?: string;
  children?: React.ReactNode;
}) {
  return (
    <div className="heading">
      <div>
        <h1>{title}</h1>
        {sub && <p>{sub}</p>}
      </div>
      <div className="actions">{children}</div>
    </div>
  );
}
export function Select({
  label,
  value,
  options,
  onChange,
  empty = "全部",
  allowEmpty = true,
}: {
  label: string;
  value: string;
  options: string[];
  empty?: string;
  allowEmpty?: boolean;
  onChange: (v: string) => void;
}) {
  return (
    <label>
      {label}
      <select value={value} onChange={(e) => onChange(e.target.value)}>
        {allowEmpty && <option value="">{empty}</option>}
        {options.map((x) => (
          <option key={x}>{x}</option>
        ))}
      </select>
    </label>
  );
}
export function Field({
  label,
  value,
  onChange,
  type = "text",
  required = false,
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  type?: string;
  required?: boolean;
}) {
  return (
    <label>
      {label}
      <input
        type={type}
        value={value}
        required={required}
        onChange={(e) => onChange(e.target.value)}
      />
    </label>
  );
}
export function Pager({
  count,
  page,
  size,
  change,
}: {
  count: number;
  page: number;
  size: number;
  change: (n: number) => void;
}) {
  return (
    <div className="pager">
      <span>
        共 {count} 条 · 第 {page} / {Math.max(1, Math.ceil(count / size))} 页
      </span>
      <button disabled={page <= 1} onClick={() => change(page - 1)}>
        上一页
      </button>
      <button disabled={page * size >= count} onClick={() => change(page + 1)}>
        下一页
      </button>
    </div>
  );
}
export function Story({ e }: { e: EventRow }) {
  return (
    <article className="story">
      <div className="meta">
        <span className="tag">{e.category}</span>
        <span>{e.media.map((m) => m.name).join("、")}</span>
        {e.auto && <span>AI 自动入库</span>}
      </div>
      <a className="story-title" href={"/news/" + e.id}>
        {e.title}
      </a>
      {e.summary && <p>{e.summary}</p>}
      <small>
        {e.occurred || "发生日待核验"} · {e.articles.length} 份来源
      </small>
    </article>
  );
}
export const states: Dict = {
  pending: "待审核",
  approved: "已入库",
  rejected: "已退回",
  filtered: "已过滤",
  draft: "草稿",
  published: "已签发",
  archived: "已归档",
  queued: "排队中",
  running: "采集中",
  done: "已完成",
  failed: "失败",
  manual: "需人工巡检",
  inspected: "已巡检",
};
