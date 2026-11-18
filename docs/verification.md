# 功能对照与工程验收

本清单覆盖六个业务模块、监测中心的八个页签，以及贯穿各页的身份、导航和数据持久化。原站依据来自《纸媒业态观测平台-技术与内容框架（开发交接）》第 3、4、6、7、8、9 节和此前的只读走查。现场实际走通了“领导看板 → 业态类 → 中国工业报事件 → 机构档案”，也切换身份查看了运营工作台和简报。原站的提交、采集、修改、签发均未实际执行。原站当时业态类卡片与列表分别显示 9 和 15，复刻项目采用已约定的同快照口径。

**如何读表**　“点到”表示原站走通过的只读路径，“可见”表示原站显示了页面、控件或数据，“交接”表示依据书面需求。“B01–B09”对应可重复的 Chromium 场景。表内以 `test_` 开头的是 Django 回归用例。B01 截图对应三个视口的看板，B05 截图对应采集弹窗，其余 B 编号可在运行报告中定位到相应交互断言。标为“历史”的资料只作已有证据，本轮没有重新采集这些公开材料。API 均相对 `/api/v1/`。

## 身份、领导看板与动态中心

实现与测试见[导航与身份](../frontend/src/main.tsx)、[看板](../frontend/src/Dashboard.tsx)、[动态中心](../frontend/src/News.tsx)、[事件接口](../backend/desk/events_api.py)、[语义检索](../backend/desk/semantic.py)、[后端用例](../backend/desk/tests.py)和[浏览器用例](../frontend/e2e/platform.spec.ts)。

| 操作 | 原站依据 | 复刻页面及 API | 浏览器、服务端证据与结果 |
| --- | --- | --- | --- |
| 登录、按职责显示入口、退出和直接访问 | 点到身份切换，领导端也可见编辑按钮 | 全站，`session/login/logout` | B02、B03、B04、B06、B07 通过。B09 核对查看人员与维护人员的账号列表、创建与角色切换。独立登录会话覆盖只读、编辑、审核、签发和配置操作。服务端越权和 CSRF 由 `test_role_boundaries`、`test_real_login_requires_csrf_and_logout_ends_session` 验证 |
| 看板五项指标、可见范围、下钻及分类参数 | 五卡可见，点到业态类下钻 | `/`，`dashboard → events/media` | B02 通过今日入库和分类跳转。`test_stat_drill_and_date_boundary` 核对八类计数；历史队列、机构字段和归并拆回另有快照回归 |
| 时间区间、变化提醒、趋势与三种分布 | 研判区、趋势和分布可见，提醒规则见交接 | `/` 看板，`dashboard?days=` | B01、B02 通过三个视口与 7/30/90 天切换，检查四张图实际渲染。提醒采用相对前期至少 3 件且达到 50% 的公开口径 |
| 研判、重点动态、日报入口和一键采集 | 入口可见 | `/`，`POST dashboard`、`monitor/collect` | B05 验证模型替身返回的研判及本批采集。重点事件/日报使用普通详情链接，原站入口与历史走查见 [README](../README.md)。真实模型的研判质量待业务评阅 |
| 普通检索、组合条件、分页、重置、浏览器返回 | 可见普通搜索与筛选，点到分类→列表 | `/news`，`GET events` | B02 通过 26 件关键词筛选、第二页、返回第一页及重置。机构、日期、归并后的多媒体关系由 `test_merge_split_and_filters` 与日期边界用例核对 |
| 导出筛选结果、选中记录与零结果 | 交接 | `/news`，`events?export=csv` | B02 核对实际下载 26 行记录和表头。`test_star_semantic_and_csv_scope`、全范围检索用例核对 ID 过滤、空集及 523 条命中 |
| 收藏、刷新和仅看收藏 | 收藏控件可见 | `/news/{id}`，`events/{id}` 的 star 操作 | B02 通过，刷新仍收藏，列表仅显示一件。`test_star_semantic_and_csv_scope` 检查其他账号看不到个人收藏 |
| 模型搜索、失败提示、重试及结果持久化 | AI 搜索入口可见 | `/news`，`POST events/search` | B02 注入一次模型 503，页面提示未完成，重试后找到靶标，刷新保留结果。后端遍历 523 项，覆盖重复游标和跨账号访问。真实检索相关性待标注样本 |
| 事件详情、关联机构、原文状态与核验 | 点到事件→机构，原文状态可见 | `/news/{id}`，`events/{id}`、`media/{id}` | B06 对合成来源核验后批准。链接读取失败和批处理回滚见 `test_link_verification_and_transactional_batch`。历史双来源实测保留于 README |
| 编辑、批准、退回、归并、拆回与审计 | 审核及归并规则见交接 | `/news/{id}`、审核队列，`events/review`、`events/{id}` | B06 验证未核验时批准被拒、审核员操作和批量退回。归并/拆回及旧链接内容稳定由 Django 用例覆盖。历史浏览器实测核对过编辑、归并前后的结果 |

## 媒体档案

