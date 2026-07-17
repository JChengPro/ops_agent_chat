# 测试验收清单

> 本清单定义后续代码和发布验收，不表示当前基线已经全部通过。

## 1. 验收记录头

- [ ] 记录分支和完整 Commit SHA。
- [ ] 记录工作区是否干净及未提交文件。
- [ ] 记录操作系统、Python、Node、Docker 和 PostgreSQL 版本。
- [ ] 记录实际执行命令、开始/结束时间和退出码。
- [ ] 记录通过、失败、跳过数量及每个跳过原因。
- [ ] 测试使用隔离数据库和临时 Runtime 资源。
- [ ] 报告中不包含 API key、密码、私钥、Token 或未脱敏输出。

## 2. Registry 与 LLM 边界

- [ ] 重复 Capability 名称启动失败。
- [ ] 同 `name@version` 定义 Hash 改变启动失败。
- [ ] Action 精确版本/definition hash 不匹配时禁止执行。
- [ ] 未知 executor、runtime、precheck、verifier、rollback 启动失败。
- [ ] change 缺少 precheck/verifier 启动失败。
- [ ] 未注册 Tool、未知参数和越界参数拒绝。
- [ ] respond/clarify 不能夹带 Tool Call。
- [ ] change 不能通过 read 决策执行。
- [ ] 通用“删除有什么后果”直接回答，不触发工具。
- [ ] 真正删除请求因无注册 Capability 被拒绝。
- [ ] 工具输出中的 Prompt Injection 不能改变权限规则。

## 3. Action 与 Approval

- [ ] Hash 覆盖 capability version/definition hash。
- [ ] Hash 覆盖项目、环境、目标、参数和最终 Runtime spec。
- [ ] Hash 覆盖连接、workdir、namespace、Compose 文件和注册配方。
- [ ] Hash 覆盖 risk、approval、Policy version、配置 revision 和 rollback。
- [ ] 任一语义字段变化使旧审批失效。
- [ ] 并发 approve/reject 只有一个请求成功。
- [ ] 审批拒绝、过期、取消和失效原因可区分。
- [ ] 多 Action 中任一拒绝/过期会原子终止其余 pending Approval 并收尾 Run。
- [ ] 后台能终止无人点击的过期 Approval，不让 Run 永久 waiting。
- [ ] 审批人必须具有项目 `approval.decide`。
- [ ] 列表、详情和决定使用一致权限。
- [ ] 审批通过后 Action 只能原子进入一次 executing。

## 4. AgentRun 与 Worker

- [ ] 消息 API 返回 202，不在请求内执行 Graph。
- [ ] 多 Worker 并发只能领取 Run 一次。
- [ ] Worker 心跳只由 lease owner 更新。
- [ ] lease 过期只恢复一次并进入 failed。
- [ ] executing Action 在 lease 过期时进入 execution_unknown。
- [ ] execution_unknown 不自动重放。
- [ ] Graph 顶层异常形成 failed Run 和用户可见消息。
- [ ] Graph 在 Action 领取后异常时，executing Action 进入 execution_unknown。
- [ ] precheck、主执行、verifier 和 rollback 的晚到结果在 token 或 Run 所有权丢失后全部被丢弃。
- [ ] Run 终止时，未开始的 proposed/ready/waiting/approved Action 全部关闭。
- [ ] queued、running、waiting_for_approval 均可取消。
- [ ] cancelled 不被模型、SSH、HTTP 或 Graph 晚到结果覆盖。
- [ ] 终态 Run 不可重新入队。
- [ ] 重复消息 idempotency key 只创建一个 Run。

## 5. Verification 与恢复

