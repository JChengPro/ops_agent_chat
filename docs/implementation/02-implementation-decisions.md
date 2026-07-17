# 实现决策

## D-001 保留单 Agent LangGraph

当前 Graph 已覆盖通用回答、只读调查和审批变更。继续复用，安全授权仍由 Registry、Policy 和 Runtime 负责，不引入多 Agent。

## D-002 Action 同时冻结执行与治理语义

Action 快照必须包含 Capability 三元组、Environment 配置 revision、Policy version、risk level 和 approval mode。执行时重新计算 Hash 并比较当前安全 Policy；任何会改变已批准语义的漂移都 fail closed。

## D-003 配置 revision 使用规范化内容 Hash

Environment 的执行相关字段使用确定性 JSON 计算 revision，不依赖更新时间。展示字段变化不会使审批失效；runtime、connection、workdir、namespace、config 和 policy profile 变化会产生新 revision。

## D-004 Verification 使用运行时专用解析器

命令退出成功与业务验证分离。Docker Compose、Kubernetes、systemd 按各自输出语义判断；未知、空、畸形或矛盾结果失败。

## D-005 状态转换集中化但不重写 Worker

新增领域状态模块封装允许值、终态和 CAS；保留当前 Worker lease 和 LangGraph Checkpoint。数据库增加 CheckConstraint 防止非法状态字符串。

## D-006 Approval 批次全有或全无

一个 Run 中任一审批拒绝、过期、取消或快照失效时，终止其余 pending Approval，并把 Run 原子排队恢复以生成用户可见终态。Approval 决定保留审计事实，不把系统失效伪装成用户拒绝。

## D-007 Collector 复用数据库 Worker

不引入 Redis/Celery。CollectorRun 作为队列，现有 Worker 同时领取 AgentRun 和 CollectorRun；API 返回 202，支持状态、取消、超时和去重。

## D-008 一个推荐发送入口

保留 `POST /chat-sessions/{id}/agent-runs` 为主入口并增加 idempotency key。旧 `/messages` POST 暂时保留兼容并返回 Deprecation/Sunset 响应头；`/execute` 继续不执行 Graph并标记废弃。

## D-009 不伪造真实环境通过

Fake LLM、Fake Transport 只证明确定性流程。真实 Docker、SSH、HTTP 分别记录；Kubernetes/systemd 无环境时标记 BLOCKED。

## D-010 审批批次终止覆盖未消费的已批准 Action

用户先批准一项、再拒绝同批另一项时，第一项 Approval 保留 approved 审计事实，但对应 Action 在尚未消费的前提下进入 cancelled。审批决定不能被改写，批次也不能留下可执行的 approved Action。

## D-011 外部 Runtime 输出不能注册知识来源

ContextSource ID 只从受控 Context Capability 的顶层 `source_ids` 获取，ExperienceItem ID 只从 `experience.search.items` 获取。Docker/SSH/HTTP 输出即使包含同名字段，也不能成为 Claim 的 Context 或 Experience 来源。

## D-012 远端恢复前先持久化执行意图

precheck、主 Action、Capability rollback 和 post-change verifier 在远端调用前必须提交带唯一 token 的 executing 标记。远端返回后，只有 Action token 仍匹配且 Run 仍为 running、未请求取消时才能提交结果。Worker 崩溃或用户取消后标记 execution_unknown，不根据缺失记录猜测成功，也不自动重放。
