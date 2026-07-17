# 设计与代码映射

基线：`main@4103be68fdd8d55bb33419e84fd9c8381cb968ef`，包含当前未提交的 Capability 精确绑定修改。

| 设计模块 | 设计要求 | 当前代码位置 | 当前实现状态 | 复用判断 | 存在问题 | 处理方式 | 对应测试 |
|---|---|---|---|---|---|---|---|
| 用户和认证 | JWT、撤销、锁定、禁用用户、生产保护 | `core/security.py`、`api/auth.py` | COMPLETE | 可原样复用 | 缺浏览器真实 Token 失效 E2E | KEEP | `test_api_regressions.py`、E2E |
| 项目 | CRUD、成员权限、软删除、置顶 | `api/projects.py`、`models/project.py` | COMPLETE | 可原样复用 | 真实多角色 API 覆盖可扩充 | KEEP | `test_api_authorization.py` |
| Environment | 分类型配置、唯一默认、稳定配置修订 | `api/projects.py`、`models/project.py`、`policy/action_hash.py` | COMPLETE | 模型/API 保留 | 配置修订使用规范化执行配置 Hash，不新增可变序号 | KEEP | 环境 Schema、治理 Hash 测试 |
| Connection | 只保存凭据引用、严格 Host Key、引用中禁止删除 | `api/connections.py`、`models/project.py`、`ConfigPanel` | COMPLETE | 模型/API 保留 | 响应只公开配置状态，不回传 credential_ref 或 fingerprint | KEEP | Connection API、SSH 集成、前端 E2E |
| Chat | 通用/项目会话边界、软删除 | `api/chat.py` | COMPLETE | 可原样复用 | 发送入口重复 | MODIFY | API 回归、前端 E2E |
| Message | 202 入队、幂等创建 | `api/chat.py`、`agent/service.py` | COMPLETE | 主链保留 | 客户端 request id 在用户与会话范围唯一 | KEEP | 顺序与并发重试去重测试 |
| AgentRun | 正式状态、原子领取、终态不可逆 | `models/agent.py`、`agent/service.py`、`agent/status.py` | COMPLETE | Worker/lease 保留 | 状态值、原子领取、租约恢复和未知执行均有确定规则 | KEEP | 并发、非法转换、异常测试 |
| LangGraph | 结构化单 Agent、审批暂停恢复 | `agent/graph.py` | COMPLETE | 主图保留 | 真实 LLM/SSH 链尚待目标环境验收 | TEST_ONLY | Graph 集成 |
| Context | 来源可追踪实体和关系 | `context/`、`models/context.py` | COMPLETE | 查询和 Collector 保留 | Collector 已异步入队；真实慢 SSH 仍需验收 | TEST_ONLY | 关系、Collector job 测试 |
| Experience Retrieval | verified 内容辅助检索 | `experience/service.py` | COMPLETE | 分块和检索保留 | 已限制 trust 状态，修改后降级，删除归档 | KEEP | trust、检索、Claim Link 测试 |
| Capability Registry | Schema、编译、精确三元组 | `capabilities/` | COMPLETE | 当前实现保留 | 需持续漂移测试 | KEEP | Registry 单元/Graph 回归 |
| Capability Definition | 有界参数、precheck/verifier/rollback | `definitions/core.yml` | COMPLETE | 可原样复用 | 真实运行时成熟度不同 | KEEP | Registry 编译 |
| Policy Engine | 角色、范围、风险、审批 | `policy/engine.py`、`policy/action_hash.py` | COMPLETE | 规则主体保留 | 决策版本、风险和审批模式已冻结并在恢复前复核 | KEEP | Policy 与 Hash 负向测试 |
| Action | 最终不可变执行和治理快照 | `models/action.py`、`agent/graph.py` | COMPLETE | 当前模型保留 | 快照覆盖执行配方、关联能力和治理语义 | KEEP | Action Hash、审批漂移测试 |
| Action Hash | 覆盖全部执行语义 | `policy/action_hash.py` | COMPLETE | 哈希函数保留 | 规范化 Hash 覆盖配置修订、Policy、风险、审批和回滚 | KEEP | 任一字段变化测试 |
| Approval | 唯一 Hash、一次决定、过期/批次收尾 | `api/approvals.py`、`agent/status.py` | COMPLETE | CAS 保留 | 批次拒绝、过期、取消和失效会统一收尾 | KEEP | 并发、批次、过期测试 |
| Verification | 真实最终状态、fail closed | `runtime/verification.py`、`agent/graph.py`、Adapters | COMPLETE | 专用解析器保留 | Docker/Kubernetes/systemd 严格解析；后两者真实环境未验收 | TEST_ONLY | verifier 参数化负向测试 |
| Runtime Adapter | 确定性 argv/HTTP/文件操作 | `runtime/adapters/` | PARTIAL | 结构可复用 | 各运行时真实验收不同 | MODIFY | Adapter 单元和真实集成 |
| SSH Transport | 指纹、超时、取消、输出限制 | `runtime/transports/ssh.py` | COMPLETE | 可原样复用 | 需隔离 SSH 真实矩阵 | TEST_ONLY | 安全边界、SSH 集成 |
| Docker Compose | 读、start/stop/restart/scale | `runtime/adapters/docker.py`、`runtime/verification.py` | COMPLETE | argv 和 verifier 保留 | 真实 Compose 测试代码已补，当前沙箱未运行 | TEST_ONLY | Docker 单元/真实 Compose |
| Kubernetes | 有界 kubectl 操作 | `runtime/adapters/kubernetes.py` | UNVERIFIED | 代码保留 | 无真实集群验收 | TEST_ONLY | 参数/解析；真实 BLOCKED |
| systemd | 有界 systemctl/journalctl | `runtime/adapters/systemd.py` | UNVERIFIED | 代码保留 | 无真实 systemd 目标验收 | TEST_ONLY | 参数/解析；真实 BLOCKED |
| Host | 有界磁盘、内存、端口 | `runtime/adapters/host.py` | UNVERIFIED | 代码保留 | 缺真实 SSH 验收 | TEST_ONLY | argv/SSH 集成 |
| HTTP | SSRF 防护、固定地址、状态验证 | `runtime/adapters/http.py` | PARTIAL | 安全实现复用 | HTTPS/SNI 真实环境未验收 | TEST_ONLY | SSRF、redirect、临时 HTTP |
| Registered Deployment | 注册配方、precheck、执行、验证、恢复 | `runtime/adapters/registered.py` | PARTIAL | 主体复用 | 真实故障恢复范围不足 | MODIFY | 单元和真实 Compose |
| Registered Config | 旧 Hash、路径、写入、验证、恢复 | `runtime/adapters/registered.py`、SSH | PARTIAL | 主体复用 | 真实临时 SSH/SFTP 验收不足 | TEST_ONLY | 安全/恢复测试 |
| Evidence | 工具直接观察、脱敏、时效 | `evidence/service.py` | COMPLETE | 可原样复用 | API/前端详情可增强 | MODIFY | Evidence API/E2E |
| Claim | 原子类型、置信度、精确来源 | `agent/service.py`、`models/evidence.py` | COMPLETE | 现有类型保留 | Runtime/Context/Experience 来源分离，跨 Run Evidence 被过滤 | KEEP | 多来源 Link 约束测试 |
| Audit | 只追加 Hash 链、并发串行、校验 | `audit/service.py` | COMPLETE | 可原样复用 | 真实备份/WORM 非目标 | KEEP | Audit 篡改/并发测试 |
| 错误处理 | Run/Action 明确终态、用户安全消息 | `agent/service.py`、`agent/status.py`、API handler | COMPLETE | 顶层处理保留 | Graph 异常统一标记 executing Action 为 execution_unknown | KEEP | Graph 异常测试 |
| 取消与恢复 | cancelled 优先、晚到结果丢弃、不重放未知执行 | `agent/service.py`、`agent_runs.py`、`agent/status.py` | COMPLETE | 主体保留 | Run/Collector 租约与取消使用原子条件更新 | KEEP | cancel/lease 回归 |
| 前端聊天 | 乐观消息、轮询、自动滚动、会话/环境隔离 | `WorkspacePage.tsx` | COMPLETE | 页面风格和布局保留 | 浏览器真实 E2E 尚未执行 | TEST_ONLY | uiState、浏览器 E2E |
| 前端运行状态 | Run/Step/Action/Evidence/Collector 展示及 Environment/Connection 管理 | `ActivityPanel`、`ConfigPanel` | COMPLETE | 现有面板保留 | 长流程和配置 CRUD 的真实浏览器 E2E 尚未执行 | TEST_ONLY | 前端状态测试/E2E |
| 前端审批 | 自然语言、去重点击、即时状态 | `ApprovalCard` | COMPLETE | 当前交互保留 | cancelled/invalidated/expired 均有展示 | KEEP | UI 状态/E2E |
| 数据库迁移 | 空库、升级/降级、约束 | `backend/alembic` | PARTIAL | 迁移链保留并新增三次迁移 | 单 Head 和离线 SQL 已通过，当前沙箱未完成真实往返 | TEST_ONLY | migration round trip |
| Docker 部署 | 四服务、健康、持久化、秘密挂载 | `docker-compose.yml`、Dockerfiles | PARTIAL | 当前部署复用 | 缺隔离测试 SSH/HTTP profile | MODIFY | config/build/up/health |
| 自动化测试 | 单元、集成、E2E、覆盖率、真实矩阵 | `backend/tests`、`frontend/tests`、CI | PARTIAL | Fixtures 和 CI 保留扩展 | 测试代码已补；本机后端/Docker/浏览器 E2E 受环境阻塞 | TEST_ONLY | 最终报告 |
| 旧架构残留 | 不保留关键词 Router、旧 RAG、CommandRun | 全仓库引用扫描 | COMPLETE | 无旧主链 | 两个发送入口和 `/execute` 是兼容 API，不是旧 Agent | MODIFY | API 契约回归 |

## 结论

现有模块大多有真实调用入口并已按设计加固，没有推翻重写。代码层面的主要安全差距已经补齐；发布结论仍取决于新迁移、后端全量测试、真实 Runtime 和浏览器 E2E 在具备依赖与 Docker 权限的环境中通过。
