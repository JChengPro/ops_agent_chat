# 教学图复核记录

复核基线：Commit `3c8c46c`

复核标准：

1. 图中组件、字段和 Capability 必须存在于当前代码。
2. 箭头必须符合真实调用或数据流向。
3. 状态转换必须符合数据库约束和原子更新逻辑。
4. 为便于教学可以省略次要分支，但不能把省略画成相反行为。
5. 图中示例不能暗示 LLM 可以生成任意 Shell 或绕过 Policy。

| 编号 | 图稿 | 复核结论 | 主要代码依据 |
| --- | --- | --- | --- |
| 01 | 系统总体架构 | 通过，已重绘异步 Run 队列、审批和 Runtime 分支；`run_id` 由 FastAPI 返回浏览器，数据库只负责持久化 | `backend/app/api/chat.py`、`backend/app/agent/service.py`、`backend/app/agent/graph.py`、`backend/app/runtime/executor.py` |
| 02 | Docker 部署拓扑 | 通过，包含四个 Compose 服务、真实命名卷、密钥目录默认值及生产 HTTPS 限制 | `docker-compose.yml`、`frontend/nginx.conf`、`backend/app/llm/configuration.py` |
| 03 | 项目目录与模块关系 | 通过，属于模块级抽象；前端样式标为真实文件 `frontend/src/styles.css` | `backend/app/`、`frontend/src/` |
| 04 | 聊天请求完整时序 | 通过，使用当前 `/api/chat-sessions/{session_id}/agent-runs` 入口和 HTTP 202 响应 | `backend/app/api/chat.py`、`backend/app/agent/service.py` |
| 05 | LangGraph 节点流程 | 通过，展示主路径；异常细分留给正文 | `backend/app/agent/graph.py`、`backend/app/agent/state.py` |
| 06 | 结构化 AgentDecision | 通过，字段与 Pydantic Schema 一致 | `backend/app/llm/schemas.py` |
| 07 | Decision 到 Action | 通过，已修正为先创建 Action 草稿，再执行 Policy | `backend/app/agent/graph.py::prepare_actions` |
| 08 | Capability 精确绑定 | 通过，已改用真实 `service.stop/status/start` 关系 | `backend/app/capabilities/definitions/core.yml`、`backend/app/capabilities/registry.py` |
| 09 | Action 不可变执行快照 | 通过，字段来自 Action 快照和 resolved spec | `backend/app/policy/action_hash.py`、`backend/app/agent/graph.py::resolve_action_spec` |
| 10 | Action Hash 与 Approval | 通过，区分存储 Hash、审批提交值和现场重算值 | `backend/app/api/approvals.py`、`backend/app/agent/graph.py` |
| 11 | Action 状态机 | 通过，已移除只读 Action 不存在的 precheck 失败分支 | `backend/app/models/action.py`、`backend/app/agent/graph.py` |
| 12 | AgentRun、Worker 与并发 | 通过，已修正租约过期为 fail closed，不自动重放 | `backend/app/agent/service.py::claim_run`、`recover_expired_runs` |
| 13 | SSH 安全检查链 | 通过，覆盖绑定、配置版本、凭据、指纹、超时、取消和晚到结果 | `backend/app/runtime/executor.py`、`backend/app/runtime/transports/ssh.py` |
| 14 | Precheck、Execute、Verifier、Rollback | 通过，已改为真实的 stop/start 回滚示例 | `backend/app/agent/graph.py::_build_rollback_spec`、`backend/app/runtime/verification.py` |
| 15 | Evidence、Claim、Audit | 通过，已按当前 SQLAlchemy 字段和关联表重绘 | `backend/app/models/action.py`、`evidence.py`、`governance.py`、`chat.py` |
| 16 | 主动巡检、诊断与自动修复 | 通过，已区分只读诊断、窄范围自动启动和人工经验 | `backend/app/monitoring/service.py`、`diagnostics.py` |

## 复核结果摘要

- 16 张图均已逐张检查标题、主流程方向、字段名称、状态名称和 Capability 名称。
- 本次源码复核重点修正了 Docker 挂载与模型连接、`styles.css` 文件、聊天 API、
  Worker 租约恢复、SSH Host Key、Audit Hash DAG 以及巡检事件状态。
- 所有图均为 `1672 x 941` PNG，README 中的 16 个链接均指向对应文件。
- PDF 已逐图验证：16 张图均以原始 `1672 x 941` 像素嵌入，显示宽度统一为
  `531 pt`，约占 A4 页面宽度的 `89.2%`，没有因分页剩余空间不足而被缩小。
- 最终 PDF 共 48 页；自动检查未发现空白页、孤立短页或异常蓝色空表头色块。

## 抽象边界

- 图 03 和图 05 是教学导航图，不枚举所有工具函数和异常分支。
- 图 04 将 Worker 与 LangGraph 合并为一个时序参与者，但真实部署中 Worker 进程调用 LangGraph。
- 图 09 展开了 `resolved_spec_json` 的关键内容；Action Hash 实际对完整 `action_snapshot()` 规范化 JSON 计算 SHA-256。
- 图 15 只展示核心外键和数据职责，不代替完整数据库 ER 图。
- 图 16 只描述当前受支持的低风险自动启动，不表示系统可以自动生成新 Capability 或巡检规则。
