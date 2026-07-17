# 最终架构实现状态

基线：`main@4103be68fdd8d55bb33419e84fd9c8381cb968ef` 加当前未提交实现，2026-07-16。

## 核心链路

```text
React 提交消息和 client_request_id
→ FastAPI 创建 queued AgentRun
→ Worker 原子领取并维护租约
→ LangGraph 获取项目、环境、Context 和 verified Experience
→ LLM 输出结构化 Decision
→ Registry 按 name/version/definition hash 解析 Capability
→ Policy 校验角色、范围、风险和审批模式
→ Action 冻结最终执行、治理和恢复快照
→ read 直接执行；change 等待绑定 Action Hash 的 Approval
→ 审批后复核 Registry、Policy、配置修订和 precheck
→ Runtime Adapter 通过 SSH/HTTP 执行确定性操作
→ 专用 verifier 解析真实最终状态
→ 保存 Invocation、Evidence、Claim、Audit
→ 返回基于来源的自然语言回答
```

## 安全不变量实现

| 不变量 | 当前实现 | 状态 |
|---|---|---|
| LLM 不能执行任意 Shell | 只接受 Registry Capability，Adapter 构造固定 argv | IMPLEMENTED |
| Capability 定义不能静默漂移 | name/version 唯一并持久化 definition hash，启动同步拒绝同版本漂移 | IMPLEMENTED |
| Action 使用不可变最终快照 | 冻结主能力、关联能力、Runtime、目标、连接、配置和恢复配方 | IMPLEMENTED |
| Action Hash 覆盖治理语义 | 覆盖 Policy version、risk、approval mode、config revision | IMPLEMENTED |
| 审批不能改变 Action | Approval 只保存决定并绑定唯一 Action Hash | IMPLEMENTED |
| 同一 Action 只执行一次 | Action CAS 进入 executing，Approval 在领取执行时消费 | IMPLEMENTED |
| verifier 缺失或无法解析失败 | Registry 编译与运行时双重 fail closed | IMPLEMENTED |
| succeeded 不等于 verified | 执行与验证状态分开，验证 Evidence 独立保存 | IMPLEMENTED |
| cancelled 不被晚到结果覆盖 | Run/Collector 原子终态、全部远端阶段 token 所有权检查 | IMPLEMENTED |
| 未知执行不自动重放 | 租约过期或执行中取消将 executing Action 标记 execution_unknown，并关闭未开始 Action | IMPLEMENTED |
| Claim 只链接真实来源 | Runtime/Context/Experience 独立来源列、精确唯一约束和 Run 过滤 | IMPLEMENTED |
| Audit 只追加且可校验 | 数据库触发器拒绝更新/删除，服务维护 Hash 链 | IMPLEMENTED |

## 能力成熟度

| 等级 | 能力 | 依据 |
|---|---|---|
| Stable 候选 | 认证、项目/环境/连接/会话、异步 Run API、结构化 Decision Schema、Registry 编译 | 入口和回归测试存在，当前工作区仍需全量后端验收 |
| Beta | LangGraph 调查、Docker 读与变更、Approval、Context/Experience、Evidence/Claim/Audit、React 工作台 | 代码链完整，但当前 Commit 的全量后端、迁移和真实 E2E 尚未完成 |
| Experimental | Kubernetes、systemd、Host 和注册配置/部署的真实执行 | 有确定性实现和测试代码，缺隔离目标环境验收 |
| Planned | 向量经验检索优化、Kubernetes/systemd 测试环境产品化 | 不影响当前主链，不作为已实现能力宣传 |

## 当前发布边界

- 本地前端开发与静态演示：可用，当前环境已通过单元测试和生产构建。
- 完整本地 Compose：配置有效，但当前沙箱无法访问 Docker API，需在正常 WSL 终端复验。
- 测试服务器只读诊断：必须先通过迁移、后端全量测试、SSH 指纹和真实只读链验收。
- 生产自动变更：当前为 `NO-GO`；至少需要目标环境的真实变更/验证/恢复演练、凭据治理、备份和持续 CI 绿色记录。

## 未完成的验收

实现缺失与环境阻塞必须区分。当前没有已知 P0 代码设计缺口，但以下测试尚未在本执行环境完成：

1. 三次新迁移的空库升级、降级和再次升级。
2. 后端全量 pytest、覆盖率、Ruff 与 mypy。
3. 真实 Docker Compose start/stop/restart/scale/unhealthy 矩阵。
4. API 连接的浏览器 E2E。
5. Kubernetes、systemd 和真实外部 LLM 验收。

权威测试状态见 `test-results/10-final-report.md`，不得仅依据本文件宣称发布通过。
