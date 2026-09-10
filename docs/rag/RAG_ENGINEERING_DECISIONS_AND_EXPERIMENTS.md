# RAG 工程决策与实验记录

## 1. 结论摘要

本轮目标不是单独“加一个 reranker”，而是让项目文档检索在准确性、延迟、隔离性、可降级和可评测之间形成闭环。

当前结论：

- 保留 `1800` 字符分块和 `160` 字符重叠。现有 4 份短文档在 `800/80`、`1200/120`、`1800/160`、`2400/200` 下结果完全相同，没有数据支持改动默认分块。
- 保留 Top-K=`5`，同一文档最多返回 `2` 个分块，并设置 `9000` 字符软预算。
- 不启用相对分数阈值。阈值虽提高 Precision，却把 Evidence Recall 从 `1.000` 降到 `0.967`。
- 实现 LLM listwise rerank，但按候选中的唯一来源数自适应跳过。当前 4 个来源少于 Top-K，因此不调用 reranker。
- DeepSeek 强制 rerank 只把 nDCG 从 `0.9689` 提升到 `0.9742`，Recall、MRR、Precision 均无提升；平均增加 `12.20s`、P95 增加 `26.31s`。当前语料下不值得支付该延迟。
- 已使用 DashScope `qwen3.7-text-embedding` 完成真实连通、1536 维校验、分批入库和 PostgreSQL 混合检索验证；当前数据库 `71/71` 个分块具有向量。该结果证明工程链路可用，不等于向量检索质量已经优于词法 baseline。

这不是“永远不使用 rerank”。当候选来源超过 Top-K 时系统仍可调用 reranker；后续接入专用 rerank 模型后，应使用新的盲测集重新决定默认策略。

## 2. 评测边界

代码基线：`main@2090b44490999993fb807a84c010abe4e22f1841` 加本轮未提交实现。

数据集：`evals/rag/golden-v1.jsonl` 中 15 条项目文档检索用例；语料为 4 份 VideoHub 项目文档、9 个生产分块。

本轮测量的是检索和排序，不是最终回答准确率。指标含义：

| 指标 | 含义 | 本项目关注点 |
|---|---|---|
| Evidence Recall@5 | 必需证据有多少进入前 5 | 运维回答不能漏掉关键前提 |
| Precision@5 | 返回分块中有多少直接覆盖 Gold | 控制无关上下文 |
| MRR | 第一条有效证据的位置 | 关键证据是否靠前 |
| nDCG | 多条必需证据的整体排序质量 | 多证据问题是否顺序合理 |
| Context chars | 送入后续 Agent 的正文字符数 | Token、延迟和噪声代理指标 |
| P50/P95 | 中位与尾部延迟 | 用户实际等待体验 |

限制：当前语料和用例较小；现有 holdout 已在开发过程中被查看，不再是严格盲测；真实 Embedding 已完成链路验证，但尚未在固定 Gold 上完成 lexical/vector/hybrid 成对质量与延迟对比；没有用独立 Judge 评估最终 Claim 正确性和忠实度。

## 3. 实现链路

```text
用户问题
  -> 项目 + Environment + verified 过滤
  -> PostgreSQL 词法候选
  -> 可选 pgvector 向量候选
  -> RRF 融合
  -> 唯一来源数判断
       <= Top-K: 跳过远程 rerank
       >  Top-K: 缓存命中或调用 reranker
  -> 每文档最多 2 块 + 9000 字符软预算
  -> Experience Evidence
  -> Agent 基于 Evidence 生成 Claim
```

项目文档只提供历史和配置知识，不能证明当前运行状态。当前状态仍必须由 Runtime Capability 获取。

## 4. 决策与证据

### D1：评测必须复用生产分块

早期 source-level evaluator 把整份文档当候选，得到 MRR `0.856 -> 0.967`，但它没有经过生产 `chunk_document()`，高估了 rerank 收益。本轮新增生产兼容 evaluator，Gold 按 `source_id + section` 匹配，避免“找对文件但找错章节”也算正确。

为什么不继续用旧评测：RAG 实际送给模型的是 Chunk，不是完整文件；文件级指标无法发现同一文档内的章节排序问题。

### D2：暂不改变 Chunk Size 与 Overlap

