# 安全测试

| 安全边界 | 实现/测试代码 | 本次状态 |
|---|---|---|
| Capability 精确三元组 | Registry、Graph、Executor 和漂移测试 | PARTIAL |
| Action 完整治理 Hash | 单元与审批漂移测试 | PARTIAL |
| 未知 verifier fail closed | 纯函数/Executor 负向测试 | PARTIAL |
| Approval 并发一次决定/消费 | PostgreSQL 并发测试 | BLOCKED |
| Action 一次执行 | CAS 与 Graph 回归测试 | BLOCKED |
| 远端晚到结果隔离 | precheck/执行/verifier/rollback token 与 Run 终态校验 | BLOCKED |
| Claim 精确来源 | 数据库 Check/partial unique 测试 | BLOCKED |
| Audit append-only/Hash chain | PostgreSQL trigger/verifier 测试 | BLOCKED |
| SSH Host Key/输出/超时 | 临时 SSH Server 测试 | BLOCKED |
| HTTP SSRF/redirect/大小 | 临时 HTTP Server 与单元测试 | BLOCKED |
| 密钥不入 Git | tracked/ignore 路径扫描 | PASS |

`PARTIAL` 表示代码和测试路径已建立，且部分可静态检查，但依赖数据库的负向断言没有在本环境实际运行。
