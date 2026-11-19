import React, { useState } from "react";
import {
  Field,
  Heading,
  Notice,
  Select,
  Wait,
  request,
  useAction,
  useData,
} from "./shared";
type Account = { username: string; role: string; active: boolean };
export function Accounts() {
  const data = useData<{ items: Account[]; roles: string[] }>("accounts"),
    action = useAction();
  const [name, setName] = useState(""),
    [password, setPassword] = useState(""),
    [role, setRole] = useState("社领导");
  if (!data.data || data.error)
    return <Wait error={data.error} retry={data.refresh} />;
  return (
    <>
      <Heading
        title="账号管理"
        sub="社领导浏览业务资料，运营编辑可管理档案、采集、审核、签发和平台账号。"
      />
      <form
        className="panel filters"
        onSubmit={(e) => {
          e.preventDefault();
          void action.run(async () => {
            await request("accounts", {
              action: "create",
              username: name,
              password,
              role,
            });
            setName("");
            setPassword("");
            data.refresh();
          }, "账号已创建");
        }}
      >
        <Field label="新账号" value={name} onChange={setName} required />
        <label>
          初始密码
          <input
            type="password"
            autoComplete="new-password"
            minLength={12}
            required
            value={password}
            onChange={(e) => setPassword(e.target.value)}
          />
        </label>
        <Select
          allowEmpty={false}
          label="账号类型"
          value={role}
          options={data.data.roles}
          onChange={setRole}
        />
        <button className="primary" disabled={action.busy}>
          创建账号
        </button>
      </form>
      <Notice text={action.notice} />
      <section className="panel">
        <h2>平台账号</h2>
        {data.data.items.map((u) => (
          <AccountRow
            key={u.username + u.role}
            account={u}
            roles={data.data!.roles}
            refresh={data.refresh}
          />
        ))}
      </section>
    </>
  );
}
function AccountRow({
  account: u,
  roles,
  refresh,
}: {
  account: Account;
  roles: string[];
  refresh: () => void;
}) {
  const [role, setRole] = useState(u.role),
    a = useAction();
  return (
    <div>
      <div className="row">
        <b className="grow">{u.username}</b>
        <Select
          allowEmpty={false}
          label={u.username + " 的账号类型"}
          value={role}
          options={roles}
          onChange={setRole}
        />
        <button
          disabled={a.busy || role === u.role}
          onClick={() =>
            a.run(async () => {
              await request("accounts", {
                action: "role",
                username: u.username,
                role,
              });
              refresh();
            })
          }
        >
          保存角色
        </button>
      </div>
      <Notice text={a.notice} />
    </div>
  );
}
