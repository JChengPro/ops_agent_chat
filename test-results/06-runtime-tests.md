# Runtime 测试

| 项目 | 状态 | 结果 |
|---|---|---|
| 严格 Docker verifier 纯函数矩阵 | PASS | 9/9：healthy、无 healthcheck、starting、unhealthy、exited、畸形、scale 正确/不足、stop |
| Docker Compose Adapter | BLOCKED | 真实临时 Compose 测试代码已补，Docker socket 被拒绝 |
| VideoHub 只读 `service.list` | PASS | Backend 容器通过 SSH 进入已登记 workdir，解析 7 个实例：3 running、3 healthy |
| SSH Transport | PASS | 真实 Paramiko Server 覆盖成功、非零退出、输出截断、错误私钥、指纹错误、超时和不可达主机 |
| SSH 凭据挂载恢复 | PASS | 强制重建 Backend/Worker 后 `/run/secrets/videohub_ssh_key` 可读；缺失时返回稳定错误码并停止重试 |
| HTTP Runtime | PASS | 隔离 HTTP Server 的成功、失败、超时和取消路径由全量 pytest 覆盖 |
| Kubernetes | BLOCKED | 无隔离集群 |
| systemd | BLOCKED | 无隔离目标 |

VideoHub 只读检查证明当前 SSH、工作目录、Docker CLI 和输出解析链路可用；未对 VideoHub 执行 start、stop、restart 等状态变更。
