# ADR-0002：组织级租户隔离采用「建表即授权」的 RLS 落地方式

- 状态：已接受（Accepted）
- 日期：2026-10-28
- 相关文件：`services/api/migrations/helpers.py`、`services/api/migrations/versions/0001_core_identity.py`、`0002_trip_domain.py`、`0003_reliability_core.py`、`services/api/app/db.py`、`infra/docker/initdb/01-bootstrap.sql`
- 相关需求：M5（数据库级租户隔离 RLS，RICE 33.3）、AT-S01（跨租户穿透必须为 0 行）

## 1. 背景

本系统面向跨国企业的商务出行管理，同一套部署要承载多个组织（organization）的数据：
行程（Trip）、拜访（Visit）、客户（Customer）、地点（Place）、导入批次（ImportBatch）、
后台任务与幂等记录。差旅数据天然包含客户名单、联系人姓名与邮箱、商务行程与拜访目的，
属于高敏感商业信息。一旦发生跨组织可见，后果是商业机密泄漏，而不是"体验问题"。

因此隔离不能停留在应用层的 `WHERE organization_id = ?` 约定上。约定式过滤有两个失效路径：

1. 新增查询时开发者忘了带条件（漏写比写错更常见，且不会报错，只会静默多返回行）；
2. 后台任务、脚本、运维 SQL、未来新增的只读分析通道绕过 ORM 直接执行 SQL。

这两条路径在代码评审中都很难穷尽。结论：隔离必须由数据库强制。

## 2. 决策

### 2.1 隔离在数据库层实现，用 RLS + FORCE

每张组织级表启用：

```sql
ALTER TABLE <t> ENABLE ROW LEVEL SECURITY;
ALTER TABLE <t> FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON <t> FOR ALL
    USING (<predicate>) WITH CHECK (<predicate>);
```

策略谓词统一为：

```sql
organization_id = nullif(current_setting('app.current_org', true), '')::uuid
```

要点：

- `nullif(..., '')` 把"从未设置"与"显式设为空串"归一为 NULL。策略谓词结果为 NULL 时行不可见、写入被拒，即 **fail-closed**：忘记绑定租户的后果是"查不到数据"，而不是"查到别人的数据"。这是刻意的取舍——前者是可发现的故障，后者是不可发现的泄漏。
- `FOR ALL` + 同时给 `USING` 与 `WITH CHECK`：读路径与写路径都受约束，避免"能读到别人的行"或"能把行写到别人的组织下"。
- `FORCE` 让表 owner 也受策略约束。没有 `FORCE` 时，owner 默认绕过 RLS，用 owner 连接做运维或跑脚本会静默穿透全部租户。

### 2.2 「建表即授权」：隔离随创建表的迁移一起落地

每个创建组织级表的迁移，在**同一批 DDL 内**立刻对这批表启用 RLS 并建策略，
调用 `migrations/helpers.py::enable_org_rls()`。不把 RLS 集中放到最后一个迁移里统一补。

理由：

- 迁移链是可部分升级的（`upgrade 0002` 而不 `upgrade head` 在排查问题时很常见）。若 RLS 只在末尾迁移里补，那么任何"升到中间版本"的库都是无隔离的裸库，而这类库恰恰是排障时最可能被连上并查数据的。
- 迁移脚本本身就是"新建了一张组织级表"的唯一权威位置。在同一处紧邻声明隔离，比在一份后期清单里维护"哪些表需要隔离"更不容易漏。
- 代价是每个迁移文件多几行。可接受。

`0013_security_rls` 保留为**幂等全库扫描兜底与验收**：它遍历 `information_schema` 中带 `organization_id` 列的表，
断言每张表都已 `ENABLE` + `FORCE` 且存在策略；发现遗漏即补建。它的作用不是"主实现"，而是防止未来某个迁移忘了调用 helper。

### 2.3 角色分层：`gbt_owner` 与 `gbt_app`

| 角色 | 用途 | 权限 |
|---|---|---|
| `postgres` | 仅引导（装扩展、建角色、建库） | 超级用户，不用于任何应用或迁移 |
| `gbt_owner` | 表 owner，执行 Alembic 迁移与开发种子 | 拥有 DDL 权限；业务表上因 `FORCE` **同样受 RLS 约束** |
| `gbt_app` | 运行时应用连接 | 非 owner、非超级用户；仅被 `GRANT SELECT, INSERT, UPDATE, DELETE` |

