import React, { useState } from "react";
import {
  Wait,
  Empty,
  Notice,
  Pager,
  Field,
  Select,
  Story,
  url,
  query,
  request,
  useAction,
  useData,
} from "./shared";
import type { Session, Media, Page, Dict } from "./shared";
function MediaForm({
  s,
  initial,
  done,
}: {
  s: Session;
  initial?: Media;
  done: () => void;
}) {
  const [f, set] = useState<Dict>(
      Object.fromEntries(
        [
          "name",
          "owner",
          "level",
          "category",
          "subcategory",
          "region",
          "kind",
          "focus",
          "reference",
          "digest_section",
          "description",
          "archive_status",
        ].map((k) => [
          k,
          initial ? String(initial[k as keyof Media] || "") : "",
        ]),
      ),
    ),
    [priority, setPriority] = useState(initial?.high_priority || false),
    [tags, setTags] = useState(initial?.tags.join("、") || ""),
    [channels, setChannels] = useState<Dict>({ ...initial?.channels }),
    a = useAction();
  const setter = (k: string, v: string) =>
    set({
      ...f,
      [k]: v,
      ...(k === "category" ? { subcategory: "", digest_section: "" } : {}),
    });
  return (
    <form
      className="panel filters"
      onSubmit={(e) => {
        e.preventDefault();
        void a.run(async () => {
          await request(
            "media" + (initial ? "/" + initial.id : ""),
            {
              ...f,
              high_priority: priority,
              tags: tags
                .split(/[、,]/)
                .map((t) => t.trim())
                .filter(Boolean),
              channels,
            },
            initial ? "PATCH" : "POST",
          );
          done();
        });
      }}
    >
      {Object.entries({
        name: "媒体名称",
        owner: "主管主办单位",
        region: "地区",
        kind: "媒体类型",
        reference: "资料出处",
        description: "定位简介",
        archive_status: "档案状态",
      }).map(([k, v]) => (
        <Field
          key={k}
          label={v}
          required={k === "name"}
          value={f[k]}
          onChange={(x) => setter(k, x)}
        />
      ))}
      {Object.entries({
        level: ["媒体层级", s.catalog.levels],
        category: ["一级分类", Object.keys(s.catalog.categories)],
        subcategory: ["二级子类", s.catalog.categories[f.category] || []],
        focus: ["关注等级", s.catalog.focus],
      } as Record<string, [string, string[]]>).map(([k, [label, options]]) => (
        <Select
          key={k}
          {...{ label, options }}
          empty="请选择"
          value={f[k]}
          onChange={(v) => setter(k, v)}
        />
      ))}
      {f.category === "头部市场化纸媒" && (
        <Select
          label="日报归栏（据资料出处核验）"
          empty="待核验"
          options={s.catalog.market_sections}
          value={f.digest_section || ""}
          onChange={(v) => setter("digest_section", v)}
        />
      )}
      <Field label="垂直领域（顿号分隔）" value={tags} onChange={setTags} />
      <label className="check">
        <input
          type="checkbox"
          checked={priority}
          onChange={(e) => setPriority(e.target.checked)}
        />
        高优先级标注
      </label>
      {s.catalog.channels.map((c) => (
        <Field
          key={c}
          label={c}
          value={channels[c] || ""}
          onChange={(v) => setChannels({ ...channels, [c]: v })}
        />
      ))}
      <Notice text={a.notice} />
      <div className="actions">
        <button className="primary" disabled={a.busy}>
          保存档案
        </button>
        <button type="button" onClick={done}>
          取消
        </button>
      </div>
    </form>
  );
}

