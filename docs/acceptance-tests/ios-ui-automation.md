# iOS UI 自动化（连接真实后端）

前置：`make api` 正在运行；每条用例用真实 HTTP 自建数据，不依赖手工种子（`scripts/seed_dev.py` 可选）。

App 已启用文件分享（`GlobalTravelOSConfig/AppInfo.plist` 的 `UIFileSharingEnabled` +
`LSSupportsOpeningDocumentsInPlace`），系统文件选择器因此能列出 App 目录；Excel 用例在
App 启动后把夹具复制进主 App 的 `Documents`（按容器元数据 `MCMMetadataIdentifier` 定位）。

| 用例 | 覆盖 | 说明 |
|---|---|---|
| `testLaunchBrowseSeedTripAndSyncCenter` | Today → AI 助手 → 通知中心 → 行动中心 → 出行列表 → 详情 → 同步页 | 验证真实网络加载与基本导航 |
| `testPreparedTripBriefing` | 准备数据 → 详情 → 拜访简报 | 测试内用真实 HTTP 创建地点/出行/拜访，再用启动参数打开，避免依赖列表分页顺序 |
| `testCreateTripFlow` | 出行列表 → 新建表单 → 创建 | 创建后断言新出行立即出现在列表顶部 |
| `testExpenseEntryFlow` | 详情 → 记一笔费用 → 保存 | 真实 POST 费用，断言金额出现在费用列表 |
| `testGenerateAndActivatePlan` | 详情 → 生成新计划 → 计划详情 → 激活 | 真实 Worker 求解；计划列表裸数组契约；激活后回到顶部读状态 |
| `testVisitRecordSubmissionFlow` | 详情 → 记录 → 填写 → 保存 → 生成会议纪要草稿 | 真实离线队列 + 服务端提交；同页生成纪要（真实 LLM 网关/stub），断言「会议纪要草稿」区出现 |
| `testExcelImportFlow` | 详情 → 导入 → 系统选择器选夹具 → 预览 → 确认导入 | 两阶段导入端到端：解析不落库、确认后出现「导入结果」 |

运行：

```bash
xcodebuild test -project apps/ios/GlobalTravelOS.xcodeproj -scheme GlobalTravelOS \
  -destination 'platform=iOS Simulator,name=YZ-E2E' -only-testing:GlobalTravelOSUITests
```

注意：

- UI 用例会在真实数据库留下"UI 简报出行/UI 自动化出行"记录；开发库可定期重建。
- 不使用假 API 或预置页面；测试通过 `GBT_UI_TEST_*` 启动参数覆盖身份与上次出行，不写回磁盘。
- 文件选择器走「我的iPhone → 应用目录」导航（不走搜索：Spotlight 不索引直接拷入的文件）。
- 现场记录页按钮贴屏幕下缘时用坐标点按（先收键盘，避免被键盘挡住）。
