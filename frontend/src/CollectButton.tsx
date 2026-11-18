import React, { useEffect, useRef, useState } from "react";
import {
  Notice,
  Wait,
  request,
  states,
  url,
  useAction,
  useData,
} from "./shared";

type Batch = {
  jobs: {
    id: number;
    source__name: string;
    state: string;
    progress: number;
    message: string;
  }[];
  counts: Record<string, number>;
  finished: number;
  complete: boolean;
  event_count: number;
  snapshot: string | null;
};

function CollectionProgress({
  batchKey,
  close,
}: {
  batchKey: string;
  close: () => void;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  const { data, error, refresh } = useData<Batch>(
    url("monitor/batch", { key: batchKey }),
  );
  useEffect(() => {
    ref.current?.showModal();
  }, []);
  useEffect(() => {
    if (!data || error || data.complete) return;
    const timer = setTimeout(refresh, 1500);
    return () => clearTimeout(timer);
  }, [data, error, refresh]);
  return (
    <dialog
      className="collection-dialog"
      ref={ref}
      onClose={close}
      aria-labelledby="collection-heading"
    >
      <div className="section-title">
        <h2 id="collection-heading">本次采集</h2>
        <button type="button" onClick={() => ref.current?.close()}>
          关闭
        </button>
      </div>
      {!data || error ? (
        <Wait error={error} retry={refresh} />
      ) : (
        <>
          <p role="status">
            {data.complete ? "采集已结束" : "正在处理"} · 已结束 {data.finished}{" "}
            / {data.jobs.length} 个信源任务
          </p>
          <div className="snapshot">
            {Object.entries(data.counts).map(([key, count]) => (
              <div key={key}>
                <strong>{count}</strong>
                <small>{key}</small>
              </div>
            ))}
          </div>
          {data.jobs.map((j) => (
            <div key={j.id} className="row">
              <div className="grow">
                <b>{j.source__name}</b>
                <small>
                  {states[j.state] || j.state} · {j.progress}%　{j.message}
                </small>
              </div>
            </div>
          ))}
          {data.complete &&
            (data.snapshot ? (
              <a
                className="button"
                href={url("/news", { snapshot: data.snapshot })}
              >
                查看本次新入库动态 · {data.event_count} 件
              </a>
            ) : (
              <p>本次暂无新入库动态</p>
            ))}
        </>
      )}
    </dialog>
  );
}

export default function CollectButton({
  source = "",
  disabled = false,
  refresh,
}: {
  source?: string;
  disabled?: boolean;
  refresh: () => void;
}) {
  const [batch, setBatch] = useState("");
  const attempt = useRef<{ source: string; key: string } | null>(null);
  const action = useAction();
  return (
    <>
      <button
        className="red"
        disabled={disabled || action.busy || Boolean(batch)}
        onClick={() =>
          action.run(async () => {
            if (!attempt.current || attempt.current.source !== source)
              attempt.current = {
                source,
                key: Array.from(
                  crypto.getRandomValues(new Uint8Array(16)),
                  (byte) => byte.toString(16).padStart(2, "0"),
                ).join(""),
              };
            const receipt = await request<{ key: string; jobs: number[] }>(
              "monitor/collect",
              attempt.current,
            );
            attempt.current = null;
            if (!receipt.jobs.length) throw new Error("请先配置并启用信源。");
            setBatch(receipt.key);
          }, "")
        }
      >
        一键采集入库
      </button>
      <Notice text={action.notice} />
      {batch && (
        <CollectionProgress
          batchKey={batch}
          close={() => {
            setBatch("");
            refresh();
          }}
        />
      )}
    </>
  );
}
