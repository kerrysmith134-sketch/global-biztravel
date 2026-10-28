# ADR-0010：事务必须在响应开始前提交

- 状态：已接受（Accepted）
- 日期：2026-10-28
- 相关文件：`app/api/transaction.py`、`app/api/deps.py`、`app/main.py`
- 相关需求：M4（可靠性）、I2（操作可审计）、S2（端到端时序）

## 背景

端到端脚本创建地点后立刻创建出行，偶发收到 `404 出发地不存在`——地点明明已经创建成功。
根因是 FastAPI 的 `yield` 依赖：自 0.106 起，依赖的退出代码（我们的 `session.commit()`）
在**响应发送之后**执行。客户端可以在 201 到达后、事务提交前发出下一个请求，读到"还不存在"
的行。这不是测试环境特例；弱网之外的任何快客户端/高并发都会遇到，并且提交失败时客户端已经
收到了成功响应。

## 决策

新增最外层 ASGI 中间件 `CommitBeforeResponseMiddleware`：

1. 在转发 `http.response.start` 之前提交请求会话；
2. 4xx/5xx 一律回滚（错误响应不能顺手提交半截写入）；
3. 提交失败 ⇒ 丢弃原响应，返回统一错误体的 `500 COMMIT_FAILED`，并在会话上标记
   `skip_commit`，避免依赖 teardown 二次提交；
4. 会话由 `get_db` 挂到 `request.state.db_session` 供中间件读取；
5. 依赖 teardown 仍保留提交兜底，但看到 `skip_commit` 就跳过。

## 后果

- 好处：客户端看到 2xx 时数据已落库；提交失败会显式暴露，而不是"成功但没保存"。
- 代价：响应开始前多一次提交（正常开销）；流式响应不适合此模式（当前 API 无流式端点）。
- 回归：`services/api/tests/test_transaction_middleware.py` 覆盖 2xx 提交、4xx 回滚、提交失败替换 500。