迁移连接用 `migration_database_url`（owner），应用连接用 `database_url`（app），两者在
`app/config.py` 中分开配置，避免"一个连接串既跑迁移又跑应用"导致权限与隔离被顺手放宽。

### 2.4 身份三表例外：`ENABLE` 但不 `FORCE`

`organizations`、`users`、`memberships` 三表只 `ENABLE` RLS，不 `FORCE`。

原因有二：

1. **一次性开通是特权引导操作**。建组织、建用户、建成员关系本质上是"先把租户存在这件事创出来"，它先于任何租户上下文。种子脚本（`scripts/seed_dev.py`）正需要这类操作。
2. **先有鸡还是先有蛋**。`users` 的可见性策略需要 join `memberships`；而在为某用户建第一条 membership 时，该 membership 尚未存在。若对 `users` 强制策略，第一次开通在逻辑上就不可能完成。

这一例外的边界被严格限制在三张身份表。**所有承载业务数据的表（trips/visits/customers/places/import_batches/import_rows/idempotency_records 及后续新增）一律 `FORCE`。**

`gbt_app` 在这三表上依然受策略约束（它非 owner），因此运行时应用无法借这个例外横向读取他组织身份数据。

### 2.5 租户上下文逐事务绑定，绑定在 Session 而非 Engine

`app/db.py` 用一个 **Session 类级**事件监听器，在每次事务开始时把当前组织写入 GUC：

```python
@event.listens_for(Session, "after_begin")
def _bind_guc_on_begin(session, transaction, connection) -> None:
    _bind_guc(session, connection)
```

要点：

- **必须是 Session 事件，不是 Engine 事件。** `after_begin` 属于 `SessionEvents`；写成 `@event.listens_for(app_engine, "after_begin")` 会在**导入期**直接抛 `AttributeError: after_begin`（Engine 上没有这个事件标识符）。这不是风格问题，是启动即崩。
- **必须逐事务绑定，不能只在连接建立时 SET 一次。** 连接池会复用物理连接：请求 A（组织甲）结束后连接归还池中，请求 B（组织乙）取到同一连接，若 GUC 还残留旧值，B 就会读到甲的数据。逐事务绑定配合 `set_config(..., true)`（等于 `SET LOCAL`，事务结束自动失效）才安全。
- **必须用 `SELECT set_config(:key, :value, true)`，不能用 `SET LOCAL x = %s`。** 后者在扩展查询协议下不被 PostgreSQL 接受，会报语法错误；`set_config()` 是函数调用，可正常参数化。
- 未绑定组织时不做任何默认填充：`current_setting('app.current_org', true)` 返回 NULL，策略 fail-closed，查不到任何行。这正是我们要的默认行为。

### 2.6 两条会话通道：`org_session` 与 `owner_session`

- `org_session(org_id)`：应用路径。`gbt_app` 连接 + 绑定 `app.current_org`。业务读写一律走这里。
- `owner_session(org_id=None)`：迁移、种子、运维路径。owner 连接。注意 **owner 在 `FORCE` 表上同样受限**，因此不传 `org_id` 时读写业务表会"查不到/写不进"——这是预期行为，不是 bug；需要跨租户操作时显式传 `org_id`。身份三表因 `FORCE` 例外，`owner_session()` 可无 org 完成开通。

## 3. 被否决的替代方案

### 3.1 应用层 `WHERE organization_id = ?` 约定（否决）

漏写条件静默返回他人数据，且无法覆盖裸 SQL 与未来新增通道。见 §1。

### 3.2 每租户独立 schema（否决）

隔离最强，但：迁移需按租户数重复执行（N 倍 DDL 时间与 N 倍的迁移失败面），
连接池无法跨租户复用（租户数增长即连接数爆炸），
Schema 级对象数量在几十个租户后显著拖慢 `information_schema` 查询与工具链。
本系统租户数量预期是"几十到几百家中小企业"，收益不抵复杂度。

### 3.3 每租户独立数据库（否决）

运维成本（备份、迁移、监控、连接串分发）随租户数线性增长，且无法做跨租户的统一 Schema 演进。
对本阶段规模是过度设计。