| 配置 | Recall@5 | MRR | nDCG | Precision@5 | 平均字符 |
|---|---:|---:|---:|---:|---:|
| 800 / 80 | 1.000 | 1.000 | 0.969 | 0.233 | 1060 |
| 1200 / 120 | 1.000 | 1.000 | 0.969 | 0.233 | 1060 |
| 1800 / 160 | 1.000 | 1.000 | 0.969 | 0.233 | 1060 |
| 2400 / 200 | 1.000 | 1.000 | 0.969 | 0.233 | 1060 |

四组结果相同，是因为现有章节都短于 800 字符。继续调整只是在没有长文档样本时猜参数，因此保留已有 `1800/160`。重新评估触发条件：加入长 runbook、单章节超过 1800 字符或跨块证据用例。

### D3：使用字段感知的确定性词法排序，但不宣称提升

实现对 Query term coverage、标题、标题路径和正文分别计分；标题路径权重高于正文重复次数。这样“SSH 主机指纹”章节不会仅因另一段重复出现 `SSH` 而被压后。

当前数据上，普通词频与字段加权结果完全相同。因此保留它的理由是确定性、低成本和语义更明确，不是已经测得准确率提升。若新盲测无收益且维护成本上升，应删除而非继续堆权重。

### D4：不部署相对分数阈值

| 相对阈值 | Recall@5 | Precision@5 | nDCG | 平均字符 |
|---:|---:|---:|---:|---:|
| 0.00 | 1.000 | 0.233 | 0.969 | 1060 |
| 0.25 | 0.967 | 0.463 | 0.948 | 744 |
| 0.50 | 0.967 | 0.596 | 0.974 | 568 |
| 0.75 | 0.967 | 0.744 | 0.974 | 394 |

阈值明显减少上下文，但漏掉了 `3.3%` 的必需证据。运维场景中，少一条必要约束可能导致错误建议，其代价高于多传约 500 字符，所以不部署阈值。

### D5：同一文档最多 2 个分块

| 每文档上限 | Recall@5 | Precision@5 | nDCG | 重复率 | 平均字符 |
|---:|---:|---:|---:|---:|---:|
| 1 | 0.967 | 0.278 | 0.948 | 0.000 | 902 |
| 2 | 1.000 | 0.233 | 0.969 | 0.220 | 1060 |
| 5 | 1.000 | 0.230 | 0.969 | 0.230 | 1068 |

上限 1 会漏掉同一文档中的第二条必要证据；上限 5 没有质量收益。选择 2 是当前数据中召回与重复内容之间最小的安全值。

### D6：上下文使用软字符预算

| 预算 | Recall@5 | Precision@5 | nDCG | 平均字符 |
|---:|---:|---:|---:|---:|
| 500 | 0.967 | 0.456 | 0.948 | 471 |
| 1000 | 1.000 | 0.263 | 0.969 | 897 |
| 3000 | 1.000 | 0.233 | 0.969 | 1060 |
| 9000 | 1.000 | 0.233 | 0.969 | 1060 |

当前 1000 字符也能保住召回，但样本太短，不能据此压缩未来多证据问题。默认 9000 是最多 5 个 1800 字符分块的上界；选择器至少保留第一块，因此它是避免后续块继续膨胀的软预算，不会从中间截断证据。待长文档盲测后可重新评估 3000 或 Token 预算。

### D7：Rerank 采用“来源竞争”而非“分块数量”触发

现有语料每题有 `4–9` 个候选分块，但只有 `3–4` 个唯一文档来源。Top-K 为 5 时，无论 rerank 如何排序，所有来源都已经有机会进入结果；为同一来源内部的小幅排序支付一次远程 LLM 调用不经济。

因此只有唯一来源数大于 Top-K 才调用 reranker。这个规则比“候选分块数 > Top-K”更贴近 rerank 的实际价值。

### D8：DeepSeek 强制 Rerank 的收益不足以覆盖延迟

| 模式 | Recall@5 | MRR | nDCG | Precision@5 | Rerank 平均 / P95 |
|---|---:|---:|---:|---:|---:|
| 生产自适应 | 1.000 | 1.000 | 0.9689 | 0.233 | 0 / 0 ms，15/15 跳过 |
| 强制 DeepSeek | 1.000 | 1.000 | 0.9742 | 0.233 | 12.20 / 26.31 s |

