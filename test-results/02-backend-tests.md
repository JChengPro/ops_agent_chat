# 后端测试

执行日期：2026-07-17。

| 检查 | 状态 | 结果 |
|---|---|---|
| Python compileall | PASS | `backend/app`、`backend/tests`、迁移文件编译成功 |
| pytest 全量 | PASS | 在隔离测试数据库执行：`108 passed, 1 skipped`，退出码 0 |
| 审计分叉回归 | PASS | 旧分叉先报告 multiple_heads，新事件追加式合并后恢复唯一链头 |
| 通用聊天真实 API | PASS | 真实 DeepSeek 请求；`你好` 的 Run 为 completed，并生成自然语言回复 |
| 本地 SSH Transport | PASS | Paramiko 测试服务器覆盖成功、非零退出和输出上限 |
| 不可重试 SSH 错误 | PASS | 私钥缺失被分类为 `ssh_credential_missing`，Graph 只调用一次并返回中文恢复步骤 |
| Ruff / mypy | FAIL | 已执行；Ruff 223 项、mypy 75 项，主要是当前仓库既有格式和类型债务；本次新增 Optional 提示已修正 |
| 覆盖率 | NOT_TESTED | 本轮未生成覆盖率报告 |

唯一跳过项是需要宿主 Docker CLI 和 Socket 的真实 Docker Compose Adapter 集成测试；应用容器未挂载宿主 Docker Socket。
