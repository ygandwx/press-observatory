import React, { useState } from "react";
import {
  Heading,
  Select,
  Field,
  Empty,
  Notice,
  Wait,
  Pager,
  Story,
  query,
  url,
  useData,
  useAction,
  request,
  states,
  api,
} from "./shared";
import type { Session, Dict, EventRow, Page } from "./shared";
export function News({
  s,
  pending = false,
}: {
  s: Session;
  pending?: boolean;
}) {
  const [f, setF] = useState<Dict>(
      pending ? { state: "pending" } : { ...query },
    ),
    [ai, setAi] = useState(""),
    [progress, setProgress] = useState(""),
    [selected, setSelected] = useState<number[]>([]),
    [reason, setReason] = useState("");
  const [active, setActive] = useState<Dict>(
      pending ? { state: "pending" } : { ...query },
    ),
    d = useData<Page<EventRow>>(url("events", active)),
    a = useAction();
  const change = (key: string, val: string) => setF({ ...f, [key]: val });
  const apply = (newFilters: Dict) => {
    setSelected([]);
    setActive(newFilters);
    setF(newFilters);
    if (!pending) history.pushState(null, "", url("/news", newFilters));
  };
  const selectOptions: Record<string, [string, string[]]> = {
    category: ["事件类别", Object.keys(s.catalog.events)],
    primary: ["一级品类", Object.keys(s.catalog.categories)],
    secondary: [
      "二级子类",
      f.primary
        ? s.catalog.categories[f.primary] || []
        : Object.values(s.catalog.categories).flat(),
    ],
    importance: ["重要度", s.catalog.importance],
    focus: ["关注等级", s.catalog.focus],
  };
  async function review(action: string) {
    const items = d.data?.items
      .filter((e) => selected.includes(e.id))
      .map((e) => ({ id: e.id, version: e.version }));
    await request("events/review", { items, reason, action });
    setSelected([]);
    d.refresh();
  }
  return (
    <>
      <Heading
        title={pending ? "待审核队列" : "动态中心"}
        sub={
          pending
            ? "核验来源后可逐条或批量处理"
            : "按机构、事件类别与日期追踪行业变化"
        }
      >
        <a
          className="button"
          href={
            api +
            url("events", {
              ...active,
              export: "csv",
              ...(selected.length ? { ids: selected.join(",") } : {}),
            })
          }
        >
          导出 CSV
        </a>
      </Heading>
      {s.rights.includes("edit") && (
        <form
          className="searchbar"
          onSubmit={(e) => {
            e.preventDefault();
            void a.run(async () => {
              const filters = { ...f, result: "", ids: "", page: "1" };
              type Batch = {
                search: string;
                scanned: number;
                total: number;
                complete: boolean;
                snapshot: string;
              };
              setProgress("正在检索全部符合条件的事件…");
              try {
                let r = await request<Batch>("events/search", {
                  q: ai,
                  filters,
                  incremental: true,
                });
                while (!r.complete) {
                  setProgress(
                    "正在检索 " + r.scanned + " / " + r.total + " 件",
                  );
                  r = await request<Batch>("events/search", {
                    search: r.search,
                    cursor: r.scanned,
                    incremental: true,
                  });
                }
                apply({ ...filters, result: r.snapshot });
                setProgress("已检索全部 " + r.total + " 件候选事件");
              } catch (error) {
                setProgress("");
                throw error;
              }
            }, "语义检索完成");
          }}
        >
          <input
            aria-label="语义检索问题"
            placeholder="搜索「最近有哪些报社改版？」"
            value={ai}
            onChange={(e) => setAi(e.target.value)}
          />
          <button disabled={!ai || a.busy}>AI 搜索</button>
        </form>
      )}
      <form
        className="panel filters"
        onSubmit={(e) => {
          e.preventDefault();
          apply({ ...f, page: "1", ids: "", result: "" });
        }}
      >
        <Field
          label="标题或媒体"
          value={f.q || ""}
          onChange={(v) => change("q", v)}
        />
        {Object.entries(selectOptions).map(([k, [label, opts]]) => (
          <Select
            key={k}
            label={label}
            value={f[k] || ""}
            options={opts}
            onChange={(v) => change(k, v)}
          />
        ))}
        <Field
          label="地区"
          value={f.region || ""}
          onChange={(v) => change("region", v)}
        />
        <Field
          label="起始日期"
          type="date"
          value={f.start || ""}
          onChange={(v) => change("start", v)}
        />
        <Field
          label="结束日期"
          type="date"
          value={f.end || ""}
          onChange={(v) => change("end", v)}
        />
        <label>
          日期口径
          <select
            value={f.clock || "occurred"}
            onChange={(e) => change("clock", e.target.value)}
          >
            <option value="occurred">事件发生</option>
            <option value="approved">首次入库</option>
          </select>
        </label>
        <label className="check">
          <input
            type="checkbox"
            checked={f.star === "1"}
            onChange={(e) => change("star", e.target.checked ? "1" : "")}
          />
          仅看收藏
        </label>
        <div className="actions">
          <button className="primary">筛选</button>
          <a
            className="button"
            href={pending ? "/monitor?tab=待审核队列" : "/news"}
          >
            重置
          </a>
        </div>
      </form>
      <Notice text={progress} />
      <Notice text={a.notice} />
      {(active.snapshot || active.result) && (
        <p className="muted">
          正在查看生成时保存的列表。{" "}
          <a
            href={url("/news", {
              ...active,
              snapshot: "",
              result: "",
              page: "1",
            })}
          >
            查看实时列表
          </a>
        </p>
      )}
      {!d.data || d.error ? (
        <Wait error={d.error} retry={d.refresh} />
      ) : (
        <section className="panel">
          <div className="section-title">
            <h2>
              {states[active.state || "approved"]}动态 · {d.data.count}
            </h2>
            {!!d.data.items.length && (
              <label className="check">
                <input
                  type="checkbox"
                  checked={selected.length === d.data.items.length}
                  onChange={(e) =>
                    setSelected(
                      e.target.checked ? d.data!.items.map((x) => x.id) : [],
                    )
                  }
                />
                全选本页
              </label>
            )}
          </div>
          {s.rights.includes("review") &&
            !active.snapshot &&
            !active.result &&
            active.state === "pending" && (
              <div className="actions">
                <input
                  aria-label="退回原因"
                  placeholder="退回原因"
                  value={reason}
                  onChange={(e) => setReason(e.target.value)}
                />
                <button
                  disabled={!selected.length || a.busy}
                  onClick={() => a.run(() => review("approve"), "已入库")}
                >
                  批量入库
                </button>
                <button
                  disabled={!selected.length || !reason.trim() || a.busy}
                  onClick={() => a.run(() => review("reject"), "已退回")}
                >
                  批量退回
                </button>
              </div>
            )}
          {d.data.items.map((e) => (
            <div className="selectable" key={e.id}>
              <input
                aria-label={"选择 " + e.title}
                type="checkbox"
                checked={selected.includes(e.id)}
                onChange={(x) =>
                  setSelected(
                    x.target.checked
                      ? [...selected, e.id]
                      : selected.filter((n) => n !== e.id),
                  )
                }
              />
              <Story e={e} />
            </div>
          ))}
          {!d.data.count && <Empty>没有符合条件的动态</Empty>}
          <Pager
            {...d.data}
            change={(n) => apply({ ...active, page: String(n) })}
          />
        </section>
      )}
    </>
  );
}
export function EventDetail({ s, id }: { s: Session; id: string }) {
  const d = useData<EventRow>("events/" + id),
    a = useAction(),
    [target, setTarget] = useState(""),
    [reason, setReason] = useState("");
  if (!d.data || d.error) return <Wait error={d.error} retry={d.refresh} />;
  const e = d.data;
  const act = (body: unknown, path = "events/" + id) =>
    a.run(async () => {
      await request(path, body);
      d.refresh();
    });
  return (
    <>
      <a href="/news">← 动态中心</a>
      <Heading
        title={e.title}
        sub={[
          e.category,
          e.subtype,
          e.occurred || "日期待核验",
          states[e.state],
        ]
          .filter(Boolean)
          .join(" · ")}
      >
        {s.rights.includes("edit") && (
          <button onClick={() => act({ action: "star", on: !e.starred })}>
            {e.starred ? "取消收藏" : "☆ 收藏"}
          </button>
        )}
      </Heading>
      <Notice text={a.notice} />
      {s.rights.includes("edit") &&
        ["pending", "rejected"].includes(e.state) && (
          <details className="panel">
            <summary>编辑并提交复核</summary>
            <form
              className="filters"
              key={e.version}
              onSubmit={(x) => {
                x.preventDefault();
                const values = Object.fromEntries(
                  new FormData(x.currentTarget),
                );
                act({ ...values, action: "edit", version: e.version });
              }}
            >
              <label>
                标题
                <input name="title" required defaultValue={e.title} />
              </label>
              <label>
                事件日期
                <input
                  type="date"
                  name="occurred"
                  defaultValue={e.occurred || ""}
                />
              </label>
              <label>
                事件大类
                <select name="category" defaultValue={e.category}>
                  {Object.keys(s.catalog.events).map((k) => (
                    <option key={k}>{k}</option>
                  ))}
                </select>
              </label>
              <label>
                子项
                <input
                  name="subtype"
                  defaultValue={e.subtype || ""}
                  list="subtypes"
                />
                <datalist id="subtypes">
                  {Object.values(s.catalog.events)
                    .flat()
                    .map((v) => (
                      <option key={v}>{v}</option>
                    ))}
                </datalist>
              </label>
              <label>
                重要度
                <select name="importance" defaultValue={e.importance}>
                  {s.catalog.importance.map((k) => (
                    <option key={k}>{k}</option>
                  ))}
                </select>
              </label>
              <label className="wide">
                摘要
                <textarea name="summary" defaultValue={e.summary} />
              </label>
              <button disabled={a.busy}>保存待审稿</button>
            </form>
          </details>
        )}
      <section className="panel">
        <h2>动态摘要</h2>
        <p>{e.summary || "待编辑补充"}</p>
        <h3>关联媒体档案</h3>
        <div className="actions">
          {e.media.map((m) => (
            <a key={m.id} href={"/archives/" + m.id}>
              {m.name} ↗
            </a>
          ))}
        </div>
        <h3>原文与来源状态</h3>
        {e.articles.map((x) => (
          <div className="row" key={x.id}>
            <a href={x.url} target="_blank" rel="noreferrer">
              {x.title} ↗
            </a>
            <span className="tag">{x.status}</span>
            {s.rights.includes("review") && (
              <button
                disabled={a.busy}
                onClick={() => act({ action: "verify", article: x.id })}
              >
                核验链接
              </button>
            )}
          </div>
        ))}
        <small>
          录入人 {e.creator} · {e.created?.slice(0, 10)}
        </small>
      </section>
      {e.reasoning && (
        <section className="panel">
          <h2>判定依据</h2>
          <p>{e.reasoning}</p>
        </section>
      )}
      {s.rights.includes("review") && (
        <section className="panel">
          <h2>审核与来源归并</h2>
          {e.state === "approved" ? (
            <div className="actions">
              <input
                aria-label="目标事件编号"
                placeholder="目标事件编号"
                value={target}
                onChange={(x) => setTarget(x.target.value)}
              />
              <button
                disabled={!target || a.busy}
                onClick={() =>
                  a.run(async () => {
                    await request("events/" + id, {
                      action: "merge",
                      target,
                      version: e.version,
                    });
                    location.href = "/news/" + target;
                  })
                }
              >
                归并至该事件
              </button>
              {e.merged.map((c) => (
                <button
                  key={c.id}
                  onClick={() =>
                    act(
                      { action: "split", target: e.id, version: c.version },
                      "events/" + c.id,
                    )
                  }
                >
                  拆回 · {c.title}
                </button>
              ))}
            </div>
          ) : (
            <div className="actions">
              <input
                placeholder="退回原因"
                value={reason}
                onChange={(x) => setReason(x.target.value)}
              />
              {["approve", "reject"].map((action) => (
                <button
                  key={action}
                  disabled={a.busy || (action === "reject" && !reason)}
                  onClick={() =>
                    act(
                      {
                        action,
                        reason,
                        items: [{ id: e.id, version: e.version }],
                      },
                      "events/review",
                    )
                  }
                >
                  {action === "approve" ? "批准入库" : "退回"}
                </button>
              ))}
            </div>
          )}
          <h3>操作记录</h3>
          {e.history?.map((h, i) => (
            <p key={i}>
              {h.at.slice(0, 16)} · {h.actor} · {states[h.action] || h.action}{" "}
              {h.detail && typeof h.detail === "object" && "reason" in h.detail
                ? String(h.detail.reason)
                : ""}
            </p>
          ))}
        </section>
      )}
    </>
  );
}
