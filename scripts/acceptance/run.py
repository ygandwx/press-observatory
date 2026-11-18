"""Run the production bundle and worker against a new, disposable loopback PostgreSQL database."""

import contextlib
import json
import os
import secrets
import signal
import socket
import subprocess
import sys
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.request import urlopen

import psycopg
from psycopg import sql

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
FRONT = ROOT / "frontend"


class ModelFixture(BaseHTTPRequestHandler):
    """OpenAI-compatible local fixture with one deterministic semantic outage."""

    failed = False
    extract_failed = False

    def do_POST(self):
        payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        item = json.loads(payload["messages"][-1]["content"])
        if isinstance(item, list):
            answer = {"conclusion": "隔离研判：仅依据本期合成事件。"}
        elif "events" in item:
            if "失败重试" in item["question"] and not ModelFixture.failed:
                ModelFixture.failed = True
                self.send_error(503, "Fixture failure")
                return
            answer = {
                "ids": [
                    e["id"] for e in item["events"] if e["title"] == "合成分页事件 00"
                ]
            }
        elif (
            "合成提取重试样本" in item.get("text", "")
            and not ModelFixture.extract_failed
        ):
            ModelFixture.extract_failed = True
            self.send_error(503, "Fixture extraction failure")
            return
        else:
            answer = {
                "title": item.get("title") or "合成晚报改版手记",
                "summary": "隔离模型生成的合成摘要",
                "relevant": True,
                "score": 96,
                "category": "技术类",
                "subtype": "",
                "importance": "常规",
                "occurred": "",
                "reasoning": "合成机构调整自身业务",
            }
        data = json.dumps(
            {
                "choices": [
                    {"message": {"content": json.dumps(answer, ensure_ascii=False)}}
                ]
            }
        ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):
        pass


@contextlib.contextmanager
def service(command, env, log, **kwargs):
    with log.open("w") as output:
        p = subprocess.Popen(
            command,
            cwd=ROOT / "backend",
            env=env,
            stdout=output,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            **kwargs,
        )
        try:
            yield p
        finally:
            if p.poll() is None:
                os.killpg(p.pid, signal.SIGTERM)
                try:
                    p.wait(timeout=8)
                except subprocess.TimeoutExpired:
                    os.killpg(p.pid, signal.SIGKILL)
                    p.wait()


def main():
    if not (FRONT / "dist/index.html").is_file():
        sys.exit("Build frontend first: cd frontend && npm ci && npm run build")
    tag = uuid.uuid4().hex
    db_name = "press_e2e_" + tag
    port = os.getenv("ACCEPTANCE_PG_PORT", "5432")
    user = os.getenv("ACCEPTANCE_PG_USER", "postgres")
    password = os.getenv("ACCEPTANCE_PG_PASSWORD", "")
    output = ROOT / "local-data" / "acceptance" / tag
    output.mkdir(parents=True)
    print("Artifacts:", output, flush=True)
    with (
        psycopg.connect(
            host="127.0.0.1",
            port=port,
            user=user,
            password=password,
            dbname="postgres",
            autocommit=True,
        ) as admin,
        contextlib.ExitStack() as stack,
    ):
        admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(db_name)))
        stack.callback(
            admin.execute,
            sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(db_name)),
        )
        model = ThreadingHTTPServer(("127.0.0.1", 0), ModelFixture)
        threading.Thread(target=model.serve_forever, daemon=True).start()
        stack.callback(model.server_close)
        stack.callback(model.shutdown)
        listener = stack.enter_context(socket.socket())
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        base = f"http://127.0.0.1:{listener.getsockname()[1]}"
        env = {
            k: os.environ[k]
            for k in (
                "HOME",
                "PATH",
                "LANG",
                "LC_ALL",
                "TMPDIR",
                "PLAYWRIGHT_BROWSERS_PATH",
                "LD_LIBRARY_PATH",
                "CI",
            )
            if k in os.environ
        }
        env.update(
            POSTGRES_HOST="127.0.0.1",
            POSTGRES_PORT=port,
            POSTGRES_USER=user,
            POSTGRES_PASSWORD=password,
            POSTGRES_DB=db_name,
            DJANGO_SECRET_KEY=secrets.token_urlsafe(36),
            DJANGO_SETTINGS_MODULE="config.settings",
            MODEL_NAME="isolated-fixture",
            MODEL_BASE_URL=f"http://127.0.0.1:{model.server_port}",
            PYTHONPATH=os.pathsep.join(map(str, (ROOT / "backend", HERE))),
            ACCEPTANCE_PASSWORD=secrets.token_urlsafe(20),
            ACCEPTANCE_BASE=base,
            ACCEPTANCE_OUTPUT=str(output),
        )
        subprocess.run(
            [sys.executable, "manage.py", "migrate", "--noinput", "--verbosity", "0"],
            cwd=ROOT / "backend",
            env=env,
            check=True,
        )
        subprocess.run(
            [sys.executable, str(HERE / "fixture_app.py"), "seed"], env=env, check=True
        )
        server = stack.enter_context(
            service(
                [
                    sys.executable,
                    "-m",
                    "gunicorn",
                    "fixture_app:application",
                    "--bind",
                    f"fd://{listener.fileno()}",
                    "--timeout",
                    "90",
                ],
                env,
                output / "web.log",
                pass_fds=(listener.fileno(),),
            )
        )
        worker = stack.enter_context(
            service(
                [sys.executable, str(HERE / "fixture_app.py"), "worker"],
                env,
                output / "worker.log",
            )
        )
        for _ in range(50):
            if server.poll() is not None or worker.poll() is not None:
                raise RuntimeError(f"Service exited. See {output}.")
            try:
                with urlopen(base + "/api/v1/ready", timeout=1) as res:
                    if json.load(res)["ready"]:
                        break
            except OSError:
                time.sleep(0.1)
        else:
            raise RuntimeError("Web service did not become ready")
        subprocess.run(
            [
                str(FRONT / "node_modules/.bin/playwright"),
                "test",
                "--config",
                "e2e/playwright.config.ts",
            ],
            cwd=FRONT,
            env=env,
            check=True,
        )
        if worker.poll() is not None:
            raise RuntimeError("Worker exited during acceptance")


if __name__ == "__main__":
    main()
