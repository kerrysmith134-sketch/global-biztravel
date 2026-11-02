# ADR-0026：客户多地址是一等公民，地址带来源与可信度

- 状态：已接受（Accepted）
- 日期：2026-11-02
- 相关文件：`app/services/place_service.py`（`list_customer_places`/`unlink_customer_place`）、`app/api/routes/customers.py`、`app/api/schemas.py`（`CustomerPlaceRead`）、迁移无（复用既有 `customer_places`）
- 相关需求：PRD P0-3「客户地理信息整理——支持一家企业有多个地址，客户信息来源及可信度」

## 背景

PRD 明确要求：一家企业可有多个地址（总部/工厂/仓库），且必须能回答"这个地址的信息从哪来、
多可靠"。库里早有 `customer_places`（Excel 导入时写入），但**没有任何 API 暴露**——用户看不到
多地址，也无法手工增删；地址的坐标来源（`geocode_source`/`geocode_confidence`）只在地点接口
里出现，没有跟"客户的一个地址"绑在一起呈现。

另一个隐含要求是"不能把公司总部默认成实际拜访地点"：拜访接口的 `place_id` 本就必填，
拜访永远指向明确地点；现在把多地址暴露出来后，"选哪个地址去拜访"是显式动作，语义更清楚。

## 决策

1. **复用既有表，不建新表**：`customer_places` 已有唯一约束与 `label`/`is_primary`，
   本 ADR 只补读写 API（迁移 head 不变）。
2. 三个端点（均先校验客户存在且属于当前租户，否则 404——跨租户不泄漏存在性）：
   - `GET /v1/customers/{id}/places`：列出全部地址，主地址优先、名称稳定排序；
   - `POST /v1/customers/{id}/places`：关联已有地点（幂等，走 `IdempotentWrite`）；
   - `DELETE /v1/customers/{id}/places/{place_id}`：解除关联，**不删地点本体**；重复删除 404。
3. **来源与可信度随地址返回**：`CustomerPlaceRead` 内嵌地点的 `geocode_source`、
   `geocode_confidence`、`geocode_status`——看一个地址就能回答"坐标可信吗"。
4. 写操作要求 TRAVELER，读只要 VIEWER，与既有客户档案一致。

## 后果

- 好处：多地址可查可改；总部与工厂是两条明确记录，拜访选点不再有"默认总部"的歧义；
  地址可信度与地点一起暴露，不需再跳一次地点接口。
- 代价：地址的**人工可信度标注**（如"客户口头确认"）尚未建模，目前只有地理编码来源；
  一家企业多地址的去重（同地址不同写法）仍依赖导入阶段的 `resolve_or_create_place`。
