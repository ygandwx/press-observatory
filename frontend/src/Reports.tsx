import React, { useRef, useState } from "react";
import { toBlob } from "html-to-image";
import {
  Heading,
  Wait,
  Empty,
  Notice,
  Pager,
  Field,
  day,
  query,
  url,
  request,
  states,
  useData,
  useAction,
} from "./shared";
import type { Session, Page } from "./shared";
type Entry = {
  id: number;
  title: string;
  summary: string;
  category: string;
  media: string[];
  sources: string[];
};
type Content = {
  start: string;
  end: string;
  headline: string;
  key_changes: string[];
  categories: Record<string, string[]>;
  special: Record<string, string[]>;
  snapshot: Record<string, number>;
  events: Entry[];
};
type Report = {
  id: number;
  kind: string;
  day: string;
  version: number;
  issuer: string;
  issued: string;
  state: string;
  share?: string;
  headline?: string;
  content: Content;
  history?: { version: number; issuer: string; issued: string }[];
};
export function Reports({ s, weekly }: { s: Session; weekly: boolean }) {
  const [date, setDate] = useState(query.day || day()),
    [state, setState] = useState(query.state || ""),
    [month, setMonth] = useState(""),
    [search, setSearch] = useState(""),
    [page, setPage] = useState(1),
    [history, toggle] = useState(weekly || query.history === "1");
  const kind = weekly ? "weekly" : "daily";
  const latest = useData<Page<Report>>(
    weekly ? "reports?kind=weekly&state=published&size=1" : null,
  );
  const d = useData<Page<Report>>(
      url("reports", {
        kind,
        day: history ? "" : date,
        state,
        month,
        q: search,
        page: String(page),
      }),
    ),
    a = useAction();
  const path = weekly ? "/briefings" : "/summary";
  const gen = () =>
    a.run(async () => {
      const r = await request<Report>("reports", { kind, day: date });
      location.href = path + "/" + r.id;
    });
  return (
    <>
      <Heading
        title={weekly ? "每周简报" : "纸媒业态日报"}
        sub={
          weekly
            ? "领导参阅版与业务详版共用同一批事件"
            : "按日期查阅关键变化、机构改革与行业动态"
        }
      >
        {!weekly && (
          <button
            onClick={() => {
              toggle(!history);
              setPage(1);
            }}
          >
            {history ? "按日查看" : "历史日报"}
          </button>
        )}
      </Heading>
      <div className="panel filters">
        <Field
          type="date"
          label={weekly ? "选择已结束的一周" : "业务日期"}
          value={date}
          onChange={(v) => {
            setDate(v);
            setPage(1);
          }}
        />
        {s.rights.includes("edit") && (
          <button className="primary" disabled={a.busy} onClick={gen}>
            {weekly ? "生成该周简报" : "生成或打开当期"}
          </button>
        )}
        {history && (
          <>
            <Field
              label="月份"
              type="month"
              value={month}
              onChange={(v) => {
                setMonth(v);
                setPage(1);
              }}
            />
            <Field
              label="搜索结论"
              value={search}
              onChange={(v) => {
                setSearch(v);
                setPage(1);
              }}
            />
            <label>
              状态
              <select
                value={state}
                onChange={(e) => {
                  setState(e.target.value);
                  setPage(1);
                }}
              >
                <option value="">全部</option>
                {["draft", "pending", "published", "archived"].map((k) => (
                  <option key={k} value={k}>
                    {states[k]}
                  </option>
                ))}
              </select>
            </label>
          </>
        )}
      </div>
      <Notice text={a.notice} />
      {weekly && latest.data?.items[0] && (
        <section className="panel">
          <h2>最新发布</h2>
          <a
            className="story-title"
            href={"/briefings/" + latest.data.items[0].id}
          >
            第 {latest.data.items[0].id} 期 · {latest.data.items[0].day}
          </a>
          <p>{latest.data.items[0].headline}</p>
        </section>
      )}
      {!d.data || d.error ? (
        <Wait error={d.error} retry={d.refresh} />
      ) : (
        <section className="panel">
          <h2>{weekly ? "历史简报" : history ? "日报档案" : "当期日报"}</h2>
          {!d.data.count && (
            <Empty>
              {weekly ? "当前条件下暂无周报" : "当前条件下暂无日报"}
            </Empty>
          )}
          {d.data.items.map((r) => (
            <div className="row" key={r.id}>
              <div className="grow">
                <a className="story-title" href={path + "/" + r.id}>
                  {weekly ? "全国纸媒业态每周简报" : "纸媒业态日报"} · 第 {r.id}{" "}
                  期
                </a>
                <p>{r.headline}</p>
                <small>
                  {r.day} · {r.issuer || "尚未签发"}
                </small>
              </div>
              <span className="tag">{states[r.state]}</span>
            </div>
          ))}
          <Pager {...d.data} change={setPage} />
        </section>
      )}
    </>
  );
}
function Edit({ r, finish }: { r: Report; finish: () => void }) {
  const [headline, setHeadline] = useState(r.content.headline),
    [key, setKey] = useState(r.content.key_changes.join("\n")),
    [cats, setCats] = useState(r.content.categories),
    [spec, setSpec] = useState(r.content.special),
    a = useAction();
  return (
    <form
      className="panel"
      onSubmit={(e) => {
        e.preventDefault();
        void a.run(async () => {
          await request("reports/" + r.id, {
            action: "edit",
            version: r.version,
            headline,
            key_changes: key.split("\n").filter(Boolean),
            categories: cats,
            special: spec,
          });
          finish();
        });
      }}
    >
      <h2>编辑草稿</h2>
      <Field
        label="一句话结论"
        value={headline}
        onChange={setHeadline}
        required
      />
      <label>
        关键变化（每行一条，最多五条）
        <textarea value={key} onChange={(e) => setKey(e.target.value)} />
      </label>
      {[cats, spec].map((group, i) => (
        <fieldset key={i}>
          <legend>{i ? "专项清单" : "分类摘要"}</legend>
          {Object.entries(group).map(([k, arr]) => (
            <label key={k}>
              {k}
              <textarea
                value={arr.join("\n")}
                onChange={(e) =>
                  (i ? setSpec : setCats)({
                    ...group,
                    [k]: e.target.value.split("\n"),
                  })
                }
              />
            </label>
          ))}
        </fieldset>
      ))}
      <div className="actions">
        <button disabled={a.busy} className="primary">
          保存
        </button>
        <button type="button" onClick={finish}>
          取消
        </button>
      </div>
      <Notice text={a.notice} />
    </form>
  );
}
export function ReportDetail({
  s,
  id,
  token,
}: {
  s?: Session;
  id?: string;
  token?: string;
}) {
  const [edition, setEdition] = useState(""),
    [mode, setMode] = useState("领导参阅版"),
    [editing, edit] = useState(false),
    a = useAction(),
    paper = useRef<HTMLElement>(null);
  const d = useData<Report>(
    token ? "shared/" + token : url("reports/" + id, { edition }),
  );
  if (!d.data || d.error) return <Wait error={d.error} retry={d.refresh} />;
  const r = d.data,
    c = r.content,
    weekly = r.kind === "weekly",
    canEdit = !edition && s?.rights.includes("edit"),
    sign = !edition && s?.rights.includes("sign");
  const call = (action: string) =>
    a.run(async () => {
      await request("reports/" + id, { action, version: r.version });
      d.refresh();
    });
  const list = (items: string[], empty: string) =>
    items.length ? (
      <ul>
        {items.map((v, i) => (
          <li key={i}>{v}</li>
        ))}
      </ul>
    ) : (
      <p className="muted">{empty}</p>
    );
  return (
    <>
      <div className="actions tools">
        {!token && (
          <a href={weekly ? "/briefings" : "/summary?history=1"}>← 历史归档</a>
        )}
        <button
          onClick={() =>
            a.run(async () => {
              await navigator.clipboard.writeText(
                paper.current?.innerText || "",
              );
            }, "已复制全文")
          }
        >
          复制全文
        </button>
        <button
          disabled={a.busy}
          onClick={() =>
            a.run(async () => {
              if (paper.current)
                await (
                  await import("./pdf")
                ).savePaper(paper.current, "纸媒业态-" + r.day);
            }, "PDF 已生成")
          }
        >
          导出 PDF
        </button>
        <button
          onClick={() =>
            a.run(async () => {
              if (!paper.current) return;
              const blob = await toBlob(paper.current, {
                backgroundColor: "#fff",
                pixelRatio: 1.5,
              });
              const link = document.createElement("a");
              link.download = "纸媒业态-" + r.day + ".png";
              if (!blob) throw new Error("长图生成失败。");
              const href = URL.createObjectURL(blob);
              link.href = href;
              document.body.appendChild(link);
              link.click();
              link.remove();
              setTimeout(() => URL.revokeObjectURL(href), 60000);
            }, "长图已生成")
          }
        >
          导出长图
        </button>
        {canEdit && r.state === "draft" && (
          <>
            <button onClick={() => edit(!editing)}>编辑</button>
            <button
              onClick={() =>
                a.run(async () => {
                  await request("reports", {
                    kind: r.kind,
                    day: r.day,
                    version: r.version,
                    regenerate: true,
                  });
                  d.refresh();
                })
              }
            >
              重新生成
            </button>
            <button onClick={() => call("submit")}>提交签发</button>
          </>
        )}
        {sign && ["draft", "pending"].includes(r.state) && (
          <button
            className="red"
            disabled={a.busy}
            onClick={() => call("issue")}
          >
            签发
          </button>
        )}
        {sign && ["published", "archived"].includes(r.state) && (
          <>
            <button onClick={() => call(r.share ? "revoke" : "share")}>
              {r.share ? "撤销分享" : "生成只读链接"}
            </button>
            <button onClick={() => call("correct")}>创建更正稿</button>
            {r.state === "published" && (
              <button onClick={() => call("archive")}>归档</button>
            )}
          </>
        )}
        {r.share && (
          <a href={"/share/" + r.share} target="_blank" rel="noreferrer">
            打开只读链接 ↗
          </a>
        )}
      </div>
      <Notice text={a.notice} />
      {editing && canEdit && (
        <Edit
          r={r}
          finish={() => {
            edit(false);
            d.refresh();
          }}
        />
      )}
      {weekly && (
        <div className="seg">
          {["领导参阅版", "业务详版"].map((m) => (
            <button
              key={m}
              aria-pressed={m === mode}
              onClick={() => setMode(m)}
            >
              {m}
            </button>
          ))}
        </div>
      )}
      <article className="paper" ref={paper}>
        <div className="paperhead">
          <small>报业观察站 · 全国纸媒业态观测平台</small>
          <h1>{weekly ? "全国纸媒业态每周简报" : "纸媒业态日报"}</h1>
          <p>
            第 {r.id} 期　{c.start}
            {weekly ? " 至 " + c.end : ""}　{weekly ? mode : states[r.state]}
          </p>
          <small>{r.issuer ? "签发人 " + r.issuer : "尚未签发"}</small>
        </div>
        <blockquote>{c.headline}</blockquote>
        <h2>一、关键变化</h2>
        {list(c.key_changes, "本期暂无关键变化")}
        <h2>二、分类摘要</h2>
        {Object.entries(c.categories).map(([k, v]) => (
          <div key={k}>
            <h3>{k}</h3>
            {list(v, "本期暂无该类动态")}
          </div>
        ))}
        <h2>三、专项清单</h2>
        {Object.entries(c.special).map(([k, v]) => (
          <div key={k}>
            <h3>
              {k} · {v.length}
            </h3>
            {v.length > 0 && list(v, "")}
          </div>
        ))}
        <h2>数据快照</h2>
        <div className="snapshot">
          {Object.entries(c.snapshot).map(([k, v]) => (
            <div key={k}>
              <strong>{v}</strong>
              <small>{k}</small>
            </div>
          ))}
        </div>
        <h2>{weekly && mode === "业务详版" ? "事件明细与原文" : "事件依据"}</h2>
        {c.events.map((e) => (
          <div className="reference" key={e.id}>
            <b>{e.title}</b>
            {weekly && mode === "业务详版" && (
              <p>
                {e.media.join("、")} · {e.category}　{e.summary}
              </p>
            )}
            <div>
              {e.sources.map((x, i) => (
                <a key={x} href={x} rel="noreferrer" target="_blank">
                  原文 {i + 1} ↗　
                </a>
              ))}
            </div>
          </div>
        ))}
        {!c.events.length && <p>本期无引用事件</p>}
        <footer>数据截至生成时刻 · 仅供参阅</footer>
      </article>
      {!!r.history?.length && (
        <label className="edition">
          签发版本
          <select value={edition} onChange={(e) => setEdition(e.target.value)}>
            <option value="">当前稿</option>
            {r.history.map((h) => (
              <option key={h.version} value={h.version}>
                版本 {h.version} · {h.issuer}
              </option>
            ))}
          </select>
        </label>
      )}
    </>
  );
}
