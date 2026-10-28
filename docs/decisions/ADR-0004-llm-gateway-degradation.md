# ADR-0004：LLM 网关统一审计、缓存与模板降级

- 状态：已接受（Accepted）
- 日期：2026-10-28
- 相关文件：`app/llm/{gateway,providers,prompts,tools,contracts}.py`、迁移 `0008_llm_invocations`、`app/services/minutes_service.py`
- 相关需求：M3（LLM 编排与工具契约）、AT-012

## 背景

设计文档第 7 章要求：Provider 抽象、调用记录、Tool 契约、确定性/重放、降级路径、预算护栏。
如果不把这些收在一个网关里，调用会散落在业务代码中，"这条建议由哪个模型/哪版 prompt 产生"
将无法回答；LLM 故障也会直接打断现场收尾。

## 决策

1. **唯一入口 `LLMGateway.complete`**：所有模型调用经过网关；业务代码只提交 `LLMRequest`
   （task、模板 id/版本、变量、schema、fallback）。
2. **每次调用落 `llm_invocations`**：模型、prompt 版本、输入哈希、token、延迟、成本、降级原因、
   trace id。缓存命中仍然落一条 `cached=true` 记录（请求发生过是审计事实），但 token/延迟记 0。
3. **schema 校验 + 重试一次 + 模板降级**：输出不合 schema 时重试一次；仍失败则使用调用方提供的
   fallback，`degraded=true` 并在 `error` 写明原因。绝不让不合格输出进入业务。
4. **输入哈希缓存**：键为 `(task, template, version, model, variables)` 的规范 JSON；相同输入
   直接复用历史输出，支持重放。
5. **本地默认 `StubProvider`**：确定性、零网络、零密钥；`model="stub"` 如实写入审计，不伪装成
   真实模型。配置 `openai_compatible` 时才外呼，凭证只来自环境变量（RL-05）。
6. **工具契约是代码常量**：工具（含 `visit.draft_minutes`）必须声明 scope / requires_approval /
   idempotent，并标注需要人审；Agent 不得直连数据库。

## 后果

- 好处：可审计、可重放、可降级；开发/测试无需网络；成本与延迟有数据可查。
- 代价：哈希缓存对 prompt 之外的隐性变化不敏感（例如外部知识变化），需要靠提升 prompt 版本或
  输入变量来使缓存失效；这是刻意选择——宁可显式升版，也不让"同输入不同输出"静默发生。