强制 rerank 仅改善 `multi-005` 一题，14 题不变、0 题下降；总计额外等待 `183.02s`。真实 PostgreSQL 检索本身平均 `5.84ms`、P50 `5.55ms`、P95 `12.06ms`。

结论：当前语料使用自适应跳过。保留 rerank 接口，是为了后续来源规模增大和专用模型接入，不是为了让每次查询都多一次 DeepSeek 调用。

### D9：缓存必须同时绑定 Query、模型和 Chunk 内容版本

进程内缓存采用 TTL + LRU，默认 `300s / 256` 项。Key 包含项目、Environment、provider、model、规范化 Query、候选 `chunk_key + content_hash`；Key 自身再做 SHA-256，不保存明文查询。

为什么不能只缓存 Query：文档编辑、项目切换、环境切换或模型切换后，旧排序可能已失效。为什么暂不引入 Redis：当前只有单 Worker 的本地部署，没有跨实例缓存收益数据；先避免增加基础设施。多 Worker 部署时可重新评估共享缓存和请求合并。

### D10：Rerank 失败时回退检索，不让 AgentRun 失败

Reranker 必须返回全部且仅返回候选 ID，拒绝重复、未知、缺失 ID 和越界分数。超时、API 错误或结构错误时保留 RRF 顺序，并记录 `ModelCall(purpose=retrieval_rerank)`。

这里采用 fail open to retrieval，不是 fail open to execution：失败只影响知识排序，不会授予 Capability、改变 Policy 或绕过审批。让只读排序故障中断整个运维调查反而降低可用性。

### D11：项目和 Environment 作用域在数据库查询前过滤

项目 ID、`verified` 状态和 Environment 在 SQL 层过滤。未选择 Environment 时只检索项目级文档；选择后只检索项目级文档加该 Environment 文档，不允许其他环境内容先进入候选再由 LLM 判断。

原因：权限和数据隔离必须由确定性代码保证，不能依赖模型“不要泄漏”的提示词。

### D12：Embedding 保持可选，并区分链路验收与质量验收

代码支持 OpenAI-compatible Embedding、pgvector 和 RRF；未配置或失败时稳定降级到词法检索。使用 DashScope `qwen3.7-text-embedding` 实测时发现两个仅靠 Mock 难以暴露的问题：模型默认输出 1024 维，而数据库固定为 1536 维；单个 42 Chunk 文档超过 DashScope 每次最多 20 条的限制。

因此 Provider 显式请求 `dimensions=1536`，并使用默认 20 条的小批量调用；旧 Chunk 内容未变化但向量为空时也会回填。真实结果为 API 返回 1536 维、6 份文档共 71 个 Chunk 全部入库、失败数 0，查询返回 `hybrid_rrf`。为什么不拿 DeepSeek Chat 假装 Embedding：Chat Completion 不是向量模型；伪向量会让指标失真。

这仍不能证明 Hybrid 的准确率更高。现有 Recall/nDCG 表来自此前固定的词法评测，下一步必须在同一 Gold、同一候选上限下成对运行 lexical、vector、hybrid，才能决定是否默认承担向量 API 的延迟和成本。

### D13：DashScope / Qwen 接入问题复盘

本次接入包含两条彼此独立的模型链路，不能只配置其中一条：

| 链路 | 配置入口 | 当前模型 | Key 保存位置 | 用途 |
|---|---|---|---|---|
| 用户聊天模型 | 前端“模型设置” | `qwen-plus` | `user_llm_settings.api_key_encrypted`，服务端加密保存 | Agent 结构化决策、回答和可选 LLM rerank |
| 文档向量模型 | 服务端 `.env` | `qwen3.7-text-embedding` | 仅部署环境变量，不进入数据库 | 文档和查询向量化 |

实际使用的非敏感配置如下，真实 Key 不得写入文档或提交 Git：

```dotenv
EMBEDDING_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
EMBEDDING_MODEL=qwen3.7-text-embedding
EMBEDDING_DIMENSIONS=1536
EMBEDDING_BATCH_SIZE=20
LLM_ALLOWED_BASE_URLS=https://api.deepseek.com,https://api.openai.com/v1,https://dashscope.aliyuncs.com/compatible-mode/v1
```

接入过程中遇到的问题及处理：

