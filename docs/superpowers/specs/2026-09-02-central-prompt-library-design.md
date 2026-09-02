# 集中提示词库设计

## 目标

将本项目的稳定系统提示词和离线摘要提示词集中在一个 YAML 文件中维护，使文案审阅、版本追踪和测试更直观；Python 代码继续负责可信上下文、权限和输入验证。

## 范围

- 新增唯一的提示词资源文件：`config/prompts.yaml`。
- 该文件收纳客服 Agent 的稳定系统提示词，以及 RAPTOR 离线摘要的稳定提示词。
- 提示词读取、schema 校验和渲染接口归属 `src/customer_service_agent/agent_api/service.py`；不新建全局 prompt package。
- RAPTOR 摘要调用方通过明确的 `raptor_summary` 提示词条目取得模板，不复制文案。
- 本设计只定义提示词资源和安全渲染边界；不创建真实 Agent、不调用 DeepSeek、也不改变现有检索或订单业务逻辑。

## 不在范围内

- 运行时从数据库、网络或用户输入加载提示词。
- Jinja、Python 表达式或任意模板执行。
- 提示词的 A/B 实验、远程发布、热更新或多租户覆写。
- 在 YAML 保存 API Key、数据库连接串、客户身份、订单数据、原始检索结果或模型推理过程。

## 文件格式

`config/prompts.yaml` 采用如下固定结构：

```yaml
version: 1

agent:
  customer_service:
    system: |
      你是专业的中文电商客服……
      {trusted_context}
      {tool_policy}

retrieval:
  raptor_summary:
    version: raptor-summary-v1
    system: |
      请将下列客服政策内容压缩为可追溯摘要……
    user: |
      来源编号：{source_ids}
      内容：
      {content}
```

顶层 `version` 必须为整数 `1`。允许的条目键固定为：

| 条目 | 必填字段 | 允许占位符 |
| --- | --- | --- |
| `agent.customer_service` | `system` | `trusted_context`、`tool_policy` |
| `retrieval.raptor_summary` | `version`、`system`、`user` | `source_ids`、`content` |

文本值必须是非空字符串。`retrieval.raptor_summary.version` 必须是非空字符串，首版固定为 `raptor-summary-v1`。

## 安全渲染契约

加载器只读取项目内的 `config/prompts.yaml`，使用安全 YAML 解析器，并在启动时完成结构和占位符校验。渲染采用 Python `str.format_map`，但仅接受本设计列出的占位符名：未知、缺失或重复定义的占位符均使启动失败，并返回稳定配置错误码 `PROMPT_CONFIG_INVALID`。

`trusted_context` 由应用服务根据已验证的请求上下文生成；它可包含面向模型的匿名化会话说明，但不得包含 `customer_id`、访问令牌、密码或内部审计字段。`tool_policy` 由 Agent 工厂生成，描述固定工具能力，不能由客户端覆盖。

`source_ids` 和 `content` 仅由离线 RAPTOR 索引任务传入。原始文档正文、节点元数据和模型输出仍进入索引 artifact 或审计边界；提示词 YAML 不保存这些运行时内容。

## RAPTOR 约束

本设计记录已批准的 RAPTOR 离线摘要参数：

- 摘要层：L1、L2、L3，共三层；L0 为原始叶节点。
- 聚类：余弦相似度的确定性层次聚类，固定随机种子 `42`，每个父节点目标 `8` 个子节点。
- 摘要模型：`deepseek-v4-flash`。
- 温度：`0`；最大输出：`300 tokens`。
- 提示词版本：`raptor-summary-v1`。

模型名称、聚类参数和提示词版本写入每次索引构建报告的配置引用；报告不得记录 API Key、提示词渲染后的业务内容或模型推理过程。

## 数据流

```text
config/prompts.yaml
  -> 安全加载与 schema/占位符校验
  -> PromptCatalog（只读模板）
  -> Agent 工厂注入可信上下文 / RAPTOR 索引器注入来源内容
  -> 模型可见提示词
```

配置文件不能直接触达模型、数据库或请求身份。模型也不能选择 YAML 路径、条目名称或替换变量。

## TDD 验收

单元测试必须先覆盖以下失败情形：缺少文件、非法 YAML、错误顶层版本、缺少必填条目、空文本、未知占位符、缺失渲染变量和尝试传入客户身份字段。随后以最小实现使这些测试通过。

正向测试覆盖：客服 Agent 模板只接受两个受控变量；RAPTOR 模板只接受来源编号和内容；`raptor-summary-v1` 作为构建报告配置引用出现；渲染后不含未替换的大括号变量。

不需要 Docker 或真实模型调用的测试必须保持为纯单元测试。DeepSeek 实际调用仍是明确 opt-in 的 live 测试，并在启用前由用户授权费用与凭据使用。

## 可追踪性

| 设计要求 | 后续实现位置 | 测试位置 |
| --- | --- | --- |
| 单一 YAML 提示词来源 | `config/prompts.yaml`、`agent_api/service.py` | `tests/unit/agent_api/test_prompt_catalog.py` |
| 受控变量与可信上下文 | `agent_api/service.py` | `tests/unit/agent_api/test_prompt_catalog.py` |
| RAPTOR 摘要提示词版本 | `retrieval/raptor.py`、`retrieval/indexing.py` | `tests/unit/retrieval/test_raptor.py`、`tests/unit/retrieval/test_index_pipeline.py` |
| 不调用真实模型 | live 测试独立标记 | `tests/live/` |