function missing(m: Media) {
  return ["官网", "客户端", "官方公众号", "视频号", "海外账号"].filter(
    (k) => !m.channels[k],
  ).length;
}
function Category({ name }: { name: string }) {
  return (
    <span
      className={
        "chip " +
        (name.startsWith("中央")
          ? "central"
          : name.startsWith("头部")
            ? "market"
            : "")
      }
    >
      {name || "—"}
    </span>
  );
}
type GroupCount = { category: string; subcategory: string; count: number };
export function Archives({ s }: { s: Session }) {
  const [active, setActive] = useState<Dict>({ ...query }),
    [f, set] = useState<Dict>({ ...query }),
    [add, setAdd] = useState(false);
  const [compare, setCompare] = useState<number[]>([]),
    [show, setShow] = useState(false);
  const tab = active.tab || "list",
    maintain = s.rights.includes("edit");
  const params = {
    ...active,
    focus: tab === "core" ? "核心必看" : active.focus || "",
  };
  const data = useData<Page<Media>>(
    tab === "board" ? null : url("media", params),
  );
  const board = useData<{ groups: GroupCount[] }>(
    tab === "board" ? url("media", { ...params, view: "board" }) : null,
  );
  const apply = (vals: Dict) => {
    set(vals);
    setActive(vals);
    history.replaceState(null, "", url("/archives", vals));
  };
  const change = (k: string, v: string) =>
    set({ ...f, [k]: v, ...(k === "category" ? { subcategory: "" } : {}) });
  const toggle = (id: number) => {
    setCompare(
      compare.includes(id) ? compare.filter((n) => n !== id) : [...compare, id],
    );
    setShow(false);
  };
  const cols = [
    "媒体名称",
    "一级分类",
    "二级子类",
    "垂直领域",
    "媒体层级",
    "地区",
    "关注等级",
    "档案状态",
    "操作",
  ];
  return (
    <div className="archive-page">
      <div className="archive-title">
        <h1>媒体档案</h1>
        {maintain && (
          <button className="primary" onClick={() => setAdd(!add)}>
            ＋ 新增档案
          </button>
        )}
      </div>
      {add && (
        <MediaForm
          s={s}
          done={() => {
            setAdd(false);
            data.refresh();
            board.refresh();
          }}
        />
      )}
      <form
        className="panel archive-filters"
        onSubmit={(e) => {
          e.preventDefault();
          apply({ ...f, page: "1" });
        }}
      >
        <Field
          label="搜索"
          value={f.q || ""}
          onChange={(v) => change("q", v)}
        />
        <Select
          label="一级分类"
          value={f.category || ""}
          options={Object.keys(s.catalog.categories)}
          onChange={(v) => change("category", v)}
        />
        <Select
          label="二级子类"
          value={f.subcategory || ""}
          options={
            f.category
              ? s.catalog.categories[f.category] || []
              : Object.values(s.catalog.categories).flat()
          }
          onChange={(v) => change("subcategory", v)}
        />
        <Field
          label="垂直领域"
          value={f.tag || ""}
          onChange={(v) => change("tag", v)}
        />
        <Select
          label="媒体层级"
          value={f.level || ""}
          options={s.catalog.levels}
          onChange={(v) => change("level", v)}
        />
        <Field
          label="地区"
          value={f.region || ""}
          onChange={(v) => change("region", v)}
        />
        <Select
          label="关注等级"
          value={f.focus || ""}
          options={s.catalog.focus}
          onChange={(v) => change("focus", v)}
        />
        <Field
          label="媒体类型"
          value={f.kind || ""}
          onChange={(v) => change("kind", v)}
        />
        {maintain && (
          <label className="check">
            <input
              type="checkbox"
              checked={f.following === "1"}
              onChange={(e) => change("following", e.target.checked ? "1" : "")}
            />
            仅看关注
          </label>
        )}
        <div className="actions">
          <button>筛选</button>
          <button type="button" onClick={() => apply({ tab })}>
            重置
          </button>
        </div>
      </form>
      <div className="archive-tabs" role="tablist">
        {[
          ["list", "总表检索"],
          ["board", "分类看板"],
          ["core", "核心追踪"],
        ].map(([key, title]) => (
          <button
            role="tab"
            aria-selected={tab === key}
            key={key}
            onClick={() => apply({ ...active, tab: key, page: "1" })}
          >
            {title}
          </button>
        ))}
      </div>
      {active.snapshot && (
        <p>
          正在查看生成时保存的机构名册。 <a href="/archives">查看实时名册</a>
        </p>
      )}
      {tab === "board" ? (
        !board.data || board.error ? (
          <Wait error={board.error} retry={board.refresh} />
        ) : (
          <div className="archive-board">
            {[...new Set(board.data.groups.map((g) => g.category))].map(
              (cat) => {
                const list = board.data!.groups.filter(
                  (g) => g.category === cat,
                );
                return (
                  <section className="panel" key={cat}>
                    <h2>{cat || "待分类"}</h2>
                    <p>共 {list.reduce((n, g) => n + g.count, 0)} 家媒体</p>
                    {list.map((g) => (
                      <button
                        className="board-row"
                        key={g.subcategory}
                        onClick={() =>
                          apply({
                            ...active,
                            tab: "list",
                            category: cat,
                            subcategory: g.subcategory,
                            page: "1",
                          })
                        }
                      >
                        <span>{g.subcategory || "未填子类"}</span>
                        <b>{g.count} 家　›</b>
                      </button>
                    ))}
                  </section>
                );
              },
            )}
            {!board.data.groups.length && <Empty>暂无匹配的机构档案</Empty>}
          </div>
        )
      ) : !data.data || data.error ? (
        <Wait error={data.error} retry={data.refresh} />
      ) : (
        <>
          <div className="archive-tools">
            <h2>机构名册 · {data.data.count}</h2>
            {tab === "list" && (
              <button
                disabled={compare.length < 2}
                onClick={() => setShow(!show)}
              >
                {show ? "关闭对比" : "同业对比"}（{compare.length}）
              </button>
            )}
          </div>
          {show && (
            <div className="comparison">
              {compare.map((id) => (
                <Compare key={id} id={id} />
              ))}
            </div>
          )}
          <div className={tab === "core" ? "core-grid" : "directory-scroll"}>
            {tab === "core" ? (
              data.data.items.map((m) => (
                <article key={m.id} className="panel">
                  <h3>
                    <a href={"/archives/" + m.id}>{m.name}</a>
                  </h3>
                  <span className="chip focus">{m.focus}</span>
                  <p>{m.owner}</p>
                  <Category name={m.category} />
                  <p>
                    {[m.subcategory, m.level, m.region, ...m.tags]
                      .filter(Boolean)
                      .join(" · ")}
                  </p>
                  <a href={"/archives/" + m.id}>查看详情 →</a>
                </article>
              ))
            ) : (
              <table className="directory-table">
                <thead>
                  <tr>
                    {cols.map((v) => (
                      <th key={v}>{v}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {data.data.items.map((m) => (
                    <tr key={m.id}>
                      <td>
                        <a href={"/archives/" + m.id}>{m.name}</a>
                        {missing(m) > 0 && (
                          <span className="missing-chip">
                            {missing(m)} 项待补
                          </span>
                        )}
                      </td>
                      <td>
                        <Category name={m.category} />
                      </td>
                      <td>
                        <span className="chip light">
                          {m.subcategory || "—"}
                        </span>
                      </td>
                      <td>{m.tags.join("、") || "—"}</td>
                      <td>{m.level || "—"}</td>
                      <td>{m.region || "—"}</td>
                      <td>{m.focus || "—"}</td>
                      <td>{m.archive_status || "—"}</td>
                      <td>
                        <label className="check">
                          <input
                            type="checkbox"
                            aria-label={"对比 " + m.name}
                            checked={compare.includes(m.id)}
                            onChange={() => toggle(m.id)}
                          />
                          对比
                        </label>{" "}
                        <a
                          aria-label={"查看详情：" + m.name}
                          href={"/archives/" + m.id}
                        >
                          详情
                        </a>{" "}
                        {maintain && (
                          <a href={"/archives/" + m.id + "?edit=1"}>编辑</a>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
          {!data.data.count && <Empty>暂无匹配的机构档案</Empty>}
          <Pager
            {...data.data}
            change={(n) => apply({ ...active, page: String(n) })}
          />
        </>
      )}
    </div>
  );
}

function Compare({ id }: { id: number }) {
  const d = useData<Media>("media/" + id);
  if (!d.data || d.error) return <Wait error={d.error} retry={d.refresh} />;
  const m = d.data;
  return (
    <div>
      <h3>{m.name}</h3>
      <p>
        {m.region} · {m.category}
      </p>
      <p>{m.owner || "主管单位待补"}</p>
      <p>
        {m.focus} · {m.count} 件事件
      </p>
      <small>
        已补渠道 {Object.values(m.channels).filter(Boolean).length} / 5
      </small>
    </div>
  );
}
function Verification({ m }: { m: Media }) {
  const row = m.verification?.record;
  if (!row) return null;
  return (
    <section className="panel evidence">
      <h2>公开资料核验</h2>
      <p className="muted">核验日期　{row.date}</p>
      {row.collection_note && <p>{row.collection_note}</p>}
      {row.facts.map((f, i) => (
        <details key={i}>
          <summary>
            <b>{f.field}</b>　{f.value}
          </summary>
          <p>{f.basis}</p>
          <a href={f.url} target="_blank" rel="noreferrer">
            查看出处 ↗
          </a>{" "}
          <small>{f.date}</small>
        </details>
      ))}
      {!!row.related_channels?.length && (
        <details>
          <summary>集团共享、历史及其他关联渠道</summary>
          {row.related_channels.map((c, i) => (
            <p key={i}><b>{c.field} · {c.name}</b>　{c.relationship}{c.entity && `（${c.entity}）`}。{c.note}{" "}
              <a href={c.url} target="_blank" rel="noreferrer">关系出处 ↗</a></p>
          ))}
        </details>
      )}
      {!!row.review_history?.length && (
        <details>
          <summary>前次记录与本次修订</summary>
          {row.review_history.map((f, i) => (
            <p key={i}><b>{f.field}</b>　{f.value}　{f.basis}{" "}
              {f.reason && <span>本次修订依据　{f.reason} </span>}
              <a href={f.url} target="_blank" rel="noreferrer">前次出处 ↗</a></p>
          ))}
        </details>
      )}
      <details>
        <summary>各字段核对记录</summary>
        {Object.entries(row.checks).map(([k, v]) => (
          <p key={k}>
            <b>{k}</b>　{v}
          </p>
        ))}
        {row.searches.map((x, i) => (
          <p key={i}>
            <a href={x.url} target="_blank" rel="noreferrer">
              {x.note} ↗
            </a>
          </p>
        ))}
      </details>
      {!!m.reviewed_sources?.length && (
        <>
          <h3>已配置信源</h3>
          {m.reviewed_sources.map((s) => (
            <p key={s.id}>
              <a href={s.url} target="_blank" rel="noreferrer">
                {s.name} ↗
              </a>{" "}
              · {s.enabled ? (s.scheduled ? "参与定时" : "手动采集") : "停用"}
            </p>
          ))}
        </>
      )}
      {!!row.candidates.length && (
        <details>
          <summary>候选栏目与核对依据</summary>
          {row.candidates.map((x, i) => (
            <p key={i}>
              <a href={x.url} target="_blank" rel="noreferrer">
                {x.note} ↗
              </a>
            </p>
          ))}
        </details>
      )}
    </section>
  );
}
export function Archive({ s, id }: { s: Session; id: string }) {
  const data = useData<Media>("media/" + id),
    canEdit = s.rights.includes("edit"),
    [edit, setEdit] = useState(canEdit && query.edit === "1"),
    a = useAction();
  if (!data.data || data.error)
    return <Wait error={data.error} retry={data.refresh} />;
  const m = data.data,
    origin = m.origin_record;
  return (
    <div className="archive-page archive-detail">
      <div className="archive-title">
        <h1>
          <a href="/archives" aria-label="返回媒体档案">
            ←
          </a>{" "}
          媒体档案详情
        </h1>
        <div className="actions">
          {canEdit && (
            <>
              <button
                disabled={a.busy}
                onClick={() =>
                  a.run(async () => {
                    await request("media/" + id, {
                      action: "follow",
                      on: !m.following,
                    });
                    data.refresh();
                  })
                }
              >
                {m.following ? "取消关注" : "关注机构"}
              </button>
              <button className="primary" onClick={() => setEdit(!edit)}>
                编辑与补全
              </button>
            </>
          )}
        </div>
      </div>
      <Notice text={a.notice} />
      {edit && (
        <MediaForm
          s={s}
          initial={m}
          done={() => {
            setEdit(false);
            data.refresh();
          }}
        />
      )}
      <div className="archive-columns">
        <div>
          <section className="panel">
            <h2>{m.name}</h2>
            <p>{m.owner || "主管主办单位待补"}</p>
            <div className="archive-badges">
              <Category name={m.category} />
              {[m.subcategory, m.level, m.focus, m.archive_status]
                .filter(Boolean)
                .map((v) => (
                  <span
                    key={v}
                    className={
                      "chip " +
                      (v === "核心必看"
                        ? "focus"
                        : v === "在库"
                          ? "in-stock"
                          : "light")
                    }
                  >
                    {v}
                  </span>
                ))}
            </div>
            <p>垂直领域　{m.tags.join("、") || "—"}</p>
            <p>地区　{m.region || "—"}</p>
            {m.description && <p>{m.description}</p>}
            {m.high_priority && <strong>高优先级</strong>}
          </section>
          <section className="panel">
            <div className="section-title">
              <h2>平台矩阵</h2>
              <span className="missing-chip">{missing(m)} 项待补</span>
            </div>
            {s.catalog.channels.map((k) => (
              <div className="channel-line" key={k}>
                <span>{k}</span>
                {m.channels[k] ? (
                  <b>
                    {/^https?:\/\//i.test(m.channels[k]) ? (
                      <a href={m.channels[k]} rel="noreferrer" target="_blank">
                        {m.channels[k]}
                      </a>
                    ) : (
                      m.channels[k]
                    )}
                  </b>
                ) : (
                  <small>ⓘ 该渠道信息待补充</small>
                )}
              </div>
            ))}
          </section>
          <Verification m={m} />
          <section className="panel">
            <div className="section-title">
              <h2>已入库动态历史记录</h2>
              <a href={"/news?media=" + m.id}>全部 {m.count} 件 ↗</a>
            </div>
            {m.timeline?.length ? (
              m.timeline.map((e) => <Story key={e.id} e={e} />)
            ) : (
              <Empty>该机构暂无已批准事件</Empty>
            )}
          </section>
        </div>
        <div>
          <section className="panel">
            <h2>档案信息</h2>
            {origin?.metadata &&
              ["最近更新时间", "录入时间"].map((k) => (
                <p key={k}>
                  {k}　{origin.metadata![k]?.text || "—"}
                </p>
              ))}
            <p>
              已入库动态　<b>{m.count}</b>
            </p>
            {m.reference && (
              <p>
                <a href={m.reference} rel="noreferrer" target="_blank">
                  资料出处 ↗
                </a>
              </p>
            )}
            {origin?.url && (
              <details>
                <summary>原站档案与渠道原值</summary>
                <a href={origin.url} rel="noreferrer" target="_blank">
                  {m.origin_id} ↗
                </a>
                {s.catalog.channels.map((k) => (
                  <p key={k}>
                    {k}　{origin.metadata?.[k]?.text || "未填写"}
                    {origin.metadata?.[k]?.links.map(([txt, href]) => (
                      <span key={href}>
                        {" "}
                        {txt} {href}
                      </span>
                    ))}
                    {origin.form &&
                      (k === "官方公众号" ? "公众号" : k) in origin.form && (
                        <small>
                          表单原值　
                          {String(
                            origin.form[k === "官方公众号" ? "公众号" : k] ||
                              "未填写",
                          )}
                        </small>
                      )}
                  </p>
                ))}
              </details>
            )}
          </section>
        </div>
      </div>
    </div>
  );
}
