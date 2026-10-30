# ADR-0024：助手对话与工作流状态分离，卡片是事实快照

- 状态：已接受（Accepted）
- 日期：2026-10-29
- 相关文件：迁移 `0027_assistant_conversations`、`app/services/assistant_service.py`、`app/api/routes/assistant.py`、prompt `assistant_reply v1`
- 相关需求：PRD 页面 05「AI Assistant——移动端统一操作入口」6.1/6.2

## 背景

PRD 要求助手"必须能真正操作系统，而不是只负责聊天"，同时明确：对话记录与工作流状态分离，
清聊天不能取消任务、取消任务不能删审计；卡片内容来自服务端工作流，而不是"根据 AI 最后一条
回复推断"。

## 决策

1. **两张对话表**（迁移 `0027`，RLS）：`assistant_conversations` / `assistant_messages`。
   删除对话只删这两张表（级联），任务/计划/审批/审计零影响。
2. **回复 = 模型文本 + 事实卡片**：`TRIP_STATUS` / `ACTION_CENTER` 卡片由服务端确定性计算
   （行程状态 + 行动中心计数）随消息落库；模型只写 `reply`（prompt `assistant_reply v1`，
   schema 校验，走既有网关与 `llm_invocations` 审计）。卡片不经过模型，杜绝"模型说 3 条待办"
   与真实状态漂移。
3. **执行边界**：助手可以按消息关键词给出 `TOOL_PROPOSAL` 卡片（工具目录条目，
   标注 `requires_approval` 与 scope），但执行仍须走业务接口与人审（不变量 I3）；
   prompt 明确"不声称已经执行"。
4. 每条助手消息带 `llm_invocation_id`，可回溯调用与降级；上下文仅取最近 6 条历史。

## 后果

- 好处：清理聊天是安全操作；卡片可信且可审计；LLM 不可用时网关降级给固定文案，对话仍可用。
- 代价：会话内执行目前是**白名单动作**（批准/驳回建议、生成计划、生成报销单），直接复用
  `proactive_service`（角色、审批策略、会签阈值都在那边，助手不降低门槛）；其他动作仍需
  逐个按同样方式接入；iOS 尚未把 actions 暴露成按钮（服务端 API 已就绪）。
