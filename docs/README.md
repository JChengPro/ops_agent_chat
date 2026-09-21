# 文档索引

文档以当前代码事实为起点。设计目标与当前实现不一致时，必须在文档中标记差距，不能把 Planned 写成 Stable。

## 当前主设计

[Ops Agent Chat 详细设计](architecture/CURRENT_DESIGN.md) 是当前详细架构入口，按总体设计、子系统设计、部署与验证组织。[根 README](../README.md) 负责概览和快速开始，专项文档负责深入实现与实验记录。已被覆盖的早期架构、部署与一次性实施报告已清理，历史版本可从 Git 查询。

## 专项说明

- [部署与配置](architecture/CURRENT_DESIGN.md#s17)：八服务部署、模型、PostgreSQL、迁移与回退入口。
- [实现决策](implementation/02-implementation-decisions.md)：保留安全设计依据，已被替代的调度决策单独标明。
- [系统内置知识](implementation/05-system-knowledge-expansion.md)：系统知识的组织、发布与验证。
- [AgentRun Profiling](implementation/06-agentrun-profiling.md)：独立耗时记录、查询接口与计时边界。
- [Redis、RabbitMQ 与 Knowledge Path](implementation/07-redis-rabbitmq-knowledge-path.md)：当前投递、缓存、知识路径及配置回退；服务职责以此升级说明与根目录 README 为准。
- [RAG 工程实验](rag/RAG_ENGINEERING_DECISIONS_AND_EXPERIMENTS.md)：历史检索实验、参数与质量取舍；缓存现状以主设计为准。
- [测试验收清单](review/TEST_ACCEPTANCE_CHECKLIST.md)：代码和发布验收要求。
- [本轮性能与故障实验](../test-results/13-v2-latency.md)：同模型前后对照、Redis/RabbitMQ 故障恢复与验证边界。
- [原架构验收记录](../test-results/10-final-report.md)：历史测试结果，不代表当前版本的验收状态。

## 教学与知识资料

- [源码深度教学](tutorial/README.md)：旧代码基线的源码导读和配图，用于理解设计；新增消息队列、缓存和知识路径见当前主设计。PDF 按需导出，不纳入 Git。
- [VideoHub 经验种子](knowledge/videohub/README.md)：会被初始化代码导入 Experience Store，并被 RAG 评测引用，不是可随意删除的说明文档。

## 维护约定

当前行为只在主设计和对应专项中维护，避免另建“最终状态”或重复部署说明。实验记录保留测量基线，旧结论被替代时注明新实现入口；一次性测试输出放在 `test-results/`。教学材料必须标注代码版本，生成文件与缓存不纳入 Git。

## 状态定义

| 状态 | 定义 |
|---|---|
| Stable | 有实际入口、关键自动化测试，且没有已知阻断该声明的设计缺口 |
| Beta | 端到端入口存在，但真实运行验收、边界覆盖或安全加固仍不完整 |
| Experimental | 有代码入口，但缺少目标环境的真实验收，不作为稳定承诺 |
| Planned | 只有正式设计要求或修复计划，尚未实现 |

一次性审查报告和历史问题流水账不作为长期文档保留，也不能替代当前 Commit 的测试报告。
