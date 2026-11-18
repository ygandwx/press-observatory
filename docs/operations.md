# 启动、部署与恢复

## Ubuntu 与 Docker Compose

使用带 Compose v2 的 Docker Engine。推荐将仓库检出到 `/opt/press-observatory`，以匹配调度单元。Web 默认只监听主机回环地址 8080。

```bash
git clone https://github.com/ygandwx/press-observatory.git
cd press-observatory
cp .env.example .env
python3 -c 'import secrets; print(secrets.token_urlsafe(48))'
```

将独立生成的随机值分别填入 `.env` 的 `DJANGO_SECRET_KEY` 与 `POSTGRES_PASSWORD`。若使用草稿 PR 中的版本，请先检出该 PR 分支。

```bash
chmod 600 .env
docker compose config -q
docker compose up -d --build
docker compose logs init
docker compose exec web python manage.py account reader 阅读
docker compose exec web python manage.py account editor 编辑 审核 配置
docker compose exec web python manage.py account publisher 签发
curl --fail http://127.0.0.1:8080/api/v1/ready
```

账号命令交互读取密码；再次运行可调整角色，加 `--password` 更新密码。用浏览器打开上述地址的首页。启用 TLS 反向代理时设置域名对应的 `ALLOWED_HOSTS`、`CSRF_TRUSTED_ORIGINS` 和 `COOKIE_SECURE=1`。生产前限制登录尝试速率、设置数据库备份保留期，并确认采集服务器允许访问目标官方信源。停止服务用 `docker compose stop`。

## 名册、信源与模型

默认初始化角色和基础关键词，业务库从空库开始。以下命令可选择性导入人民日报与光明日报两个公开机构入口及三个信源。来源为[人民网](https://www.people.com.cn/)、[光明网](https://www.gmw.cn/)和[报网动态](https://about.gmw.cn/node_8926.htm)。此命令可以重复运行。

```bash
docker compose exec web python manage.py bootstrap --public-sources
docker compose cp ./local-data/media.csv web:/tmp/media.csv
docker compose exec web python manage.py import_media /tmp/media.csv
docker compose exec web python manage.py import_media /tmp/media.csv --apply
```

CSV 须为 UTF-8，至少包含 `name,reference`；支持 `owner,level,category,subcategory,region,kind,focus`。正式名册由部署方核验，缺项可在档案页补全。

模型配置使用 `MODEL_BASE_URL`、`MODEL_NAME`、`MODEL_API_KEY`。服务端调用配置端点下的 `/chat/completions`，需要支持 JSON 输出。会发送待抽取内容或可见事件摘要，部署方应选用获准处理这些材料的模型服务。缺配置时显示具体反馈，采集候选保留待审。自动批准初始关闭。

受限平台支持已有授权的中间数据接口。将 `CHANNEL_FEEDS` 配成以信源 ID 为键、受控的 HTTPS 输出地址为值的 JSON 对象；响应格式是含 `title`、`url` 字段的文章数组。平台登录、授权和数据供应由部署方处理。缺少 feed 的任务进入人工巡检。配置更新后运行 `docker compose up -d` 使进程读取新值。

## 调度

```bash
sudo install -m 0644 deploy/press-collect.service deploy/press-collect.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now press-collect.timer
systemctl list-timers press-collect.timer
```

每天北京时间 08:00 和 18:00 投递信源任务。错过时补最近的时段。手动任务和定时任务共用 worker，详情在监测中心查看。

## 本地无容器启动

需要 Python 3.14 和 Node 24。常规环境使用 PostgreSQL，通过 POSTGRES_HOST、POSTGRES_PORT、POSTGRES_USER、POSTGRES_PASSWORD、POSTGRES_DB 配置。快速单进程开发可设置 `DEV_SQLITE=1`；队列并发与正式部署使用 PostgreSQL。

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r backend/requirements.txt
(cd frontend && npm ci && npm run build)
export DJANGO_SECRET_KEY="$(python -c 'import secrets; print(secrets.token_urlsafe(48))')"
export DEV_SQLITE=1
python backend/manage.py migrate
python backend/manage.py bootstrap --public-sources
python backend/manage.py account local 编辑 审核 签发 配置
python backend/manage.py runserver 127.0.0.1:8000
```

在相同环境的另一终端运行 `python backend/manage.py worker`。根路径提供已构建的 Web 应用。前端热更新使用 `cd frontend && npm run dev`，Vite 代理 API 到 8000。

## 备份、恢复与升级

在升级前保存当前 Git 提交、部署配置和数据库的自定义格式备份。下面的恢复示例写入新命名的独立数据库，运行前确认名称空闲。

```bash
mkdir -p backups
docker compose exec -T db sh -c 'pg_dump -Fc -U "$POSTGRES_USER" "$POSTGRES_DB"' > backups/observatory.dump
docker compose exec -T db sh -c 'createdb -U "$POSTGRES_USER" observatory_restore_check'
docker compose exec -T db sh -c 'pg_restore --exit-on-error -U "$POSTGRES_USER" -d observatory_restore_check' < backups/observatory.dump
```

使用独立应用配置连接恢复库，核对机构、原文、审核日志、期次、签发版本和来源引用。升级时构建已审核提交，先在恢复库验证迁移和业务测试，再部署到目标环境。回退时应用提交和数据库结构须兼容；如果迁移无法逆转，使用升级前快照恢复到独立数据库并切换服务配置。生产代理、证书、告警接收人与数据保留期限由部署方配置。
