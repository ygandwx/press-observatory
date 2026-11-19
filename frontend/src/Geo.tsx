import React, { useState } from "react";
import {
  Empty,
  Field,
  Heading,
  Pager,
  Wait,
  query,
  url,
  useData,
} from "./shared";
import type { Page, Session } from "./shared";

export type TopicEntry = {
  id: number;
  title: string;
  summary: string;
  date: string;
  date_kind: string;
  category: string;
  media: { id: number; name: string }[];
  matches: { term: string; fields: string[]; source_urls?: string[] }[];
  sources: { title: string; url: string }[];
};

export function Geo({ s }: { s: Session }) {
  const [filters, setFilters] = useState({
    media: query.media || "",
    category: query.category || "",
    q: query.q || "",
    start: query.start || "",
    end: query.end || "",
    page: query.page || "1",
  });
  const result = useData<
    Page<TopicEntry> & { media: { id: number; name: string }[] }
  >(url("geo", filters));
  const change = (key: keyof typeof filters, value: string) => {
    const next = { ...filters, page: "1", [key]: value };
    setFilters(next);
    window.history.replaceState(null, "", url("/geo", next));
  };
  return (
    <>
      <Heading
        title="GEO 专题"
        sub="生成式引擎优化事件时间线。按发生日排序，发生日待核的事件使用入库日。"
      />
      <section className="panel filters">
        <Field
          label="搜索专题"
          value={filters.q}
          onChange={(v) => change("q", v)}
        />
        <label>
          媒体机构
          <select
            value={filters.media}
            onChange={(e) => change("media", e.target.value)}
          >
            <option value="">全部机构</option>
            {result.data?.media.map((m) => (
              <option key={m.id} value={m.id}>
                {m.name}
              </option>
            ))}
          </select>
        </label>
        <label>
          事件类别
          <select
            value={filters.category}
            onChange={(e) => change("category", e.target.value)}
          >
            <option value="">全部类别</option>
            {Object.keys(s.catalog.events).map((c) => (
              <option key={c}>{c}</option>
            ))}
          </select>
        </label>
        <Field
          label="开始日期"
          type="date"
          value={filters.start}
          onChange={(v) => change("start", v)}
        />
        <Field
          label="结束日期"
          type="date"
          value={filters.end}
          onChange={(v) => change("end", v)}
        />
      </section>
      {!result.data || result.error ? (
        <Wait error={result.error} retry={result.refresh} />
      ) : (
        <section className="panel">
          <h2>专题时间线 · {result.data.count} 件</h2>
          {!result.data.count && <Empty>当前条件下无已核实的 GEO 事件</Empty>}
          {result.data.items.map((item) => (
            <article key={item.id} className="reference">
              <small>
                {item.date || "日期待核"} · {item.date_kind} · {item.category}
              </small>
              <h3>
                <a href={"/news/" + item.id}>{item.title}</a>
              </h3>
              {item.summary && <p>{item.summary}</p>}
              <p>
                {item.media.map((m) => (
                  <a key={m.id} href={"/archives/" + m.id}>
                    {m.name}　
                  </a>
                ))}
              </p>
              <p>
                {item.matches
                  .map((hit) => hit.term + "（" + hit.fields.join("、") + "）")
                  .join(" · ")}
              </p>
              {item.sources.map((source) => (
                <a
                  key={source.url}
                  href={source.url}
                  target="_blank"
                  rel="noreferrer"
                >
                  {source.title || "原文"} ↗{" "}
                  {item.matches
                    .filter((hit) => hit.source_urls?.includes(source.url))
                    .map((hit) => hit.term)
                    .join("、")}
                </a>
              ))}
            </article>
          ))}
          <Pager {...result.data} change={(p) => change("page", String(p))} />
        </section>
      )}
    </>
  );
}
