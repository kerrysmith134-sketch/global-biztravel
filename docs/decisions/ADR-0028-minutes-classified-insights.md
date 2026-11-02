# ADR-0028：纪要分类洞察，"必须确认"由服务端强制

- 状态：已接受（Accepted）
- 日期：2026-11-02
- 相关文件：`app/llm/prompts.py`（`meeting_minutes` v2、`MUST_CONFIRM_KINDS`）、
  `app/llm/providers.py`（`_meeting_insights`）、`app/services/minutes_service.py`
  （`MinutesInsight`/`_normalise_insights`）、iOS `MinutesDraft`/`VisitRecordSheet`
- 相关需求：PRD P1「自动识别拜访纪要、客户需求和后续任务」、页面 07 §8.2 分类表

## 背景

PRD 页面 07 §8.2 要求 AI 提取后**必须区分**六类信息（普通会议摘要 / 客户需求 / 商机信号 /
交易条件 / 商业承诺 / 正式商机阶段变化），并规定各自的默认处理；红线是"不得把 AI 推断
直接写入 CRM 并作为已确认事实"。此前纪要只有 summary/decisions/follow_up_tasks，没有分类，
更没有"哪些必须人工确认"的表达。

## 决策

1. **模板升版不改写**：新增 `meeting_minutes` v2（`MEETING_MINUTES_V2_*`），输出多一个
   `insights` 数组；v1 保留在 `PROMPT_TEMPLATES` 里供历史调用回放（模块头的版本化约定）。
2. **类别白名单**：`INSIGHT_KINDS` 六类与 PRD 表一一对应；本地 stub 按关键词确定性抽取
   （只在记录里真的出现"报价/交付/扩产/需要/商机阶段"等措辞时才归类，命不中不生成）。
3. **`requires_confirmation` 是服务端业务规则**：`MUST_CONFIRM_KINDS`（除 SUMMARY 外全部）
   强制为 True——模型说 false 也改回来。校验时非法类别、空文本直接丢弃。
4. **模板兜底不猜**：LLM 不可用时只回一条 SUMMARY 洞察（或空数组），绝不编客户需求/交易条件。
5. **不写 CRM**：洞察只随草稿返回并可回溯 `llm_invocation_id`；测试断言全程零 CRM 同步任务。
6. iOS 在纪要区展示分类徽标与"需人工确认"计数（`pendingConfirmationCount`），客户端只展示、
   不改写确认标记。

## 后果

- 好处：六类信息可区分、可计数；"必须确认"由服务端保证，不依赖模型自觉；红线（不进 CRM、
  不当已确认事实）有自动化断言守着。
- 代价：抽取靠关键词，召回有限（真实模型接入后同一 schema 直接生效）；`STAGE_CHANGE`
  在本地 stub 下几乎不会出现——这是诚实的（记录里没写商机阶段就不该产出）。