| 问题 | 表现 | 根因 | 处理 | 验证结果 |
|---|---|---|---|---|
| 前端无法选择 DashScope | 供应商是固定下拉框，不能手工填写 `dashscope` | 前端选项只包含原有供应商 | 增加“DashScope（通义千问）”，选择后填充兼容地址，并将默认聊天模型切换为 `qwen-plus` | 数据库保存的 provider、URL、model 正确 |
| Base URL 被服务端拒绝 | 即使前端填写正确也无法保存 | 模型地址不在 `LLM_ALLOWED_BASE_URLS` | 默认允许列表和 `.env.example` 增加 DashScope 兼容地址 | 设置 API 校验通过 |
| 1536 维配置未生效 | API 可访问，但报 `unexpected vector shape` | Provider 只校验 `EMBEDDING_DIMENSIONS`，请求时没有把 `dimensions` 传给模型；模型默认返回 1024 维 | 调用 Embedding API 时显式发送 `dimensions=1536` | 真实 API 返回 1536 维 |
| 长文档整批向量化失败 | 42 个 Chunk 全部失败并降级词法检索 | DashScope 该模型单次最多接受 20 条输入，代码一次提交全部 Chunk | 增加 `EMBEDDING_BATCH_SIZE`，默认按 `20/20/2` 分批并保持结果顺序 | 42 个 Chunk 回填失败数为 0 |
| 历史 Chunk 始终没有向量 | 配置 Key 后仍显示部分或全部向量为空 | 增量索引把内容未变化视为无需处理，没有识别“内容未变但 embedding 为空” | 未变化 Chunk 的向量为空时加入回填队列 | 数据库最终为 `71/71` 个 Chunk 有向量 |
| 容易把“能调用”误写成“质量更好” | Hybrid 查询成功后可能被误认为准确率提升 | 工程连通性与检索质量是两个不同验收目标 | 分开记录链路 PASS 和质量 `NOT_TESTED` | 当前只声明真实 Hybrid 链路可用，不声明优于 lexical |

