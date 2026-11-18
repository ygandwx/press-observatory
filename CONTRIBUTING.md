# 项目协作

从[完整交付清单](docs/roadmap.md)选取同一交付范围内的工作。Issue 描述依赖、涉及模块和复现步骤。PR 说明功能差异、验证结果、权限与数据迁移影响。新功能应复用服务端权限、审核服务和查询条件。

```bash
python3 scripts/check_docs.py
(cd frontend && npm ci && npm run build)
python backend/manage.py makemigrations --check --dry-run
(cd backend && python manage.py test desk)
git diff --check
```

数据库与密钥环境见[运行手册](docs/operations.md)。CI 使用独立 PostgreSQL 验证迁移和测试；测试合成样本仅存在于测试库。真实样本、备份、内部文件和密钥留在部署方控制的存储中。
