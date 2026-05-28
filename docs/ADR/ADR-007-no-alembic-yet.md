# ADR-007 — P0 不用 Alembic,自建幂等 ALTER 补丁

- **状态**:accepted(P0 / P1.x)
- **日期**:2026-05-28
- **相关**:`apps/server/polynoia/storage/bootstrap.py`(`_SCHEMA_PATCHES` + `_apply_schema_patches`)

## 背景

SQLAlchemy 的 `Base.metadata.create_all()` 只**新建表**,不会给已有表 ADD COLUMN。P0 阶段每天加新字段(merge_mode / pinned / default_merge_mode / ...),如果走传统 Alembic 流程:

1. 每个字段一个 `alembic revision --autogenerate -m ...`
2. 跑 `alembic upgrade head`
3. 团队成员 pull 之后还要跑 upgrade

频率太高,摩擦感大。而且 P0 是单机 sqlite,没有"生产数据迁移"风险。

## 决策

**`apps/server/polynoia/storage/bootstrap.py` 维护一个手写的 `_SCHEMA_PATCHES` 列表**,每项是 `(table, column, ADD COLUMN SQL)`。boot 时调 `_apply_schema_patches()` 用 `PRAGMA table_info` 检测列缺失,缺则 `ALTER TABLE ADD COLUMN`,幂等。

例:
```python
_SCHEMA_PATCHES: list[tuple[str, str, str]] = [
    ("conversations", "merge_mode",
     "ALTER TABLE conversations ADD COLUMN merge_mode VARCHAR(16) "
     "NOT NULL DEFAULT 'auto'"),
    ("messages", "pinned",
     "ALTER TABLE messages ADD COLUMN pinned BOOLEAN NOT NULL DEFAULT 0"),
    # ...
]
```

## 为什么

- **零摩擦** — 加字段只改 model.py + bootstrap.py 一行,不用 alembic 工具链
- **dev 数据不丢** — 老用户 sqlite 文件继续可用,跑一次 boot 自动升级
- **代码 review 看一眼就懂** — patches 列表就是 schema 演化的完整 changelog

## 否则会怎样(走 Alembic)

- 每天加新字段都要 revision + upgrade,频率不匹配
- 团队成员同步迁移版本麻烦
- 给老 sqlite 写一个一次性"补丁脚本"不如让 bootstrap 自动做

## 何时改用 Alembic

- 切 Postgres / 多人协作 production env(P1+)
- 涉及"删除列 / 改类型 / 数据迁移"这种不只是 ADD COLUMN 的复杂变更
- 上线 prod 多实例需要 schema 版本号统一时

到那时把 `_SCHEMA_PATCHES` 整理成正式 alembic migration 即可,不冲突。

## 代价

- 不支持复杂迁移(DROP / 改类型 / 数据迁移)— 接受,P0 用不到
- SQLite 特有 — Postgres 切换时要重写
