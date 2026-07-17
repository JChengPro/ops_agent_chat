# 旧实现清理记录

| 旧模块 | 当前引用 | 替代模块 | 处理方式 | 数据影响 | API 影响 | 验证结果 |
|---|---|---|---|---|---|---|
| 关键词 Intent Router | 未发现 | LLM `AgentDecision` + Registry/Policy | REMOVE（历史已删除） | 无 | 无 | 全仓库引用扫描 |
| 旧主 RAG Pipeline | 未发现 | 通用 LLM + Context + Experience + Runtime | REMOVE（历史已删除） | 旧表迁移策略需发布确认 | 旧 RAG API 不存在 | 全仓库引用扫描 |
| CommandRun/自由命令执行 | 未发现 | Action + Capability + Runtime Adapter | REMOVE（历史已删除） | 无 | 无 | Registry 无自由 Shell |
| POST `/chat-sessions/{id}/messages` | 后端兼容入口 | POST `/agent-runs` | MODIFY（废弃周期） | 无 | 已增加 Deprecation/Sunset 头和 OpenAPI 标记 | API 回归测试已补，待当前环境执行 |
| POST `/agent-runs/{id}/execute` | 测试和兼容客户端可能使用 | Worker 自动领取 | MODIFY（废弃周期） | 无 | 永不直接执行 Graph | 现有回归已覆盖 |
| 历史问题流水账 | 无运行引用 | 当前设计与最终报告 | REMOVE（已删除） | 无 | 无 | 文档引用检查通过 |

本轮引用扫描未发现旧关键词 Router、主 RAG Pipeline、CommandRun 或自由 Shell 入口。两个兼容 API 仍有明确迁移价值，因此没有提前删除；后续只应在外部客户端完成迁移且 API 回归通过后移除。