实现见[档案页面](../frontend/src/Archives.tsx)、[档案与配置接口](../backend/desk/management_api.py)和[名册导入命令](../backend/desk/management/commands/import_media.py)。

| 操作 | 原站依据 | 复刻页面及 API | 浏览器、服务端证据与结果 |
| --- | --- | --- | --- |
| 名册预检、事务导入、重复导入、错误行回滚 | 名册和 CSV 见交接 | `manage.py import_media`，读写 Media | 浏览器启动前用实际命令预检两行，核对零写入，连续导入两次仍是两家。故意传入缺出处的行，前一行也回滚。正式名册待提供 |
| 按机构、地区、分类和关注等级筛选，查询历史 | 点到档案与机构历史，字段可见 | `/archives`、`/archives/{id}`，`media`、`events?media=` | B03 读取真实 PostgreSQL 结果。后端筛选和归并关系见 `test_merge_split_and_filters`、`test_historical_queue_and_media_verify_current_permissions` |
| 关注、仅看关注、对比与关闭对比 | 字段及渠道可见，同业对比见交接 | `/archives`，`media/{id}` 的 follow 操作及详情 | B03 验证关注后刷新、仅一条结果，勾选两家并打开与关闭并列对比 |
| 新增/编辑档案、取消与五件套补全 | 机构资料与缺项可见，编辑见交接 | `/archives/{id}?edit=1`，`POST/PATCH media` | B03 验证取消不写、保存五渠道后刷新、覆盖面板从数据库读出 5/10；只读账号直接打开编辑链接也看不到表单。新增与编辑共用表单和服务端校验 |

## 监测中心八个页签

原站运营身份下可见以下全部页签。写入结果均来自复刻系统的独立数据库。实现见[运营工作台](../frontend/src/Monitor.tsx)、[采集弹窗](../frontend/src/CollectButton.tsx)、[配置接口](../backend/desk/management_api.py)、[采集 worker](../backend/desk/collection.py)及[导入、调度和重试用例](../backend/desk/tests.py)。

| 页签与操作 | 原站依据 | 复刻页面及 API | 浏览器、服务端证据与结果 |
| --- | --- | --- | --- |
| 采集概览，全部/单信源投递、进度、完成、重复执行、新入库下钻 | 页签、日志和采集按钮可见 | `/monitor?tab=采集概览`，`monitor/collect`、`monitor/batch` | B05 从看板启动全量、从工作台选单源。独立 worker 读取同一合成原文，首次新增一件，第二次零新增。定时窗口及过期租约重领由后端用例验证 |
| 覆盖度面板，查看机构、信源和五件套缺项，进入补全 | 页签和缺项可见 | 覆盖度面板，`monitor`、`media/{id}` | B03 核对档案保存后面板的分子与分母。历史浏览器打开过补全入口。正式覆盖率依赖名册及启用信源 |
| 信源配置，新增、编辑、选择适配器、关联机构、可信标志和启停 | 配置入口可见 | 信源配置，`monitor/source` | B04 建立合成信源，启用、暂停并核对保存结果。B05 使用可信且关联机构的测试信源。受限平台只核验授权接口形态与巡检回退 |
| 关键词管理，主体/动作/排除词、关联分类、启停与取消 | 页签与规则可见 | 关键词管理，`monitor/rule` | B04 新增规则，取消一次修改，再暂停并刷新核对。worker 回归覆盖主体和动作匹配及分流 |
| 自动入库设置，阈值、过滤线、开关及权限 | 阈值选项可见 | 自动入库设置，`monitor/policy` | B04 检查错误阈值、保存、刷新与审核账号禁用配置。B05 验证启用后的自动入库。后端还覆盖灰区、低分、缺关联机构及第 501 件以外的相似事件 |
| 人工巡检，缺授权转巡检、重试和记录结论 | 页签可见，行为见交接 | 人工巡检及最近记录，`monitor/retry`、`monitor/inspect` | B05 用实际 worker 消费人工渠道任务和重试任务，再记录结论。无需访问受限平台 |
| 待审核队列，多选、全选、批量批准/退回，退回原因 | 选项可见，行为见交接 | 待审核队列，`events?state=pending`、`events/review` | B06 全选两件，未填原因按钮禁用，填写后队列清空。批准权限、版本冲突及事务回滚由 `test_link_verification_and_transactional_batch` 覆盖 |
| 动态录入，正文或链接提取、失败重试、人工修改与保存 | 页签可见，提取与核验规则见交接 | 动态录入，`monitor/extract`、`POST events` | B06 经一次模型失败后成功提取，分别走正文与链接入口，保存候选并由另一个账号核验。模型使用本机协议替身 |

## 业态日报与每周简报

实现见[期刊视图和编辑器](../frontend/src/Reports.tsx)、[PDF 导出](../frontend/src/pdf.ts)、[期刊与签发快照](../backend/desk/reports.py)及[可见性与分享接口](../backend/desk/reports_api.py)。

