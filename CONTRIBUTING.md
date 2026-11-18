# 项目协作

在同一份[完整交付清单](docs/roadmap.md)中维护六个模块和共用工程。Issue 记录具体工作、技术依赖和复现步骤。PR 说明功能差异、验证结果、权限与数据迁移影响。遇到可复现的功能缺口，在同一交付中修正并补充回归。需要真实模型、授权信源或业务名册的验证，在记录中写明现有接口、已测范围和所需资料。新功能应复用服务端权限、审核服务和查询条件。

```bash
python3 scripts/check_docs.py
(cd frontend && npm ci && npm run build)
python backend/manage.py makemigrations --check --dry-run
(cd backend && python manage.py test desk)
git diff --check
```

数据库与密钥环境见[运行手册](docs/operations.md)。CI 使用独立 PostgreSQL 验证迁移和测试；测试合成样本仅存在于测试库。真实样本、备份、内部文件和密钥留在部署方控制的存储中。

完整浏览器与 worker 验收使用独立 PostgreSQL 库。运行命令、证据编号和报告位置见[逐项验收矩阵](docs/verification.md)。
