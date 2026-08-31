# 订单与受控 SQL 规格

**状态：** 实施中（Tasks 1–4 已完成）

**版本：** 1.0

**上位规格：** [智能客服 Agent 总体规格](000-customer-service-agent-overview.md)

## 1. 范围

本规格定义模拟电商订单的读取、创建、发货前修改、取消、退货退款申请，以及受控只读 NL2SQL。所有数据位于 PostgreSQL，所有客户数据访问都必须使用可信运行时 `customer_id`。

首版不连接真实支付、仓库、物流或退款系统，不允许订单物理删除。

## 2. 订单数据模型

### 2.1 核心实体

`orders`：

- `order_id`：服务端生成的稳定 ID；
- `customer_id`：客户所有者；
- `status`：订单状态；
- `contact_name`、`contact_phone`、`shipping_address`：模拟收货信息；
- `currency`、`total_amount`；
- `created_at`、`updated_at`；
- `version`：乐观并发版本。
- `cancellation_refund_status`：取消时形成的退款处理状态；用于重复取消返回首次业务结果，不代表退款到账。

`order_items`：`order_id`、`product_id`、`product_name_snapshot`、`unit_price`、`quantity`。

`return_requests`：`return_id`、`order_id`、`customer_id`、`reason_code`、`reason_text`、`status`、`refund_status`、时间戳。

`operations`：`operation_id`、`customer_id`、`thread_id`、`tool_name`、规范化参数哈希、审批状态、执行状态、结果摘要和时间戳。

`audit_events`：操作、调用者、目标、前后状态摘要、结果、request/trace ID，不保存密钥。

## 3. 状态机

订单状态：

```text
pending_payment -> paid -> processing -> shipped -> delivered
       |            |          |
       +------------+----------+-> cancelled

delivered -> return_requested -> returned -> refunded
```

首版工具直接产生的状态变化：

- 创建：不存在 → `pending_payment`；
- 取消：`pending_payment | paid | processing` → `cancelled`；
- 退货申请：`delivered` → `return_requested`，同时创建 `return_requests(status=requested, refund_status=pending)`；
- 修改联系方式/地址：状态不变，只增加 `version`。

`shipped` 后不得修改收货信息或取消；`return_requested` 之后的仓库验收、`returned` 和 `refunded` 由模拟后台夹具驱动，不由客服 Agent 直接推进。

| ID | 要求 |
|---|---|
| ORD-STATE-001 | 每个状态转换必须在领域服务中显式允许，数据库 repository 不得绕过状态机。 |
| ORD-STATE-002 | 非法转换返回 `BUSINESS_RULE_REJECTED`，不得重试或让模型换工具规避。 |
| ORD-STATE-003 | 状态变更和审计记录必须位于同一 PostgreSQL 事务。 |
| ORD-STATE-004 | 并发版本不匹配返回 `ORDER_VERSION_CONFLICT`，要求重新查询并重新审批。 |

## 4. 订单工具

### 4.1 `get_order`

模型参数：`order_id`。客户身份从运行时上下文注入。

| ID | 要求 |
|---|---|
| ORD-READ-001 | 只返回当前客户拥有的订单；不存在和不属于客户统一返回 `ORDER_NOT_FOUND`，避免枚举。 |
| ORD-READ-002 | 模型可见内容只包含客服所需摘要；完整实体作为脱敏 artifact。 |
| ORD-READ-003 | 查询结果必须包含状态、版本和查询时间，供后续写操作生成预览。 |

### 4.2 `create_order`

模型参数：商品项、收货人、联系电话、收货地址。价格、商品有效性和总额必须由服务端商品快照计算，不能信任模型提供价格。

| ID | 要求 |
|---|---|
| ORD-CREATE-001 | 工具调用先创建 operation 预览，不得在 HITL 前插入订单。 |
| ORD-CREATE-002 | 批准后以事务创建订单、订单项和审计记录，初始状态为 `pending_payment`。 |
| ORD-CREATE-003 | 商品不存在、停用、数量无效或地址字段缺失必须在预览前拒绝。 |

### 4.3 `update_order_contact`

模型参数：`order_id` 和需要替换的联系方式/地址字段。未提供字段保持不变。