真实验证顺序是：前端设置落库且 Key 为密文、`qwen-plus` Chat Completion 收到回复、Embedding 返回 1536 维、历史文档分批回填、数据库向量覆盖达到 `71/71`、实际检索方法返回 `hybrid_rrf`。官方兼容接口规格参考：[阿里云百炼 OpenAI Embedding 兼容接口](https://help.aliyun.com/zh/model-studio/embedding-interfaces-compatible-with-openai)。

这次实践说明，仅用 Fake Provider 测“返回列表能写库”不够。第三方 OpenAI-compatible 接口仍可能在默认维度、批量上限、模型名称和扩展参数上不同；接入验收必须包含一次真实 API 调用、超过单批上限的文档，以及存量无向量数据回填。

### D14：为什么向量化选择 Qwen Embedding，而不是直接使用 DeepSeek 通用模型

这里不是在判断“Qwen 一定比 DeepSeek 更聪明”，而是在为两个不同任务选择不同类型的模型：

| 任务 | 需要的输出 | 合适的模型类型 | 当前选择 |
|---|---|---|---|
| Agent 理解问题、生成结构化 Decision 和回答 | 文本或 JSON | 通用 Chat / Reasoning 模型 | 用户可选 `qwen-plus`，部署默认仍可使用 DeepSeek |
| 第一阶段语义召回 | 固定维度、可计算余弦距离的数值向量 | 专用 Embedding 模型 | `qwen3.7-text-embedding` |
| 候选精排 | Query 与少量候选的相关性顺序或分数 | Rerank 模型，或暂时用通用 LLM | 当前自适应使用通用 LLM，收益不足时跳过 |

第一阶段检索要求同一模型把文档和查询映射到稳定的向量空间，然后由 PostgreSQL `pgvector` 计算余弦距离。`qwen3.7-text-embedding` 直接返回这种稠密向量，并支持项目数据库需要的 1536 维输出。DeepSeek 当前官方模型列表和公开 API 重点提供 Chat Completions、Responses、JSON Output 和 Tool Calls；它们返回生成文本，不是供 `pgvector` 使用的 Embedding 向量。

不能让 DeepSeek 通用模型“输出一串数字”来冒充 Embedding，原因如下：

1. 这些数字没有经过 Embedding 训练目标约束，余弦距离不具有可靠的语义检索含义。
2. 生成结果可能受提示词、采样和模型升级影响，难以保证相同文本得到稳定向量。
3. 很难严格保证每次都是 1536 个合法浮点数，解析和异常处理成本高。
4. 每个文档 Chunk 都做一次文本生成，比专用向量接口更慢、更贵，也不适合批量索引。
5. 即使在候选精排阶段使用 DeepSeek，本项目实测 nDCG 只从 `0.9689` 提升到 `0.9742`，平均增加 `12.20s`；若把它放到全量第一阶段召回，延迟会更不合理。

因此当前取舍是：DeepSeek 或 `qwen-plus` 继续承担语言理解与 Agent 决策；Qwen Embedding 只承担向量化；RRF 负责合并词法和向量候选；远程 LLM rerank 仅在候选来源确实存在竞争时调用。这样既没有用通用模型伪造向量，也没有因为引入 Qwen Embedding 就强制替换原来的 Agent 通用模型。

选择 `qwen3.7-text-embedding` 的直接依据是：它提供 OpenAI-compatible Embedding 接口、支持中文和技术文本、支持 1536 维以及单批最多 20 条，能够适配当前 `Vector(1536)` 数据库结构。当前真实测试只证明接口兼容和链路可用；是否比其他 Embedding 模型更准确，仍需要同一评测集上的对照实验，不能仅凭供应商品牌下结论。

参考资料：

- [阿里云百炼 OpenAI Embedding 兼容接口](https://help.aliyun.com/zh/model-studio/embedding-interfaces-compatible-with-openai)
- [DeepSeek 官方模型与价格](https://api-docs.deepseek.com/quick_start/pricing)
- [DeepSeek 官方 Chat Completions API](https://api-docs.deepseek.com/api/create-chat-completion/)

## 5. 没有采用的方案

| 方案 | 暂不采用原因 | 重新评估条件 |
|---|---|---|
| 每次都调用 DeepSeek rerank | nDCG 仅 `+0.00535`，平均增加 `12.20s` | 来源明显增多或换专用低延迟模型 |
| 相对分数阈值 | Evidence Recall 降至 `0.967` | 更大盲测证明可保持召回 |
| 每文档只取 1 块 | 多证据题漏证据 | 文档结构变化并重建 Gold |
| 继续调 Chunk Size | 四组参数结果完全相同 | 增加长文档和跨块问题 |
| Redis rerank 缓存 | 当前无多实例收益证据 | 多 Worker/多副本部署 |
| 用同一个 DeepSeek 评答案又做 Judge | 自我评判偏差大，不能作为可靠准确率 | 增加人工标注或独立 Judge |
| 只验证向量可写入就宣称质量提升 | 链路可用不代表排序更准 | 完成 lexical/vector/hybrid 成对评测 |

## 6. 可复现命令与证据

```bash
PYTHONPATH=backend backend/.venv/bin/python evals/rag/evaluate_retrieval_pipeline.py
PYTHONPATH=backend backend/.venv/bin/python evals/rag/evaluate_llm_reranker.py \
  --output evals/rag/reports/adaptive-reranker-chunks-v2.json
PYTHONPATH=/app python /app/evals/rag/evaluate_live_retrieval.py
PYTHONPATH=/app python /app/evals/rag/evaluate_llm_reranker.py --force-rerank
```

对应报告：

- `evals/rag/reports/retrieval-pipeline-v1.json`
- `evals/rag/reports/live-retrieval-v1.json`
- `evals/rag/reports/adaptive-reranker-chunks-v2.json`
- `evals/rag/reports/llm-reranker-forced-v2.json`

回归：Docker 隔离测试库中 `166 passed, 1 skipped`。跳过项不是本轮 RAG 用例。

## 7. 下一轮实验

1. 新建不参与调参的盲测集，并增加长文档、同义表达、否定条件、多环境同名服务和无答案问题。
2. 在当前 DashScope Embedding 上对比 lexical / vector / hybrid 的 Recall、nDCG、P50/P95 和调用成本，不先假定 hybrid 一定更好。
3. 获得专用 rerank 模型后，报告质量提升、P50/P95、Token 或调用成本，并与“不 rerank”成对比较。
4. 使用人工复核或独立 Judge 评估 Claim Correctness、Faithfulness、Answer Relevancy 和 Abstention；在此之前不得把检索 Recall 称为回答准确率。
5. 语料规模超过当前 4 个来源后，重新扫描 Top-K、候选数、每来源分块数和缓存命中率。
