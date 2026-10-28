"""全球商务差旅智能体系统（Global Business Travel OS）后端应用包。

分层（设计文档 v2.0 第 5.3/5.4 章）：
    api         HTTP 路由与依赖注入（薄，不放业务逻辑）
    domain      领域模型与不变量
    services    应用用例（编排授权、事务、审计、Outbox）
    planning    规划编排（对接纯计算包 travel_planner）
    proactive   主动式运行时（触发 → 建议 → 人审 → 执行 → 审计）
    memory      Agent 记忆（事实/过程/情景，同库同事务）
    reliability 幂等 / Outbox / 后台任务
    llm         LLM 网关（Provider 抽象 + 调用审计 + 降级）
"""

__version__ = "2.0.0"