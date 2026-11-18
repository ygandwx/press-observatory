import React, { useState } from "react";
import { createRoot } from "react-dom/client";
import {
  day,
  Wait,
  Notice,
  useData,
  useAction,
  request,
  Field,
} from "./shared";
import type { Session } from "./shared";
import Dashboard from "./Dashboard";
import { News, EventDetail } from "./News";
import { Archives, Archive } from "./Archives";
import Monitor from "./Monitor";
import { Accounts } from "./Accounts";
import { Reports, ReportDetail } from "./Reports";
import "./style.css";
const links = [
  ["/", "领导看板", "▦"],
  ["/summary", "今日日报", "▤"],
  ["/news", "动态中心", "≡"],
  ["/archives", "媒体档案", "▥"],
  ["/monitor", "监测中心", "◎"],
  ["/briefings", "每周简报", "▧"],
  ["/accounts", "账号管理", "♙"],
];
function Brand() {
  return (
    <div className="brand">
      <b>报</b>
      <div>
        <strong>报业观察站</strong>
        <small>PRESS OBSERVATORY</small>
      </div>
    </div>
  );
}
function Login({ refresh }: { refresh: () => void }) {
  const [name, setName] = useState(""),
    [password, setPassword] = useState(""),
    a = useAction();
  return (
    <main className="login">
      <section>
        <Brand />
        <h1>
          全国纸媒
          <br />
          业态观测平台<span>。</span>
        </h1>
        <p>媒体档案 · 行业动态 · 观测简报</p>
      </section>
      <form
        onSubmit={(e) => {
          e.preventDefault();
          void a.run(async () => {
            await request("login", { username: name, password });
            refresh();
          }, "");
        }}
      >
        <h2>登录工作台</h2>
        <Field required label="账号" value={name} onChange={setName} />
        <Field
          required
          label="密码"
          type="password"
          value={password}
          onChange={setPassword}
        />
        <button className="primary" disabled={a.busy}>
          登录
        </button>
        <Notice text={a.notice} />
      </form>
    </main>
  );
}
function App() {
  const session = useData<Session>("session"),
    [open, setOpen] = useState(false),
    a = useAction();
  const [, page, id] = location.pathname.split("/");
  if (page === "share" && id)
    return (
      <main className="shared">
        <ReportDetail token={id} />
      </main>
    );
  if (!session.data || session.error)
    return <Wait error={session.error} retry={session.refresh} />;
  const s = session.data;
  if (!s.username) return <Login refresh={session.refresh} />;
  const ops = s.rights.some((x) => ["edit", "review", "configure"].includes(x));
  const view = !page ? (
    <Dashboard s={s} />
  ) : page === "news" ? (
    id ? (
      <EventDetail id={id} s={s} />
    ) : (
      <News s={s} />
    )
  ) : page === "archives" ? (
    id ? (
      <Archive id={id} s={s} />
    ) : (
      <Archives s={s} />
    )
  ) : page === "accounts" && s.role === "维护人员" ? (
    <Accounts />
  ) : page === "monitor" && ops ? (
    <Monitor s={s} />
  ) : ["summary", "briefings"].includes(page) ? (
    id ? (
      <ReportDetail s={s} id={id} />
    ) : (
      <Reports s={s} weekly={page === "briefings"} />
    )
  ) : (
    <div className="empty">
      此页面不可访问。 <a href="/">返回看板</a>
    </div>
  );
  return (
    <div className="shell">
      {open && (
        <button
          aria-label="关闭菜单"
          className="shade"
          onClick={() => setOpen(false)}
        />
      )}
      <aside className={open ? "open" : ""}>
        <a href="/" aria-label="报业观察站首页">
          <Brand />
        </a>
        <small className="navlabel">观测工作台</small>
        <nav>
          {links
            .filter(
              ([path]) => !["/accounts", "/monitor"].includes(path) || ops,
            )
            .map(([path, name, icon]) => (
              <a
                key={path}
                href={path}
                className={"/" + (page || "") === path ? "active" : ""}
              >
                <i>{icon}</i>
                {name}
              </a>
            ))}
        </nav>
        <div className="asidefoot">全国纸媒业态每日观测平台</div>
      </aside>
      <main className="workspace">
        <header>
          <button
            className="hamb"
            aria-label="打开菜单"
            onClick={() => setOpen(true)}
          >
            ☰
          </button>
          <span>
            报业观察站　/　
            {links.find(([path]) => path === "/" + (page || ""))?.[1] || "观测"}
          </span>
          <div>
            <time>{day()}</time>
            <span>{s.role}</span>
            <b>{s.username}</b>
            <button
              onClick={() =>
                a.run(async () => {
                  await request("logout", {});
                  location.href = "/";
                }, "")
              }
            >
              退出
            </button>
          </div>
        </header>
        <div className="content">
          <Notice text={a.notice} />
          {view}
        </div>
      </main>
    </div>
  );
}
window.addEventListener("popstate", () => location.reload());
createRoot(document.getElementById("root")!).render(<App />);
