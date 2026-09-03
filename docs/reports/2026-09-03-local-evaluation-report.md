# 本地完整验证与 LangSmith 评测报告

**日期：** 2026-09-03
**提交基线：** `e9cd2a3` 之后的评分器兼容性修复待提交
**配置边界：** 测试通过 `uv run --env-file .env` 加载本地配置；报告未读取、打印或保存任何密钥、连接串、原始提示词、地址、电话或 chain-of-thought。

## 结论

本次本地与 Docker 回归、真实 DeepSeek 调用、LangSmith 数据集/experiment/trace 验证均已完成。作品集 M6 的评测管线可运行：固定样本会被同步到 LangSmith，确定性评分器可以登记，真实模型调用会生成 LLM trace。

这证明的是**演示版评测管线可用**，不是完整 60 条生产质量门禁，也不证明全部工具由真实 Agent 正确路由。

## 验证结果

| 层级 | 命令 / 范围 | 结果 | 证据边界 |
|---|---|---:|---|
| 静态与单元/契约 | Ruff、unit、contract | 通过 | 231 项单元/契约通过；默认不访问网络 |
| 完整本地 + Docker | unit、contract、PostgreSQL、Milvus、Neo4j | **248 passed**，18.93 秒 | PostgreSQL 订单/checkpoint、Milvus 检索、Neo4j 图查询均使用本机 Docker 隔离资源 |
| 真实 DeepSeek | `live_model` | **2 passed, 1 skipped**，6.97 秒 | 已验证 Agent 工具调用与 RAPTOR 模型路径；跳过项是同时标记为 LangSmith live、但本命令未开启 LangSmith 开关 |
| 真实 LangSmith | portfolio evaluation + DeepSeek tool trace | **1 passed**，16.52 秒 | 创建/复用最小数据集、运行 experiment，并写入真实 LLM trace |

LangSmith SDK 在 Python 3.13 下发出 11 条 `ast.Str` deprecation warning；测试通过，警告来自已安装 SDK 的内部实现，不影响本次结果。

## LangSmith 只读核验

| 项目 / 数据 | 运行数 | 类型 | 错误数 | 结论 |
|---|---:|---|---:|---|
| `wang-agent-portfolio-v1` 数据集 | 1 个数据集 | 6 个固定样本 | — | 已存在并被复用 |
| `wang-agent-portfolio-0e02424e` | 6 | `chain` | 0 | 最新成功 experiment |
| `wang-agent-portfolio-2ce271f7` | 6 | `chain` | 0 | 前一次成功 experiment |
| `wang-agent-portfolio` | 2 | `llm` | 0 | 真实 DeepSeek 工具调用 trace |

首次 experiment `wang-agent-portfolio-c9186cec` 没有可用运行记录。它暴露了评分器参数名不符合 LangSmith SDK 契约的问题；修复后新 experiment 通过，因此该首次记录不计入任何通过率。

## 修复记录

`score_selected_tool` 的第一个参数从 `_inputs` 改为 `inputs`。当前 LangSmith SDK 仅识别特定 evaluator 参数名；此前会在注册评分器时抛出 `ValueError`。新增签名测试先稳定复现该错误，修复后本地 5 项 M6 测试和真实 LangSmith 评测均通过。

## 评测解释与限制

- 六条样本覆盖路由、三类 RAG、SQL、订单的**评测管线**，每条样本的目标工具由确定性 target 返回；因此 `tool_route` 分数验证的是数据集、评分器和 LangSmith experiment 的接线正确性。
- 它不替代真实客服 Agent 的全工具路由准确率，不应报告为“工具选择达到 90%”或“所有 RAG Hit@5 达标”。
- 完整生产 M6（60 条样本、LLM 裁判、P95 门禁、人工抽检）仍不在当前简历版范围内。
- 真实 Docker 与模型验证使用本地服务/账号；在 GitHub Actions 中 CI 只运行 Ruff 与离线 unit/contract，不执行带密钥的测试。

## 复现命令

```bash
RUN_MILVUS_INTEGRATION=1 \
  uv run --env-file .env pytest \
  -m 'unit or contract or integration_postgres or integration_milvus or integration_neo4j' -q

RUN_LIVE_MODEL_TESTS=1 \
  uv run --env-file .env pytest -m live_model -q -rs

RUN_LIVE_LANGSMITH_TESTS=1 RUN_LIVE_MODEL_TESTS=1 \
  uv run --env-file .env pytest tests/live/test_langsmith_portfolio_eval.py -q
```
