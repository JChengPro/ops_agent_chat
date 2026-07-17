# Ops Agent Chat 最终实现计划

## 当前基线

- 分支：`main`（沙箱只读 `.git`，无法创建功能分支或提交）
- Commit：`4103be68fdd8d55bb33419e84fd9c8381cb968ef`
- 工作区：非干净；所有用户已有修改均已保留
- 设计基线：本地最终设计文档 `00` 至 `04`
- 实施日期：2026-07-16

## 设计目标

交付受治理的单 Agent 运维链路：结构化 LLM Decision、Capability Registry、Policy、不可变 Action、人工 Approval、Runtime 执行、严格 Verification、Evidence/Claim/Audit，以及可运行的 React、PostgreSQL、Worker 和 Docker Compose。

## 当前架构事实

- FastAPI 只创建 queued Run；独立 Worker 通过数据库租约领取 AgentRun 和 CollectorRun。
- Graph 节点为 resolve_capabilities、decide、prepare_actions、await_approval、execute、finish。
- Runtime 支持 Docker Compose、Kubernetes、systemd、Host、HTTP 和注册部署/配置。
- LLM 只能选择 Registry 中已注册的语义能力；最终 argv、HTTP 请求和文件操作由确定性 Adapter 构造。
- Action 冻结 Capability 三元组、关联能力、解析后执行配方、配置修订和治理语义；恢复前再次校验。
- Approval、Run、Action 和 Collector 使用原子条件更新，执行未知不会自动重放。

## 分阶段任务与进度

| 阶段 | 内容 | 状态 | 验收结果 |
|---|---|---|---|
| A | 设计与代码映射、实施决策、基线记录 | COMPLETE | 已覆盖前后端、数据库、部署和旧代码 |
| B | 完整治理快照和 Capability 精确绑定 | COMPLETE | name/version/hash、Policy/risk/approval/config revision 已冻结 |
| C | 严格 Runtime Verification | COMPLETE | Docker/Kubernetes/systemd 专用解析器 fail closed |
| D | 状态、审批、幂等与异常恢复 | COMPLETE | 状态约束、CAS、批次收尾、租约恢复已实现 |
| E | Claim、Experience、Collector | COMPLETE | 三类来源、信任状态、异步 Collector 已实现 |
| F | 前端契约和完整交互 | COMPLETE | 环境切换、异步采集、审批反馈与轮询隔离已实现 |
| G | 旧代码清理和文档收敛 | COMPLETE | 旧主链引用不存在，兼容 API 明确废弃周期 |
| H | 数据库/后端/前端/Docker/Runtime/E2E 验收 | PARTIAL | 后端、迁移、Compose、前端 HTTP 和通用聊天通过；浏览器交互及部分外部 Runtime 未执行 |

## 已完成事项

- Capability name/version/definition hash 精确绑定，关联 precheck/verifier/rollback 同步冻结。
- Action Hash 覆盖最终执行快照、配置修订、Policy 版本、风险、审批模式和恢复快照。
- 严格 Docker/Kubernetes/systemd verifier；空、畸形、矛盾或未知结果失败。
- Approval 批次拒绝、过期、取消和失效收尾；决定与执行消费分离。
- AgentRun 请求幂等、Worker 原子领取、异常收尾、取消优先和未知执行不重放。
- precheck、主执行、verifier 和 rollback 均在远端调用前提交执行令牌；只有仍属于活动 Run 的令牌持有者可以落库，租约恢复和取消会丢弃晚到结果。
- Runtime/Context/Experience 三类 Claim 来源精确关联与数据库约束。
- Experience trust 生命周期和异步 Collector 队列、取消、去重、租约恢复。
- 前端环境切换、Collector 状态、审批即时反馈和会话异步竞争保护。
- 三次 Alembic 迁移和覆盖核心负向路径的回归测试代码。
- README、实施映射、旧代码记录和测试报告与当前实现对齐。

## 重要技术决策

- 不增加自由 Shell，不降低审批、Policy 或 verifier 规则。
- 继续使用 PostgreSQL 队列与租约，不引入 Redis/Celery/Neo4j。
- Environment 执行相关配置使用规范化内容 Hash 作为配置修订。
- 保留现有单 Agent LangGraph，不引入多 Agent。
- Fake LLM 和 Fake Executor 只证明确定性工作流，不冒充真实模型或 Runtime 验收。
- 兼容 API 先标记 deprecated，再在客户端迁移和回归通过后删除。

## 测试结果

- `npm test`：PASS，4/4；`npm run build`：PASS。
- `python -m compileall -q backend/app backend/tests backend/alembic/versions`：PASS。
- 后端 pytest：PASS，106 passed、1 skipped。
- Alembic 空库 `upgrade → downgrade base → upgrade`：PASS，Head 为 `d7f2a9c4e681`。
- `docker compose config --quiet`、完整 build/up 和容器健康：PASS。
- Compose 内前端 HTTP/API 代理 E2E：PASS。
- 真实 DeepSeek 通用聊天：PASS，`你好` 返回 completed；Audit 唯一链头验证通过。
- Playwright 浏览器交互：NOT_TESTED；真实宿主 Docker Adapter、Kubernetes/systemd：BLOCKED 或 NOT_TESTED。

## 阻塞项

- `.git` 写权限不足：无法创建功能分支或提交。
- Playwright 未安装，交互式浏览器 E2E 未执行。
- 测试容器未挂宿主 Docker Socket，真实 Docker Adapter 用例按设计跳过。
- Kubernetes/systemd 无隔离真实目标环境。

## 下一步

在浏览器确认左侧三个点菜单浮在列表上方且不改变列表高度；随后为隔离 Runtime 建立真实 Docker 审批变更链和 Playwright E2E，不降低安全断言。
