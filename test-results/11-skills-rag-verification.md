# Skills、RAG 与稳定验证测试报告

日期：2026-09-09
代码基线：`main@2090b44490999993fb807a84c010abe4e22f1841` 加当前未提交实现。

## 实现范围

- 版本化 Skill Registry、结构化 Skill 选择、无 Skill 回退和 Capability 交集收窄。
- Markdown 标题感知分块、稳定 Chunk ID、内容 Hash 和增量索引。
- OpenAI-compatible Embedding、pgvector 1536 维列、HNSW 索引、词法/向量 RRF 混合检索和词法降级。
- 自适应 OpenAI-compatible LLM listwise rerank、严格结果校验、短期缓存、ModelCall 记录和 RRF 失败降级。
- 项目与 Environment 检索隔离、每文档分块上限和上下文软预算。
- 开发者维护、用户只读的系统内置知识库及只读 Capability/API/前端展示。
- 服务和部署变更的多次验证、连续成功门槛及独立验证 Evidence。

## 实际测试

| 项目 | 状态 | 结果 |
|---|---|---|
| Python `compileall` | PASS | 后端应用与测试无语法错误 |
| Ruff 关键错误规则 | PASS | `E9,F63,F7,F82` 无错误 |
| 本轮模块 mypy | PASS | experience、reranking、context API 与 runtime executor 无类型错误 |
| 无数据库后端回归 | PASS | 56 passed |
| Capability/Skill/System Knowledge 启动编译 | PASS | 23 Capability、2 Skill、8 条系统知识 |
| Alembic 静态迁移链 | PASS | 单一 head `f2c7d1a9e483` |
| 前端单元测试 | PASS | 11 passed |
| 前端生产构建 | PASS | TypeScript 与 Vite 构建成功 |
| 前端静态浏览器 E2E | PASS | `E2E_SKIP_API=1 npm run test:e2e` |
| 评测集校验 | PASS | 50 条；35 dev、15 holdout |
| 当前词法 baseline | PASS | 15 条：Recall@5 0.600，MRR 0.433 |
| 候选混合前词法改进 | PASS | 15 条：Recall@5 1.000，MRR 0.856；仅限当前小语料，不代表生产效果 |
| 系统知识检索 | PASS | 8 条：Recall@3 1.000，MRR 0.938 |
| 后端全量 pytest | PASS | Docker 隔离测试库：166 passed、1 skipped |
| Rerank 与检索回归 | PASS | 覆盖排序、响应校验、失败降级、缓存、ModelCall、环境隔离、多样性和上下文预算 |
| Alembic 空库往返 | PASS | empty → head → base → head，当前 head `f2c7d1a9e483` |
| Docker Compose 构建/运行 | PASS | backend、worker、frontend、postgres 均运行，backend 与 postgres healthy |
| 生产兼容 Chunk 检索 | PASS | 15 条：Recall@5 1.000、MRR 1.000、nDCG 0.969；真实 PostgreSQL 结果一致 |
| 真实数据库检索延迟 | PASS | 均值 5.84ms、P50 5.55ms、P95 12.06ms |
| 自适应 Rerank | PASS | 当前 3–4 个唯一来源不超过 Top-K，15/15 跳过，质量不变且不增加模型延迟 |
| 强制 DeepSeek Rerank | PASS | 15 条、0 失败；Recall/MRR/Precision 不变，nDCG 0.9689→0.9742；1 条提升、14 条不变、0 条下降 |
| 强制 Rerank 延迟 | PARTIAL | 均值 12.20s、P50 10.99s、P95 26.31s；收益不足以支持当前语料每次调用 |
| DashScope 用户模型配置 | PASS | 前端保存 `dashscope / qwen-plus` 后，服务端密文读取配置并完成一次真实 Chat Completion |
| DashScope Embedding 链路 | PASS | `qwen3.7-text-embedding` 显式请求 1536 维；按 20 条分批，6 份文档 71/71 Chunk 向量化，失败 0 |
| 真实混合检索 | PASS | PostgreSQL 查询实际返回 `hybrid_rrf`；证明词法与向量通道均参与候选融合 |

## 阻塞项

| 项目 | 状态 | 证据与影响 |
|---|---|---|
| Embedding 质量增益 | NOT_TESTED | 真实 API 链路已通过，但尚未在固定 Gold 上成对比较 lexical/vector/hybrid 的质量、延迟和成本 |
| 最终回答质量评测 | NOT_TESTED | 当前结果只证明检索排序变化，没有使用人工或 LLM Judge 评估 Claim 正确性、忠实度和回答相关性 |
| 大规模语料评测 | BLOCKED | 当前项目文档语料很小；15 条检索用例不足以外推生产效果 |

## 结论

当前词法路径在小语料上已经达到 Recall@5 与 MRR 1.000。强制 DeepSeek rerank 只带来 nDCG `+0.00535`，却平均增加 12.20 秒，因此生产策略按唯一来源数自适应跳过；实现仍保留给更大语料和后续专用模型。真实 DashScope Embedding 工程链路已验收，但其质量增益、最终回答质量和大规模盲测仍未验收，整体结论为 `CONDITIONAL GO`。完整依据见 `docs/rag/RAG_ENGINEERING_DECISIONS_AND_EXPERIMENTS.md`。
