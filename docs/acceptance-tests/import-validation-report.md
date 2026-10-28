# Excel 导入 — 真实数据校验报告

> 本报告由 `scripts/generate_import_report.py` 对运行中的后端发起真实 HTTP 请求生成，
> 数据源为验收夹具 `docs/acceptance-tests/fixtures/sample-customers.xlsx`。
> 报告内容全部来自服务端返回的结构化 `report`，未经人工修饰。

#### 运行上下文

| 项 | 值 |
| --- | --- |
| 生成时间（服务端时钟，UTC） | 2026-10-28T06:00:04.792413+00:00 |
| 校验组织 | `11111111-1111-1111-1111-111111111111`（安纳瑞能源） |
| 目标出行 | 2026 Q4 欧洲多国商务出行 `980fb373-7890-4138-813d-e85e934858f3` |
| 上传文件 | `sample-customers.xlsx` |
| 文件 SHA-256 | `443dbf60cc5ea6323696c44d8cf62fa17dd5887f57403c3aad4b42b55200f9ab` |
| 批次 import_id | `959466d0-217f-4406-84a6-a66a27e96dd4` |
| 批次状态 | `PARSED` |
| 地理编码器 | `offline-city-index` / `2026.10` |
| 校验 schema 版本 | `1` |

#### 一、逐行体检结论

| 指标 | 数量 |
| --- | --- |
| 数据行总数 | 10 |
| 可直接导入（VALID） | 8 |
| 存在阻断问题（INVALID） | 1 |
| 批内重复（DUPLICATE） | 1 |
| 坐标未解析 | 1 |
| 已在本次出行中 | 4 |

结论：**10 行中 8 行可直接确认导入**，1 行含阻断问题（默认不勾选）、1 行为批内重复（默认不勾选）；另有 1 行坐标缺失，需人工补全或接受按城市中心点降级定位。确认阶段由用户逐行勾选，阻断行即使被勾选也会被拒收并计入 `rejected_rows`。

> 「已在本次出行中」是与目标出行**当前状态**比对出来的结果（种子基线在这条出行里已有若干次拜访），不是文件自身的固有属性；其余计数与问题码只由文件内容决定，反复校验结果不变。

#### 二、表头识别

- 表格列数：`17`
- 已识别字段：`16` 个
- 未识别（被忽略）列：备注
- 缺失必需列：无

未识别列只会产生 `UNKNOWN_COLUMNS` 警告，不阻断导入；缺失必需列则整体返回 `422 MISSING_COLUMN`，**不会创建任何批次**。

<details><summary>已识别字段清单</summary>

```text
address_line
business_value
city
contact_email
contact_name
country_code
customer_name
external_ref
latitude
longitude
priority
service_minutes
tier
time_window_end
time_window_start
visit_title
```

</details>

#### 三、问题聚合

| 严重度 | 问题码 | 命中行数 | 样例行号 | 说明 | 建议动作 |
| --- | --- | --- | --- | --- | --- |
| 错误 | `MISSING_CUSTOMER_NAME` | 1 | 7 | 客户名称为空 | 补齐客户名称，或删除该行 |
| 警告 | `UNKNOWN_COLUMNS` | 10 | 2、3、4、5、6、7、8、9、10、11 | 以下列被忽略：备注 | 这些列会被忽略，确认是否拼写有误 |
| 警告 | `ALREADY_IN_TRIP` | 4 | 2、3、4、6 | 该出行中已存在同客户同地点的拜访 | 确认是否重复安排 |
| 警告 | `COORDINATES_PARTIAL` | 1 | 5 | 只填了纬度或经度其中一项 | 两项都填或都不填 |
| 警告 | `DUPLICATE_ROW` | 1 | 6 | 与第 2 行重复（同客户、同城市、同时间窗） | 只保留一行，其余不要勾选 |
| 警告 | `INVALID_SERVICE_MINUTES` | 1 | 8 | 停留时长不是正整数分钟 | 填 1–1440 之间的整数 |
| 警告 | `INVALID_TIME_WINDOW` | 1 | 9 | 时间窗起止顺序错误 | 让结束晚于开始 |
| 警告 | `INVALID_COUNTRY_CODE` | 1 | 10 | 国家代码不是两位字母 | 使用 ISO 3166-1 alpha-2，如 CN/DE |
| 警告 | `COORDINATES_MISSING` | 1 | 10 | 无法确定坐标（既没有经纬度，城市也不在离线索引中） | 补经纬度，或改用索引中已知的城市名 |
| 警告 | `INVALID_EMAIL` | 1 | 11 | 邮箱格式可疑 | 修正或留空 |

共命中 `10` 类问题：1 类错误、9 类警告。错误级问题会阻断该行导入，警告级问题放行并在确认结果里回传。

#### 四、逐行明细

| 行号 | 状态 | 客户 | 城市 / 国家 | 坐标 | 问题码 |
| --- | --- | --- | --- | --- | --- |
| 2 | `VALID` | 慕尼黑机器人有限公司 | Munich / DE | RESOLVED（48.1351, 11.5820） | `ALREADY_IN_TRIP`、`UNKNOWN_COLUMNS` |
| 3 | `VALID` | 柏林出行集团 | Berlin / DE | RESOLVED（52.5200, 13.4050） | `ALREADY_IN_TRIP`、`UNKNOWN_COLUMNS` |
| 4 | `VALID` | 阿姆斯特丹货运公司 | Amsterdam / NL | RESOLVED（52.3680, 4.9040） | `ALREADY_IN_TRIP`、`UNKNOWN_COLUMNS` |
| 5 | `VALID` | 赫尔辛基试点公司 | Helsinki / FI | RESOLVED（60.1700, 24.9380） | `COORDINATES_PARTIAL`、`UNKNOWN_COLUMNS` |
| 6 | `DUPLICATE` | 慕尼黑机器人有限公司 | Munich / DE | RESOLVED（48.1351, 11.5820） | `DUPLICATE_ROW`、`ALREADY_IN_TRIP`、`UNKNOWN_COLUMNS` |
| 7 | `INVALID` | — | Berlin / DE | RESOLVED（52.5200, 13.4050） | `MISSING_CUSTOMER_NAME`、`UNKNOWN_COLUMNS` |
| 8 | `VALID` | 布鲁塞尔能源署 | Brussels / BE | RESOLVED（50.8510, 4.3520） | `INVALID_SERVICE_MINUTES`、`UNKNOWN_COLUMNS` |
| 9 | `VALID` | 巴黎物流集团 | Paris / FR | RESOLVED（48.8570, 2.3520） | `INVALID_TIME_WINDOW`、`UNKNOWN_COLUMNS` |
| 10 | `VALID` | 亚特兰蒂斯科技 | Atlantis / — | MISSING | `INVALID_COUNTRY_CODE`、`COORDINATES_MISSING`、`UNKNOWN_COLUMNS` |
| 11 | `VALID` | 都柏林数据服务 | Dublin / IE | RESOLVED（53.3500, -6.2600） | `INVALID_EMAIL`、`UNKNOWN_COLUMNS` |

> 行号为 Excel 实际行号（第 1 行为表头），因此数据行从第 2 行起。本阶段 `parse` 按设计**不写入任何业务数据**：客户、地点、拜访的行数在校验前后保持不变。

#### 五、复核方式

```bash
scripts/bootstrap.sh                       # 起库 + 迁移（幂等）
.venv/bin/python scripts/seed_dev.py --verify   # 造数并校验隔离（幂等）
.venv/bin/python scripts/generate_import_report.py  # 重新生成本报告
.venv/bin/python -m pytest services/api/tests/test_backend_smoke.py -m integration
```
