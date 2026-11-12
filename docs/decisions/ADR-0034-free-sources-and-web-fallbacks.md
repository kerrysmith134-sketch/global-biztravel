# ADR-0034 剩余外部依赖用免费开源源接入，无源者用网页形式兜底

- 状态：已接受（2026-11-12）
- 落点：`app/integrations/{weather,flights,roads}.py`、`app/services/{signal_pull_service,
  visa_catalog_override_service,receipt_ocr_service}.py`、`POST /v1/integrations/signals:pull-*`、
  `/v1/admin/visa-catalog*`、`/v1/trips/{id}/expenses:export`、`POST /v1/expenses:ocr`、
  迁移 `0031_visa_catalog_overrides`、compose `mailpit`、配置 `GBT_WEATHER_PROVIDER` /
  `GBT_FLIGHT_PROVIDER` / `GBT_OSRM_BASE_URL` / `GBT_FIELD_ENCRYPTION_KEY_FILE`
- 对应：implementation-status §7 各项（除 APNs 外全部收口或给出兜底）

## 背景

§7 剩余项此前全部标注"需要真实供应商凭证"。逐项盘点后：多数能用**免钥开源/公开源**
替代（天气/航班/地面路由/汇率/SMTP/embedding/OCR/密钥托管），真正无免费源的是签证官方
数据与财务系统——这两项按"网页形式"兜底（ADMIN 维护页 + CSV 导出）。

## 决策矩阵

| §7 项 | 方案 | 免费/开源来源 | 状态 |
|---|---|---|---|
| 天气 | 免钥 API 接入 | **Open-Meteo**（开源项目，公开数据免 key） | ✅ 信号拉取 + 指南实时天气 |
| 航班 | 免钥 API 接入 | **OpenSky Network**（开源 ADS-B 网络，匿名可用） | ✅ FLIGHT_STATUS 信号 |
| 地面交通 | 免钥 API 接入 | **OSRM**（开源路由引擎 + OSM 数据；demo 限速，生产自托管） | ✅ GROUND 段真实驾车时间 |
| 汇率 | 免钥 API 接入 | Frankfurter（ADR-0033，先行落地） | ✅ |
| SMTP | 开源自托管 | **MailPit**（compose 自带，网页看信件）；生产接免费档 SMTP | ✅ 邮件通道本地闭环 |
| embedding | 开源本地模型 | **Ollama**（OpenAI-compatible 端点），hash 兜底保留 | ✅ 配置即用 |
| KMS | 开源模式 | 密钥文件（Docker secret/挂载，优先于环境变量）；自托管 Vault/Infisical 同路径 | ✅ 文件密钥支持 |
| 票据 OCR | 开源自托管 | **Tesseract**（Apache-2.0），可选外部二进制，未装 503 | ✅ 候选字段提取 |
| 签证官方源 | **网页形式** | 无免费官方 API（Timatic 收费） | ✅ ADMIN 维护页 + 覆盖表 |
| 财务系统 | **网页形式** | 无统一免费标准 | ✅ CSV 导出（防注入） |
| APNs | 不替代 | Apple 专有，无开源可能；inapp 通知中心即兜底 | ⏸ 唯一保留项 |

## 关键语义（全部沿用既有红线）

1. **不伪造数据**（ADR-0005/0012/0018 同源）：源未配置或请求失败一律显式失败
   （503/任务 FAILED/gap 保留）；OSRM 只服务 GROUND（公路网世界模型），RAIL/AIR/SEA
   保持大圆估算并诚实标注 `source`。
2. **信号只存事实**（设计文档 §14）：天气达恶劣阈值（风 ≥50 km/h 或降水 ≥10 mm）才落
   `WEATHER_DISRUPTION`；是否提醒由主动式运行时决定。provider 字段记**实际注入的源名**，
   幂等键随源名走。
3. **人审门槛不放宽**（I3）：OCR 只产候选字段（金额/日期/商户提示），不自动建费用；
   签证覆盖条目强制 `manual_confirmation_required=true`（ADR-0027）。
4. **签证目录单一真相**：离线基线（版本库、只读、可评审）+ 组织覆盖表（迁移 0031，RLS
   同款策略）合并；删除覆盖即回落基线。
5. **CSV 防注入**：公式前缀（`=` `+` `-` `@`）加引号前缀，字段全引号转义（OWASP 规范）。
6. **成本可见性**：免钥公开源有速率限制（OpenSky 匿名、OSRM demo）——拉取入口是 ADMIN
   显式触发，不做后台高频轮询；规模化前自托管对应开源组件。

## 后果

- **+** §7 从"全部需要真实凭证"变为"仅 APNs 一项需要 Apple 凭证"；本地开发/演示
  环境的数据面完全自给（DB + 公开源 + MailPit + Tesseract）。
- **+** 全部新源零密钥、零云端账号；所有降级路径显式可测（14 + 7 条新测试）。
- **−** 公开源的数据时效/精度有限（ECB 日频中间价、OSM 路况缺实时、OpenSky 匿名配额）
  ——与"不做假精度"的立场一致，来源与新鲜度随结果返回。
- **−** Tesseract 票据识别精度一般，只作候选提示不作结论。