| ID | 要求 |
|---|---|
| ORD-UPDATE-001 | 仅 `pending_payment`、`paid`、`processing` 可修改。 |
| ORD-UPDATE-002 | 预览必须同时显示旧值和新值的脱敏摘要及当前版本。 |
| ORD-UPDATE-003 | 批准后必须用预览版本做乐观并发检查。 |

### 4.4 `cancel_order`

模型参数：`order_id`、`reason_code`、可选说明。

| ID | 要求 |
|---|---|
| ORD-CANCEL-001 | 只有尚未发货的订单可取消。 |
| ORD-CANCEL-002 | 已付款订单取消后必须在 artifact 标记 `refund_status=pending`，不得声称退款已到账。 |
| ORD-CANCEL-003 | 重复取消已取消订单返回同一业务结果，不创建第二次退款语义。 |

### 4.5 `request_return`

模型参数：`order_id`、`reason_code`、`reason_text`。

| ID | 要求 |
|---|---|
| ORD-RETURN-001 | 只有 `delivered` 订单可发起退货退款申请。 |
| ORD-RETURN-002 | 批准后必须原子创建唯一退货申请并将订单变为 `return_requested`。 |
| ORD-RETURN-003 | 工具只能承诺“申请已提交”，不能承诺退货通过或退款到账。 |

## 5. HITL 与幂等

### 5.1 操作预览

每个订单写工具在执行前必须形成：

```json
{
  "operation_id": "server-generated",
  "tool_name": "string",
  "target": {"order_id": "string | absent-for-create"},
  "changes": {},
  "business_effect": "string",
  "expected_order_version": 1,
  "warnings": []
}
```

| ID | 要求 |
|---|---|
| ORD-HITL-001 | 预览和待执行规范化参数必须保存并计算哈希；批准后必须验证哈希未变化。 |
| ORD-HITL-002 | 只允许 approve/reject；任何参数修改都必须拒绝旧 operation 并生成新 operation。 |
| ORD-HITL-003 | operation 必须绑定客户、线程、请求、工具和 interrupt。 |
| ORD-HITL-004 | 被拒绝、过期或客户不匹配的 operation 不得执行。 |

### 5.2 幂等

| ID | 要求 |
|---|---|
| ORD-IDEM-001 | `operation_id` 由服务端在预览阶段生成，数据库必须有唯一约束。 |
| ORD-IDEM-002 | 相同 operation 的重复批准返回首次执行结果；不得再次写入领域数据。 |
| ORD-IDEM-003 | 相同业务意图但不同 operation 仍需按当前状态重新校验，不能自动合并。 |
| ORD-IDEM-004 | 写工具不使用通用自动重试；数据库提交结果不明时先按 operation 查询结果。 |

## 6. 客户隔离

| ID | 要求 |
|---|---|
| ORD-AUTH-001 | 每个 repository 方法必须显式接收服务端 `customer_id`，且查询条件同时包含目标 ID 和客户 ID。 |
| ORD-AUTH-002 | 模型参数 schema 不得出现 `customer_id`。 |
| ORD-AUTH-003 | SQL 工具只能访问客户作用域视图；公共商品视图除外。 |
| ORD-AUTH-004 | 审计和错误消息不得泄露其他客户的订单是否存在。 |

## 7. 受控 NL2SQL

### 7.1 工具契约

`query_business_data(question: str)` 接受自然语言问题。内部 SQL 生成器输出结构化对象：

```json
{
  "sql": "SELECT ...",
  "explanation": "该查询回答什么",
  "referenced_relations": ["customer_orders"]
}
```

模型不得直接获得数据库连接。生成结果必须在独立安全层通过后才能执行。

### 7.2 允许关系

- 客户作用域视图：`customer_orders`、`customer_order_items`、`customer_return_requests`；
- 公共只读视图：`catalog_products`、`catalog_promotions`；
- 禁止直接访问底层表、系统表、审计表、operation 表和检查点表。

### 7.3 校验流水线

```text
structured output validation
-> parse one SQL statement into AST
-> require SELECT/CTE ending in SELECT
-> reject DDL/DML/call/copy/locking/unsafe functions
-> relation and column allowlist
-> inject/verify customer scope
-> enforce approved row limit and timeout
-> execute in read-only transaction
-> normalize result and audit artifact
```

