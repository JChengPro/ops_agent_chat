# Skills、RAG 与稳定验证测试报告

日期：2026-09-03  
代码基线：`main@3c8c46c5b3d4ccaf99a85633328988513bf741ba` 加当前未提交实现。

## 实现范围

- 版本化 Skill Registry、结构化 Skill 选择、无 Skill 回退和 Capability 交集收窄。
- Markdown 标题感知分块、稳定 Chunk ID、内容 Hash 和增量索引。
- OpenAI-compatible Embedding、pgvector 1536 维列、HNSW 索引、词法/向量 RRF 混合检索和词法降级。
- 开发者维护、用户只读的系统内置知识库及只读 Capability/API/前端展示。
- 服务和部署变更的多次验证、连续成功门槛及独立验证 Evidence。

## 实际测试

| 项目 | 状态 | 结果 |
|---|---|---|
| Python `compileall` | PASS | 后端应用与测试无语法错误 |
| Ruff 关键错误规则 | PASS | `E9,F63,F7,F82` 无错误 |
| 安全模块 mypy | PASS | verification 与 action_hash 无类型错误 |
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

## 阻塞项

| 项目 | 状态 | 证据与影响 |
|---|---|---|
| 后端全量 pytest | BLOCKED | `conftest.py` 创建测试库时 `psycopg.OperationalError: connection is bad`；当前 WSL 无 PostgreSQL |
| 迁移空库往返 | BLOCKED | 无可连接 PostgreSQL；仅验证了静态单 head 和 revision 链 |
| Docker Compose 构建/运行 | BLOCKED | Docker CLI 报当前 WSL 发行版未启用 Docker Desktop integration，且 `/var/run/docker.sock` 不存在 |
| 真实 Embedding API | BLOCKED | 未使用真实 API Key；已测试维度校验、查询构造和失败降级 |
| API 联通浏览器 E2E | BLOCKED | 后端依赖 PostgreSQL，当前只执行无 API 的静态浏览器烟雾测试 |

## 结论

本轮实现通过可在当前环境执行的静态、单元、构建、浏览器烟雾和离线评测。数据库迁移、Graph 数据库集成、Compose 和真实 Runtime 尚未执行，因此整体只能标记为 `CONDITIONAL GO`，不能宣称完整上线验收通过。
