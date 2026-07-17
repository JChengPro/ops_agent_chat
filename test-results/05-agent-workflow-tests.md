# Agent 工作流测试

| 链路 | 测试实现 | 本次状态 |
|---|---|---|
| 通用直接回答 | Fake Decision Provider 集成测试 | BLOCKED |
| 只读调查和 Evidence | Fake Executor Graph 集成测试 | BLOCKED |
| 变更、审批、恢复和验证 | Graph + PostgreSQL 集成测试 | BLOCKED |
| Registry 定义漂移 | 审批后漂移负向测试 | BLOCKED |
| Policy/配置漂移 | 审批后漂移负向测试 | BLOCKED |
| 取消和 Worker lease | API/服务回归测试 | BLOCKED |
| precheck/执行晚到结果 | token + Run 所有权并发回归测试 | BLOCKED |
| 请求幂等 | 顺序与并发数据库测试 | BLOCKED |

阻塞原因是当前没有包含 psycopg、pytest、FastAPI 和 LangGraph 的完整 Python 环境，不是这些测试已经失败。Fake LLM 测试即使通过，也只证明确定性编排和治理链，不代表真实模型调用通过。

真实 LLM：BLOCKED，当前任务不读取用户密钥且网络受限。
