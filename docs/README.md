# 文档索引

文档以当前代码事实为起点。设计目标与当前实现不一致时，必须在文档中标记差距，不能把 Planned 写成 Stable。

## 仓库文档

- `architecture/PROJECT_STRUCTURE.md`：当前代码目录与入口速查。
- `config/DEEPSEEK_CONFIG.md`：当前 OpenAI-compatible LLM 配置。
- `database/POSTGRES_PGVECTOR_SETUP.md`：PostgreSQL 和迁移边界。
- `deployment/DOCKER_ONE_CLICK_DESIGN.md`：Compose 启动与服务职责。
- `implementation/01-design-code-mapping.md`：设计要求与真实代码映射。
- `implementation/02-implementation-decisions.md`：本轮实现决策。
- `implementation/03-legacy-cleanup.md`：旧实现引用与清理结论。
- `implementation/04-final-architecture-status.md`：最终架构、安全不变量和成熟度。
- `knowledge/videohub/`：默认项目经验种子，不是主设计文档。
- `review/TEST_ACCEPTANCE_CHECKLIST.md`：代码和发布验收清单。
- `../test-results/10-final-report.md`：本次实际测试、阻塞项和发布判断。

## 状态定义

| 状态 | 定义 |
|---|---|
| Stable | 有实际入口、关键自动化测试，且没有已知阻断该声明的设计缺口 |
| Beta | 端到端入口存在，但真实运行验收、边界覆盖或安全加固仍不完整 |
| Experimental | 有代码入口，但缺少目标环境的真实验收，不作为稳定承诺 |
| Planned | 只有正式设计要求或修复计划，尚未实现 |

一次性审查报告和历史问题流水账不作为长期文档保留，也不能替代当前 Commit 的测试报告。