- [ ] 未知或缺失 verifier 必须失败。
- [ ] 命令 exit 0 但目标错误时不得 verified。
- [ ] Docker start/restart：running + health + exit 状态正确。
- [ ] Docker scale：总数、running 数和 healthy 数等于期望。
- [ ] Docker stop：所有实例停止或副本为 0。
- [ ] 空输出、畸形 JSON、部分副本失败全部 fail closed。
- [ ] Kubernetes desired/available/rollout 真实验证。
- [ ] systemd ActiveState/SubState 真实验证。
- [ ] 配置更新前比较旧 Hash，更新后比较新 Hash。
- [ ] verifier 结果保存独立 RuntimeEvidence。
- [ ] 变更失败、验证失败和 verifier 缺失进入恢复流程。
- [ ] rollback 使用审批快照，不重新解析可变配置。
- [ ] rollback 自身执行验证；失败标记 rollback_failed。
- [ ] 无恢复能力时明确报告，不声称成功回滚。

## 6. Evidence、Claim 与 Audit

- [ ] fact 无 Evidence 时降级为 inference。
- [ ] completed 不改变 Claim 置信度上限。
- [ ] 每条 Claim 只链接真正支持它的来源。
- [ ] Runtime、Context、Experience Link 各自可追踪。
- [ ] 一条 Link 恰好关联一种来源。
- [ ] 不同项目/Run 的 Evidence 不能被引用。
- [ ] 原始输出脱敏、截断并标记是否敏感。
- [ ] Audit 并发追加不产生多个链头。
- [ ] Audit UPDATE/DELETE 被数据库拒绝。
- [ ] 篡改、分叉、断链、循环可被 verifier 发现。
- [ ] Audit verifier 只允许系统 admin。

## 7. Auth、项目与 API

- [ ] login、logout Token 撤销、禁用用户和失败锁定。
- [ ] owner/approver/operator/viewer 权限矩阵。
- [ ] Connection 必须属于项目 owner。
- [ ] 一个项目最多一个 active default Environment。
- [ ] Runtime 配置使用对应 Schema，路径不能逃逸 workdir。
- [ ] 通用 ChatSession 允许 null project/environment 且无项目工具。
- [ ] 项目 ChatSession 必须绑定同项目有效 Environment。
- [ ] API 统一 401/403/404/409/422/503 语义。
- [ ] `/live` 与 `/ready` 依赖边界正确。
- [ ] 发送消息只保留一个推荐入口，兼容入口不能执行 Graph。

## 8. Context 与 Experience

- [ ] Docker Compose Collector 保存 source hash、实体和关系。
- [ ] 多来源冲突不覆盖丢失来源。
- [ ] 递归关系查询防循环并限制深度。
- [ ] Collector 超时、失败、取消和重复执行可追踪。
- [ ] Collector 长任务不阻塞 HTTP Worker。
- [ ] Experience trust status 只能取允许值。
- [ ] 只有 verified Experience 可被 Agent 检索。
- [ ] 修改 verified 内容后必须重新验证。
- [ ] 删除 Experience 正确处理 Chunk 和历史来源引用。

## 9. 前端端到端

- [ ] 登录、退出和 Token 失效提示。
- [ ] 项目/聊天新增、重命名、置顶和删除。
- [ ] 通用聊天与项目聊天切换。
- [ ] 发送后立即显示用户消息和运行状态。
- [ ] 切换会话后旧轮询不能覆盖当前页面。
- [ ] 取消后立即展示 cancelled，晚到回答不出现。
- [ ] 审批按钮防重复点击并立即反馈已记录。
- [ ] 审批影响、风险和恢复使用自然语言。
- [ ] verified、failed、rolled_back、rollback_failed 区分展示。
- [ ] Evidence 详情可追到来源和时间。
- [ ] Feedback 四种状态可提交和更新。
- [ ] 左右栏折叠、移动视口和长文本无功能遮挡。

## 10. 发布判定

- [ ] Registry compile 通过。
- [ ] Alembic 单 head 和 migration round trip 通过。
- [ ] 后端测试零失败。
- [ ] 前端测试、build 和 API-connected E2E 通过。
- [ ] Compose config、backend/worker/frontend build 通过。
- [ ] `/live`、`/ready` 在完整 Compose 中通过。
- [ ] 真实 Docker Adapter 与审批变更链通过。
- [ ] P0 用例全部通过且无跳过。
- [ ] 未真实验收的 Kubernetes/systemd 继续标记 Experimental。
- [ ] 测试报告与发布 Commit SHA 一致。