| 操作 | 原站依据 | 复刻页面及 API | 浏览器、服务端证据与结果 |
| --- | --- | --- | --- |
| 当期、历史、日期定位、打开/生成、重新生成草稿 | 日期、期号和状态可见，生成入口可见 | `/summary`、`/summary/{id}`，`reports` | B01 打开列表与期刊，B07 打开已有当期并重新生成草稿。重复期次和锁定签发稿由后端用例核对 |
| 六栏、关键变化、三项专项、统计、星期、事件原文、归栏出处及待核 | 日报结构可见，六栏见交接 §8.1 | 日报详情，`reports/{id}` | B01、B07 读取期刊。六栏排序、空栏、多机构、上限和已签发旧布局由 Django 回归验证，历史浏览器已展开待核归栏并核对出处。≤3 秒的生产规模生成目标仍需在目标机和正式数据上计时 |
| 编辑取消、保存、刷新、提交、签发、创建更正与查旧版 | 原站只确认编辑及签发入口可见 | 日报详情，`reports/{id}`、`?edition=` | B07 用两个维护账号和一个查看账号的独立会话验证。取消保留原文，更正草稿对读者不可见，历史版本可重查 |
| 复制全文、PDF、长图、匿名只读分享和撤销 | 导出和分享入口可见/交接 | 日报详情、`/share/{token}`，`shared/{token}` | B07 在分享请求未结束时确认复制按钮禁用，随后读回剪贴板中的原文 URL，实际下载并检查 PDF、PNG 文件头，独立匿名会话在撤销后重新打开失败。导出的视觉检查及备份恢复为历史证据 |
| 周报最新一期、日期/月/状态/结论筛选，生成已结束周和归档 | 最新一期、历史与状态筛选可见 | `/briefings`，`reports?kind=weekly` | B08 检查未结束周被拒、历史筛选、签发后进入最新发布及归档筛选。后端重复生成同一周返回同一期 |
| 切换领导参阅版和业务详版、周度比较与原文 | 双版本见交接 §8.2 | `/briefings/{id}`，`reports/{id}` | B08 核对两周计数、业务版分类和同一组事件的原文。签发后比较值复现由后端回归验证。正式周报的编辑质量需要完整自然周的真实资料 |

## 独立环境复跑

在仓库根目录执行。需要 Python、Node、可创建临时数据库的本机 PostgreSQL 账号和 Chromium 系统库。CI 使用 Python 3.14、Node 24、PostgreSQL 18。若系统尚未安装浏览器运行库，在 Chromium 安装命令中加入 `--with-deps`。以下命令使用当前 Python 环境。

```bash
python -m pip install -r backend/requirements.txt
(cd frontend && npm ci && npm run build && npx playwright install --only-shell chromium)
export ACCEPTANCE_PG_USER=postgres
export ACCEPTANCE_PG_PORT=5432
# 有密码的本机数据库，通过环境注入 ACCEPTANCE_PG_PASSWORD。
python scripts/acceptance/run.py
```

[运行入口](../scripts/acceptance/run.py)只连接 127.0.0.1，自建随机命名的空库、临时账号及两个环回端口，运行迁移、真实前端产物、Gunicorn 和独立 Django worker。默认配置不会载入项目的 `.env`、渠道凭据或模型设置。测试登录、CSRF、分页、导出、SQL 和任务租约使用实际实现。只把出站原文字节替换为 `acceptance.invalid` 固定样本，并在另一个本地端口提供合成模型 HTTP 响应。[固定样本](../scripts/acceptance/fixture_app.py)

每次运行把报告 `results.json`、三档宽度的看板截图、采集弹窗截图和服务日志放在 `local-data/acceptance/<运行编号>/`。失败时另存 Playwright 跟踪。正常结束或测试断言失败都会停止服务并删除本次创建的库。CI 在构建后执行相同入口，将结果和截图作为 `browser-acceptance` 工件保留 7 天。[CI 定义](../.github/workflows/check.yml)

**证据边界**　B01 的布局检查在 375、768、1440 像素各打开六个模块和两种期刊详情，共 24 个页面/宽度组合，核对导航和整页横向溢出。实际公开官网采集、两篇真实报道的交叉核验及 PostgreSQL 备份恢复来自此前隔离验证，记录见 [README](../README.md)。本套件每次仅使用合成内容。正式名册、真实受限渠道、模型质量、完整真实周的编辑评阅和目标机运行均保留为业务或部署验收条件。

## 媒体档案专项

B03 核对比较、维护人员关注与编辑、查看人员的按钮可见性。B09 核对三个档案页签及账号角色变更。后端 `DirectoryImportTests` 覆盖预检、整批回滚、重复导入、全字段导出、分页无重复、已有信源和待审关联、最后维护账号保护及越权拒绝。实际名册核对口径见[档案迁移说明](archive-directory.md)。


## 档案证据和栏目采集

`VerifiedRosterTests` 核对预检、逐字段出处、事务回滚、重复导入和人工编辑值保护。`CrawlPolicyTests` 覆盖拒绝与允许路径、同域跳转复核、跨域阻断、抓取间隔、跨请求退避、限定栏目、链接去重，以及采样通过后才可纳入定时。运行命令、详细口径见[逐家核验流程](verified-roster.md)。
