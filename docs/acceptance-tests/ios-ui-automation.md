# iOS UI 自动化（连接真实后端）

前置：`make api` 与 `make worker` 正在运行（`testAcceptanceJourneyChain` / `testGenerateAndActivatePlan`
要真实 Worker 求解，只起 API 会一直等不到计划）；每条用例用真实 HTTP 自建数据，不依赖手工种子
（`make test` / `make e2e` / `make ios-test` 会自动跑 `make seed`；UI 用例本身可不依赖种子）。

Excel 夹具 `docs/acceptance-tests/fixtures/sample-customers.xlsx` 是**生成产物**（不入版本库）：
`make seed` 按"今天"生成会议日期（+2/+3/+4 天），链式用例的行程窗口也按今天推导，
两边同源；夹具缺失时先跑 `make seed`。

App 已启用文件分享（`GlobalTravelOSConfig/AppInfo.plist` 的 `UIFileSharingEnabled` +
`LSSupportsOpeningDocumentsInPlace`），系统文件选择器因此能列出 App 目录；Excel 用例在
App 启动后把夹具复制进主 App 的 `Documents`（按容器元数据 `MCMMetadataIdentifier` 定位）。

| 用例 | 覆盖 | 说明 |
|---|---|---|
| `testAcceptanceJourneyChain` | AT-001~006 + AT-010/012 单条串联：新建 → Excel 导入 → 规划 → 激活 → 地图/日历/验证 → 费用 → 现场记录 → 纪要（严格断言，无期望失败标记） | 每阶段有导航栏检查点（expectNav）。AT-007/008 与 AT-011 拆成独立用例（见下两行）：链式内做这几段在本机 8 轮完整套件验证中可复现地找不稳，拆开后各自可重试、失败点更近 |
| `testLaunchBrowseSeedTripAndSyncCenter` | Today → AI 助手 → 通知中心（含偏好面板 P3 开关真实 PUT）→ 行动中心 → 出行列表 → 详情 → 同步页 | 验证真实网络加载与基本导航 |
| `testPreparedTripBriefing` | 准备数据 → 详情 → 拜访简报 | 测试内用真实 HTTP 创建地点/出行/拜访，再用启动参数打开，避免依赖列表分页顺序（结果复盘视图 `TripOutcomeView` 暂无 UI 用例，仅服务端 `test_trip_outcome.py` 覆盖） |
| `testPlanChangeAndDiffFlow` | 生成计划 → 激活 → **AT-007** 取消一个拜访（真实 `POST /plan-changes`，等 Worker 重求解）→ **AT-008** 从行程详情「与上一版比较」打开版本比较页 | 覆盖计划调整与版本比较的按钮级路径；用例准备两个拜访（取消唯一拜访会让重求解以「没有可规划的有效拜访」失败，这是正确业务行为）。`PlanDiff` 模型按真实 `PlanVersionDiffRead` 重写（此前手写字段与响应完全漂移，由新增的契约样本解码测试抓出） |
| `testCreateTripFlow` | 出行列表 → 新建表单 → 创建 | 创建后断言新出行立即出现在列表顶部 |
| `testExpenseEntryFlow` | 详情 → 记一笔费用 → 保存 | 真实 POST 费用，断言金额出现在费用列表 |
| `testTripCloseoutReadinessFlow` | 真实出行 + 未完成拜访 + 无票据费用 → 费用区结账评估 → 点击问题跳转拜访区 | 断言 BLOCKED、2 个阻塞、1 个提醒、原币种金额与三类问题来源；检查跳转进入真实拜访区 |
| `testGenerateAndActivatePlan` | 详情 → 生成新计划 → 计划详情 → 激活 | 真实 Worker 求解；计划列表裸数组契约；激活后回到顶部读状态 |
| `testVisitRecordSubmissionFlow` | 详情 → 记录 → 填写 → 保存 → 生成会议纪要草稿 | 真实离线队列 + 服务端提交；同页生成纪要（真实 LLM 网关/stub），断言「会议纪要草稿」区出现 |
| `testExcelImportFlow` | 详情 → 导入 → 系统选择器选夹具 → 预览 → 确认导入 | 两阶段导入端到端：解析不落库、确认后出现「导入结果」 |

运行（推荐 `make ios-test`：内部经 `scripts/ios_test_guard.sh`，xcodebuild 挂死时自动清缓存重试）：

```bash
xcodebuild test -project apps/ios/GlobalTravelOS.xcodeproj -scheme GlobalTravelOS \
  -destination 'platform=iOS Simulator,name=GBT-E2E' -only-testing:GlobalTravelOSUITests
# 注意：只用 bundle 级过滤（-only-testing:GlobalTravelOSUITests）。按单方法过滤
# （-only-testing:GlobalTravelOSUITests/testXxx）在本机偶发匹配不到、跑 0 个用例却报
# TEST SUCCEEDED；隔离排查单条用例时请检查输出里的 Executed N tests。
```

注意：

- UI 用例会在真实数据库留下"UI 简报出行/UI 自动化出行"记录；开发库可定期重建。
- 不使用假 API 或预置页面；测试通过 `GBT_UI_TEST_*` 启动参数覆盖身份与上次出行，不写回磁盘。
- 文件选择器走「我的iPhone → 应用目录」导航（不走搜索：Spotlight 不索引直接拷入的文件）。
- 现场记录页不做多行输入（避免键盘遮挡），纪要按钮用中段坐标拖动交替方向查找后点按（sheet 上 swipe 易触发交互式关闭）。