| ID | 要求 |
|---|---|
| SQL-001 | 只允许一个 SQL statement，且最终操作必须为 SELECT。 |
| SQL-002 | 禁止 INSERT、UPDATE、DELETE、MERGE、DDL、CALL、COPY、事务控制、行锁和多语句。 |
| SQL-003 | 客户作用域视图必须由安全层注入可信 `customer_id`，模型 SQL 不能提供或覆盖该值。 |
| SQL-004 | 必须使用 AST 校验，禁止仅靠字符串或正则判断 SQL 安全。 |
| SQL-005 | 必须施加用户批准的最大行数和查询超时；未获参数批准时真实 SQL 工具保持禁用。 |
| SQL-006 | 执行必须使用只读事务和最低权限数据库角色。 |
| SQL-007 | 模型可见结果只含回答所需行和字段；完整 SQL、参数、截断信息和执行计划标识进入脱敏 artifact。 |
| SQL-008 | SQL 解析、白名单或客户范围校验失败时不得尝试自动改写后静默执行；应返回稳定校验错误供 Agent 重新规划。 |

## 8. 稳定业务错误

| code | 含义 | 是否重试 |
|---|---|---:|
| `ORDER_NOT_FOUND` | 订单不存在或不属于当前客户 | 否 |
| `ORDER_STATE_INVALID` | 当前状态不允许目标操作 | 否 |
| `ORDER_VERSION_CONFLICT` | 预览后订单已变化 | 否；重新查询和审批 |
| `ORDER_OPERATION_REJECTED` | 用户拒绝操作 | 否 |
| `ORDER_OPERATION_EXPIRED` | operation 不再可执行 | 否 |
| `SQL_OUTPUT_INVALID` | SQL 模型输出不符合结构 | 有界模型修复 |
| `SQL_POLICY_REJECTED` | AST、安全或范围校验失败 | 否；重新规划 |
| `SQL_TIMEOUT` | 查询超时 | 仅只读工具可有界重试 |
| `BUSINESS_RULE_REJECTED` | 领域规则拒绝 | 否 |

## 9. Given/When/Then 验收场景

### ORD-SCN-001：越权查询

- Given：订单属于客户 A；
- When：客户 B 调用 `get_order`；
- Then：返回 `ORDER_NOT_FOUND`，无订单内容且有安全审计。

### ORD-SCN-002：批准创建

- Given：有效商品和完整地址已形成预览；
- When：用户批准同一 operation 两次；
- Then：只创建一个订单，两次响应引用同一订单和执行结果。

### ORD-SCN-003：发货后修改

- Given：订单状态为 `shipped`；
- When：请求修改地址；
- Then：在生成 HITL interrupt 前返回 `ORDER_STATE_INVALID`。

### ORD-SCN-004：预览后并发变化

- Given：取消预览记录版本 3，审批前订单变为版本 4；
- When：用户批准；
- Then：返回 `ORDER_VERSION_CONFLICT`，不取消，要求重新查询和审批。

### ORD-SCN-005：退货语义

- Given：订单已签收；
- When：用户批准退货；
- Then：订单变为 `return_requested`，生成唯一申请，回答不得声称退款完成。

### SQL-SCN-001：受控客户查询

- Given：自然语言问题询问当前客户最近订单；
- When：SQL 生成器产生合规 SELECT；
- Then：安全层注入客户范围和限制，只返回当前客户数据。

### SQL-SCN-002：危险 SQL

- Given：生成结果含 UPDATE、多语句或系统表；
- When：安全层解析；
- Then：返回 `SQL_POLICY_REJECTED`，数据库没有收到执行请求。

## 10. 测试要求

- 单元：每个状态转换与非法转换、金额计算、预览哈希、幂等、权限过滤、AST 校验和错误映射。
- 契约：全部订单工具 schema、artifact、HITL preview 和 SQL 结构化输出。
- PostgreSQL 集成：事务原子性、唯一约束、乐观锁、只读角色、客户视图和 operation 重放。
- 评测：15 条订单场景必须保持安全和状态机 100%；8 条 SQL 场景覆盖正常、越权、危险语句、无答案和超时。

任何越权、重复副作用或非法状态转换测试失败时，整个首版评测必须阻断。
