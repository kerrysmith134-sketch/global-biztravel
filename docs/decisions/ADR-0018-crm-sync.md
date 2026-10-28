# ADR-0018：CRM 同步以"待导入文件"为默认适配器，不伪造外部成功

- 状态：已接受（Accepted）
- 日期：2026-10-28
- 相关文件：迁移 `0023_crm_sync`、`app/models/crm.py`、`app/integrations/crm.py`、`app/services/crm_service.py`、`app/api/routes/crm.py`、`app/workers/tasks.py`
- 相关需求：PRD「访后跟进 / CRM 回写」

## 背景

现场记录需要在访后写回企业 CRM。真实 CRM（Salesforce、Dynamics、飞书等）需要租户级凭据、
字段映射与网络可达性，本地开发无法验证。如果适配器在未配置时返回 `SUCCEEDED`，系统会对外
声称"已写入 CRM"，而实际没有任何外部效果——这是不可接受的假成功。

## 决策

1. 同步是**异步任务**：`POST /v1/visit-records/{id}/crm-sync` 只创建 `crm_sync_jobs` 与
   `crm_sync` 背景任务，返回 202；客户端轮询任务状态。
2. `crm_sync_jobs` 以 `(organization_id, visit_record_id)` 唯一；重复触发返回同一任务，
   记录内容更新后可对失败任务重新入队。
3. 适配器边界是 `CrmProvider.sync(org, record, payload)`。默认 `FileCrmProvider` 把规范化载荷
   写到 `var/crm/<org>/<record>.json`，返回 `PREPARED`——**只表示待导入文件已生成**，不声称
   外部系统已接收。
4. 未配置的 `UnavailableCrmProvider` 抛 `CrmUnavailable`；任务以不可重试的 `TaskFailed` 终态失败，
   `crm_sync_jobs.status=FAILED` 并保留错误原因，供用户与运维看到。
5. 载荷包含出行/拜访/客户/地点与记录正文（结果、笔记、跟进任务、客户端更新时间），
   不包含无关 PII 字段；同一组织内的行受 RLS 约束。

## 后果

- 好处：本地与 CI 可完整验证排队、幂等、状态流转与失败可见性；接入真实 CRM 只需实现
  `CrmProvider` 并配置凭据，无需改业务层。
- 代价：`PREPARED` 不是最终成功；运营需要一个导入步骤（文件/中间表）才能把数据送进 CRM。
  真实适配器上线后应新增 `SUCCEEDED` 路径并保留 `PREPARED` 作为"已落盘未确认"状态。
