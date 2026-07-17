# 数据库与迁移测试

| 项目 | 状态 | 说明 |
|---|---|---|
| 单一 Alembic head | PASS | `d7f2a9c4e681` |
| 空库 upgrade | PASS | 全部迁移从 base 执行到 head |
| downgrade/upgrade round trip | PASS | `upgrade head → downgrade base → upgrade head`，退出码 0 |
| 状态、来源和幂等约束 | PASS | 包含于后端全量 pytest |
| Audit append-only trigger | PASS | UPDATE 被数据库触发器拒绝 |
| Audit 历史分叉收敛 | PASS | 不修改旧事件，新 v2 事件绑定全部旧链头并恢复可验证状态 |

新增迁移 `d7f2a9c4e681` 为审计事件增加 `hash_version` 和多父哈希快照。旧事件继续使用 v1 哈希算法，新事件使用 v2 算法。