### 3.4 单独向量库 / 图库承载记忆，绕开 RLS（否决）

原设计曾考虑"关系库 + 向量库 + 图库"三件套。该方案引入跨系统写入的非事务性（记忆写入与环境数据变更不在同一事务里，
回滚后残留脏记忆）、以及需要**在每个系统里各自重新实现一遍隔离**。决策：三层记忆（语义/程序/情景）
全部落在**同一 PostgreSQL 事务边界**内，情景记忆用 `pgvector`。隔离因此只有一处实现，且天然继承本 ADR 的 RLS。

### 3.5 关闭 Alembic `compare_type` 以消除类型漂移告警（否决，相关但独立）

`compare_type=True` 曾产生大量 `TEXT()`→`String()`、`CHAR(n)`→`String(n)`、`TIMESTAMP(timezone=True)`→`DateTime()` 告警。
直觉反应是关掉 `compare_type` 消噪，但那样就永久失去"模型声明与库现状漂移"的告警能力——
而这类漂移正是"模型以为列是 NOT NULL、数据库其实是可空"这类生产事故的来源。
正确做法是让模型列类型与 DDL **逐字对齐**（`Text`/`CHAR(n)`/`SmallInteger`/`DateTime(timezone=True)`），
并让 `include_object` 只排除 PostGIS 自身对象（`spatial_ref_sys` 表、派生列 `places.geog` 及其索引）。
本 ADR 记录此点，是因为它与 RLS 决策同期作出，且同属"不为了让告警消失而放弃护栏"的取向。

## 4. 后果

**收益**

- 跨租户泄漏从"依赖每次查询写对"变为"数据库强制"，AT-S01 可用一条真实 SQL 断言验证。
- 新增组织级表若忘记隔离，`0013_security_rls` 兜底扫描会补建；同时 helper 的调用点在迁移文件里可见，便于评审。
- 记忆、任务、幂等记录等后续模块自动继承同一隔离，无需各自设计。

**代价与已知约束**

- 每次事务多一次 `set_config` 往返。相对每次请求多次 SQL 的量级，开销可忽略（且仅在事务真正开始后执行一次）。
- 开发与运维中若要跨租户查询（如全局统计），必须显式绑定组织或使用 owner 连接并在 `FORCE` 表上承担限制；没有"一把梭看到所有租户"的便利通道。这是刻意的。
- `pg_dump` / `pg_restore` 类工具与 RLS 的交互需要留意（`--enable-row-security` 相关行为），恢复演练时应验证。
- 三张身份表的 `NO FORCE` 是**已知的窄口**：任何用 owner 连接的代码都能读写这三表。约束方式是纪律——身份开通只出现在引导脚本与迁移中，不出现在应用代码里；后续如引入"自助注册"必须显式评审该口子。

## 5. 验证方式

```bash
# 1) 迁移后在库上直接断言（不经过应用层）
#    期望：每张业务表 rls=True, force=True, policies=1
psql "$MIGRATION_URL" -c "
SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity,
       (SELECT count(*) FROM pg_policies p WHERE p.tablename = c.relname) AS policies
FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = 'public' AND c.relkind = 'r' ORDER BY 1;"

# 2) 跨租户穿透（AT-S01）：以 gbt_app 连接绑定 org A，按 org B 的行程名查询，期望 0 行
#    由 scripts/seed_dev.py --verify 自动化执行；泄漏则脚本以非零码退出。
```

实测结果（PostgreSQL 16.15 / PostGIS 3.6 / pgvector，容器 `zhi-tu-pg`）：
`customers`、`customer_places`、`import_batches`、`import_rows`、`places`、`trips`、`visits`、`idempotency_records`
均为 `rls=True force=True policies=1`；`organizations`、`users`、`memberships` 为 `rls=True force=False policies=1`。

## 6. 后续复查触发条件

出现以下任一情况时需重新评审本 ADR：

- 引入跨租户的全局分析/BI 通道；
- 引入自助注册或组织切换流程；
- 租户数量级跃升（数百以上）导致连接或迁移成本成为瓶颈；
- 引入独立向量库/图库承载记忆；
- 承接外部审计要求（如 SOC 2 / ISO 27001）对隔离证据的具体形式。