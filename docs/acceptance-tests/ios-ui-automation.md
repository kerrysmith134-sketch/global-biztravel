# iOS UI 自动化（连接真实后端）

前置：`make api` 正在运行；`scripts/seed_dev.py` 可选（行动中心与列表浏览不依赖特定种子出行）。

| 用例 | 覆盖 | 说明 |
|---|---|---|
| `testLaunchBrowseSeedTripAndSyncCenter` | Today → 行动中心 → 出行列表 → 详情 → 同步页 | 验证真实网络加载与基本导航 |
| `testPreparedTripBriefing` | 准备数据 → 详情 → 拜访简报 | 测试内用真实 HTTP 创建地点/出行/拜访，再用启动参数打开，避免依赖列表分页顺序 |
| `testCreateTripFlow` | 出行列表 → 新建表单 → 创建 | 创建后断言新出行立即出现在列表顶部 |
| `testExpenseEntryFlow` | 详情 → 记一笔费用 → 保存 | 真实 POST 费用，断言金额出现在费用列表 |
| `testGenerateAndActivatePlan` | 详情 → 生成新计划 → 计划详情 → 激活 | 真实 Worker 求解；计划列表裸数组契约；激活后回到顶部读状态 |

运行：

```bash
xcodebuild test -project apps/ios/GlobalTravelOS.xcodeproj -scheme GlobalTravelOS \
  -destination 'platform=iOS Simulator,name=YZ-E2E' -only-testing:GlobalTravelOSUITests
```

注意：

- UI 用例会在真实数据库留下"UI 简报出行/UI 自动化出行"记录；开发库可定期重建。
- 不使用假 API 或预置页面；测试通过 `GBT_UI_TEST_*` 启动参数覆盖身份与上次出行，不写回磁盘。
- 尚未覆盖 Excel 文件选择器（原生文档选择器）与现场记录提交的逐按钮流程。
