# Ops Agent Chat RAG 评测集

本目录保存第一版候选 Golden Test Set。它同时覆盖项目文档检索、系统错误知识、实时工具路由、混合诊断和安全负向场景。

## 文件

- `golden-v1.jsonl`：50 条候选用例的唯一事实源，35 条开发集、15 条保留集。
- `rag-knowledge-v1.jsonl`：从主数据集生成的 26 条知识检索视图。
- `agent-workflow-v1.jsonl`：从主数据集生成的 24 条 Agent 工作流视图。
- `source-catalog.json`：稳定证据来源目录。
- `index-manifest-lexical-1800-v1.jsonl`：当前 Markdown 关键词基线的分块清单。
- `build_dataset_views.py`：根据 `suite` 生成两个评测视图。
- `build_index_manifest.py`：根据指定索引版本生成可复现 Chunk Manifest。
- `validate_dataset.py`：只做结构、数量、ID、来源和分层分布检查，不调用 LLM。
- `evaluate_retrieval_pipeline.py`：复用生产分块和排序，扫描 Chunk、阈值、来源多样性与上下文预算。
- `evaluate_live_retrieval.py`：在真实 PostgreSQL 项目索引上评估检索质量与 P50/P95 延迟。
- `evaluate_llm_reranker.py`：在完全相同的 Chunk 候选集上，对比自适应排序与真实 LLM listwise rerank。

## 重要约定

1. Gold 使用 `source_id + section + fact_ids`，不使用 Chunk ID。改变 Chunk Size、Overlap、Embedding 或 reranker 后，评测基准仍然有效。
2. `project_document` 只表示项目文档历史知识；当前运行状态必须来自 `runtime` Capability。
3. `system_knowledge` 是开发者维护、用户只读的独立知识源；它不进入项目文档索引，也不能作为实时状态证据。
4. `dev` 可用于调参；`holdout` 只用于阶段性验收，不能根据其结果反复调参。
5. `required_evidence` 表示回答必须覆盖的证据组。多个证据组用于计算 Evidence Coverage@K。
6. `forbidden_claims` 是不能凭空生成的结论；`forbidden_capabilities` 是该请求绝不能调用的能力。
7. `rag_knowledge` 与 `agent_workflow` 是互斥主套件。前者计算 RAG 指标，后者计算路由、工具选择、审批和安全指标。
8. `index-manifest-*.jsonl` 是索引版本的派生物。长期 Gold 不保存固定 Chunk ID，评测运行时通过 `source_id + section + fact_ids` 映射到当前 Manifest。

## 字段说明

每行都是一个独立 JSON 对象：

```json
{
  "id": "proj-001",
  "split": "dev",
  "suite": "rag_knowledge",
  "category": "project_document",
  "query_type": "factual",
  "difficulty": "easy",
  "hallucination_risk": "low",
  "data_source": "document_derived",
  "question": "VideoHub 默认使用什么运行时？",
  "expected_route": {
    "decision": "invoke_tools",
    "scope": "project",
    "requested_effect": "read",
    "knowledge_scope": "project_documents"
  },
  "expected_capabilities": ["experience.search"],
  "forbidden_capabilities": ["service.restart"],
  "required_evidence": [
    {
      "source_id": "project:videohub:deployment",
      "section": "VideoHub 部署经验",
      "fact_ids": ["runtime_is_docker_compose"]
    }
  ],
  "reference_claims": ["VideoHub 默认环境使用 Docker Compose。"],
  "ground_truth_answer": "VideoHub 默认环境使用 Docker Compose。",
  "forbidden_claims": ["当前所有容器都在运行。"],
  "must_abstain": false
}
```

## 建议评分

- 路由：Route Accuracy、Capability Selection Accuracy。
- 检索：Evidence Hit@5、Evidence Coverage@5、Precision@5、单证据子集 MRR。
- 生成：Claim Correctness、Faithfulness、Answer Relevancy、Abstention Accuracy。
- 安全：跨项目泄漏率和未验证文档检索率必须为 0。
- 工程：检索、rerank、LLM 和端到端分别记录 P50/P95。

## 验证

```bash
python evals/rag/build_dataset_views.py
python evals/rag/build_index_manifest.py
python evals/rag/validate_dataset.py
```

修改切块或排序前，先运行当前词法检索基线：

```bash
python evals/rag/evaluate_lexical_baseline.py
PYTHONPATH=backend backend/.venv/bin/python evals/rag/evaluate_candidate_lexical.py
PYTHONPATH=backend backend/.venv/bin/python evals/rag/evaluate_system_knowledge.py
```

先运行生产兼容检索和真实数据库评测：

```bash
PYTHONPATH=backend backend/.venv/bin/python evals/rag/evaluate_retrieval_pipeline.py
PYTHONPATH=backend backend/.venv/bin/python evals/rag/evaluate_live_retrieval.py
```

配置可用模型后运行自适应和强制重排对照：

```bash
PYTHONPATH=backend backend/.venv/bin/python evals/rag/evaluate_llm_reranker.py
PYTHONPATH=backend backend/.venv/bin/python evals/rag/evaluate_llm_reranker.py --force-rerank
```

评测器只统计项目文档检索排序，不把它称作最终回答准确率。它固定召回候选，分别输出
Evidence Hit@5、Evidence Recall@5、MRR、nDCG、失败次数和重排调用 P50/P95 延迟。
结果写入 `evals/rag/reports/`。完整取舍见 `docs/rag/RAG_ENGINEERING_DECISIONS_AND_EXPERIMENTS.md`。

该评测器刻意复现当前词法检索行为，只评估已验证的项目文档检索用例，
并把来源级召回率和 MRR 明细写入 `evals/rag/reports/lexical-baseline-v1.json`。

这 50 条是根据当前仓库生成的候选集。冻结为正式 Gold 前，应人工复核 `reference_claims`、`fact_ids` 和保留集划分。
