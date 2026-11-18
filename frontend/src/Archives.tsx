import React, { useState } from "react";
import {
  Heading,
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
        ].map((k) => [
          k,
          initial ? String(initial[k as keyof Media] || "") : "",
        ]),
      ),
    ),
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
              focus: f.focus || "常规参照",
              kind: f.kind || "报纸",
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
export function Archives({ s }: { s: Session }) {
  const [f, set] = useState<Dict>({ ...query }),
    [active, apply] = useState<Dict>({ ...query }),
    [add, setAdd] = useState(false),
    [compare, setCompare] = useState<number[]>([]),
    [show, setShow] = useState(false);
  const d = useData<Page<Media>>(url("media", active));
  const change = (k: string, v: string) => set({ ...f, [k]: v });
  const filter = (p: Dict) => {
    apply(p);
    history.replaceState(null, "", url("/archives", p));
  };
  return (
    <>
      <Heading title="媒体档案" sub="全国纸媒机构、平台矩阵与变化记录">
        {s.rights.includes("edit") && (
          <button onClick={() => setAdd(!add)}>新增机构</button>
        )}
      </Heading>
      {add && (
        <MediaForm
          s={s}
          done={() => {
            setAdd(false);
            d.refresh();
          }}
        />
      )}
      <form
        className="panel filters"
        onSubmit={(ev) => {
          ev.preventDefault();
          filter({ ...f, page: "1" });
        }}
      >
        <Field
          label="名称或主管单位"
          value={f.q || ""}
          onChange={(v) => change("q", v)}
        />
        {Object.entries({
          level: ["层级", s.catalog.levels],
          category: ["一级品类", Object.keys(s.catalog.categories)],
          subcategory: [
            "二级子类",
            f.category
              ? s.catalog.categories[f.category] || []
              : Object.values(s.catalog.categories).flat(),
          ],
          focus: ["关注等级", s.catalog.focus],
        } as Record<string, [string, string[]]>).map(
          ([k, [label, options]]) => (
            <Select
              key={k}
              {...{ label, options }}
              value={f[k] || ""}
              onChange={(v) => change(k, v)}
            />
          ),
        )}
        <Field
          label="地区"
          value={f.region || ""}
          onChange={(v) => change("region", v)}
        />
        <Field
          label="媒体类型"
          value={f.kind || ""}
          onChange={(v) => change("kind", v)}
        />
        <label className="check">
          <input
            type="checkbox"
            checked={f.following === "1"}
            onChange={(e) => change("following", e.target.checked ? "1" : "")}
          />
          仅看关注
        </label>
        <div className="actions">
          <button className="primary">筛选</button>
          <a className="button" href="/archives">
            重置
          </a>
        </div>
      </form>
      {active.snapshot && (
        <p className="muted">
          正在查看生成时保存的机构名册。{" "}
          <a href={url("/archives", { ...active, snapshot: "", page: "1" })}>
            查看实时名册
          </a>
        </p>
      )}
      {!d.data || d.error ? (
        <Wait error={d.error} retry={d.refresh} />
      ) : (
        <section className="panel">
          <div className="section-title">
            <h2>机构名册 · {d.data.count}</h2>
            <button
              disabled={compare.length < 2}
              onClick={() => setShow(!show)}
            >
              {show ? "关闭对比" : "同业对比"}（{compare.length}）
            </button>
          </div>
          {show && (
            <div className="comparison">
              {compare.map((id) => (
                <Compare key={id} id={id} />
              ))}
            </div>
          )}
          {d.data.items.map((m) => (
            <div className="row" key={m.id}>
              <input
                type="checkbox"
                aria-label={"对比 " + m.name}
                checked={compare.includes(m.id)}
                onChange={(e) => {
                  setCompare(
                    e.target.checked
                      ? [...compare, m.id]
                      : compare.filter((x) => x !== m.id),
                  );
                  setShow(false);
                }}
              />
              <a className="grow" href={"/archives/" + m.id}>
                <b>{m.name}</b>
                <small>
                  {[m.owner, m.region, m.subcategory]
                    .filter(Boolean)
                    .join(" · ")}
                </small>
              </a>
              <span className="tag">{m.focus}</span>
            </div>
          ))}
          {!d.data.count && <Empty>暂无匹配的机构档案</Empty>}
          <Pager
            {...d.data}
            change={(n) => filter({ ...active, page: String(n) })}
          />
        </section>
      )}
    </>
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
export function Archive({ s, id }: { s: Session; id: string }) {
  const d = useData<Media>("media/" + id),
    [edit, setEdit] = useState(s.rights.includes("edit") && query.edit === "1"),
    act = useAction();
  if (!d.data || d.error) return <Wait error={d.error} retry={d.refresh} />;
  const m = d.data;
  return (
    <>
      <a href="/archives">← 媒体档案</a>
      <Heading
        title={m.name}
        sub={[m.owner, m.level, m.region, m.kind].filter(Boolean).join(" · ")}
      >
        <button
          disabled={act.busy}
          onClick={() =>
            act.run(async () => {
              await request("media/" + id, {
                action: "follow",
                on: !m.following,
              });
              d.refresh();
            })
          }
        >
          {m.following ? "取消关注" : "关注机构"}
        </button>
        {s.rights.includes("edit") && (
          <button onClick={() => setEdit(!edit)}>编辑与补全</button>
        )}
      </Heading>
      <Notice text={act.notice} />
      {edit && (
        <MediaForm
          s={s}
          initial={m}
          done={() => {
            setEdit(false);
            d.refresh();
          }}
        />
      )}
      <section className="panel">
        <h2>机构资料</h2>
        <p>
          {[m.category, m.subcategory, m.focus, ...m.tags]
            .filter(Boolean)
            .join(" · ")}
        </p>
        {m.reference && (
          <a href={m.reference} rel="noreferrer" target="_blank">
            资料出处 ↗
          </a>
        )}
        <h3>平台矩阵</h3>
        {s.catalog.channels.map((c) => (
          <div className="row" key={c}>
            <b>{c}</b>
            <span>{m.channels[c] || "该渠道信息待补充"}</span>
          </div>
        ))}
      </section>
      <section className="panel">
        <div className="section-title">
          <h2>变化时间线</h2>
          <a href={"/news?media=" + m.id}>全部 {m.count} 件 ↗</a>
        </div>
        {m.timeline?.length ? (
          m.timeline.map((e) => <Story key={e.id} e={e} />)
        ) : (
          <Empty>该机构暂无已批准事件</Empty>
        )}
      </section>
    </>
  );
}
