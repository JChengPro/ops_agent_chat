import pytest
import yaml

from app.system_knowledge.registry import SystemKnowledgeRegistry, system_knowledge_registry


def test_system_knowledge_contains_all_documented_ssh_errors():
    identifiers = {item["id"] for item in system_knowledge_registry.list()}
    assert {
        "ssh_credential_not_configured",
        "ssh_credential_missing",
        "ssh_credential_unreadable",
        "ssh_authentication_failed",
        "ssh_host_key_mismatch",
        "ssh_connection_timeout",
        "ssh_connection_failed",
        "ssh_command_timeout",
    } <= identifiers


def test_host_key_mismatch_guidance_fails_closed():
    result = system_knowledge_registry.search("SSH 主机指纹与登记值不一致", limit=3)
    assert result["items"][0]["id"] == "ssh_host_key_mismatch"
    assert "不得关闭严格主机校验" in result["items"][0]["content"]


def test_missing_key_guidance_distinguishes_ops_agent_container_from_target_project():
    result = system_knowledge_registry.search("运行容器中没有找到 SSH 私钥", limit=3)
    item = next(item for item in result["items"] if item["id"] == "ssh_credential_missing")
    assert "Backend 和 Worker" in item["content"]
    assert "不能修复" in item["content"]


def test_system_knowledge_covers_runtime_monitoring_approval_and_rag_failures():
    identifiers = {item["id"] for item in system_knowledge_registry.list()}
    assert {
        "docker_service_exited_127",
        "runtime_verification_failed",
        "approval_action_invalidated",
        "worker_lease_expired",
        "monitoring_worker_unavailable",
        "embedding_dimension_mismatch",
        "rag_latency_tradeoff",
    } <= identifiers
    assert len(identifiers) >= 25


def test_runtime_and_rag_queries_retrieve_the_matching_guidance():
    exited = system_knowledge_registry.search("backend 容器 exited 127 command not found", limit=3)
    assert exited["items"][0]["id"] == "docker_service_exited_127"
    dimensions = system_knowledge_registry.search("Embedding 向量维度和 pgvector 不一致", limit=3)
    assert dimensions["items"][0]["id"] == "embedding_dimension_mismatch"


def test_system_knowledge_is_grouped_into_category_documents_for_display():
    documents = system_knowledge_registry.documents()
    assert {document["id"] for document in documents} == {
        "ssh-errors", "runtime-errors", "approval-execution", "monitoring", "model-rag",
        "system-guide", "project-guide", "deployment-guide",
        "queue-delivery", "redis-cache", "performance-guide", "web-api-errors",
    }
    assert sum(len(document["items"]) for document in documents) == len(system_knowledge_registry.list())
    ssh = next(document for document in documents if document["id"] == "ssh-errors")
    assert ssh["title"] == "SSH 连接与安全"
    assert {item["id"] for item in ssh["items"]} >= {"ssh_host_key_mismatch", "ssh_credential_missing"}


def test_user_workflows_retrieve_their_category_sections():
    queries = {
        "忘记密码 用户名 邮箱 登录": "account_login_and_recovery",
        "首次接入 生成密钥 安装公钥 挂载": "ssh_first_connection_setup",
        "同一服务器 多个项目 多环境": "multiple_projects_and_environments",
        "巡检范围 检查哪些 动态更新": "monitoring_scope_and_schedule",
        "自动修复范围 手动停止 维护": "monitoring_remediation_boundaries",
        "上传文档 删除文档 打开文档": "project_documents_lifecycle",
        "备份 恢复 pg_dump": "deployment_backup_and_restore",
        "两个模型 模型设置 默认模型": "model_roles_and_configuration",
    }
    for query, expected in queries.items():
        hits = system_knowledge_registry.search(query, limit=3)["items"]
        assert expected in {item["id"] for item in hits}, query


def test_guidance_describes_implemented_rag_and_monitoring_boundaries():
    rerank = system_knowledge_registry.get("rag_latency_tradeoff")
    assert "唯一文档来源数" in rerank.content
    assert "低于相关性阈值" not in system_knowledge_registry.get("rag_no_relevant_knowledge").summary
    remediation = system_knowledge_registry.get("monitoring_remediation_boundaries")
    assert "development 或 test" in remediation.content
    assert "service.start" in remediation.content
    assert "人为维护" in remediation.content


def test_expanded_documents_remain_read_only_and_have_unique_sections():
    documents = system_knowledge_registry.documents()
    identifiers = [item["id"] for document in documents for item in document["items"]]
    assert len(identifiers) == len(set(identifiers))
    assert all(document["read_only"] for document in documents)
    assert all(item["read_only"] for document in documents for item in document["items"])


def test_category_documents_provide_full_text_for_older_preview_clients():
    for document in system_knowledge_registry.documents():
        assert document["content"].strip(), document["id"]
        for item in document["items"]:
            assert f"## {item['title']}" in document["content"]
            assert item["content"] in document["content"]


@pytest.mark.parametrize(("query", "expected"), [
    ("RabbitMQ 消息队列故障怎么办", "queue_run_stuck_queued"),
    ("对话一直排队不消费", "queue_run_stuck_queued"),
    ("消息队列的密码修改后连不上", "queue_broker_outage_recovery"),
    ("重复消息会不会重复执行", "queue_duplicates_and_concurrency"),
    ("切回 PostgreSQL 轮询要停哪些进程", "queue_postgres_fallback"),
    ("Outbox 是做什么的", "queue_delivery_architecture"),
    ("Redis 缓存怎么清理", "redis_cache_maintenance"),
    ("Redis 缓存会保存审批和最终答案吗", "redis_cache_scope"),
    ("缓存不命中和 TTL 过期", "redis_cache_miss_and_expiry"),
    ("Redis 连不上或超时还能聊天吗", "redis_unavailable_fallback"),
    ("怎么查询 AgentRun profiling 耗时", "profiling_query_run"),
    ("LLM 占大部分耗时要换模型吗", "profiling_model_latency"),
    ("Embedding 和 Rerank 哪个耗时", "profiling_rag_latency"),
    ("服务端完成时间和页面等待 800ms", "profiling_total_and_polling"),
    ("网页可以打开但是登录502", "web_login_502_proxy"),
    ("登录 401 和 429 分别怎么办", "web_login_status_codes"),
    ("浏览器打不开 5175 网络怎么检查", "web_network_and_health"),
    ("回答没有刷新 引用打不开", "web_result_refresh_and_sources"),
    ("第一次使用完整流程", "system_first_use_workflow"),
    ("怎样提问 历史和实时诊断区别", "system_question_and_path"),
    ("README 详细设计 教学文档应该看哪份", "system_documentation_map"),
    ("Compose 项目首次接入 workdir", "project_compose_onboarding_example"),
    ("项目文档怎么写才能检索", "project_document_authoring"),
    ("上传了文档却搜不到", "project_knowledge_scope_and_missing"),
    ("首次部署 .env 配置清单", "deployment_configuration_reference"),
    ("alembic 迁移失败 checkpoint 表", "deployment_migrations_and_storage"),
    ("Maintenance 停了巡检没有更新", "monitoring_worker_unavailable"),
    ("如何开启巡检并验证恢复", "monitoring_enable_and_validate"),
    ("SSH 应该按什么顺序排查", "ssh_layered_diagnosis"),
    ("backend 异常只读诊断流程", "runtime_readonly_diagnosis_workflow"),
    ("Kubernetes systemd 支持范围", "runtime_adapter_support"),
    ("受控重启从提出到验证", "approval_change_walkthrough"),
    ("审批后取消和回滚的区别", "approval_cancel_and_recovery"),
    ("第一次配置聊天模型 Base URL", "model_first_configuration"),
    ("项目 RAG 从上传到引用回答流程", "rag_retrieval_pipeline_explained"),
])
def test_handbook_questions_retrieve_supporting_section(query, expected):
    hits = system_knowledge_registry.search(query, limit=3)["items"]
    assert expected in {item["id"] for item in hits}, [(item["id"], item["score"]) for item in hits]


@pytest.mark.parametrize("query", ["", "   ", "🧪", "zxqv_nonexistent_internal_error_9387"])
def test_unmatched_queries_do_not_invent_sources(query):
    assert system_knowledge_registry.search(query)["items"] == []


def test_handbook_covers_maintenance_credentials_and_process_ownership():
    get = system_knowledge_registry.get
    assert "maintenance" in get("deployment_start_and_health").content
    assert "Maintenance" in get("monitoring_worker_unavailable").content
    assert "docker compose exec -T maintenance test -r" in get("ssh_first_connection_setup").content


def test_long_repeated_body_does_not_outrank_matching_topic(tmp_path):
    entries = [
        dict(id="recovery", title="RabbitMQ credential recovery", summary="Broker credentials",
             tags=["RabbitMQ", "credentials"], content="Check configured broker credentials."),
        dict(id="unrelated", title="Account guide", summary="Account access", tags=["account"],
             content="RabbitMQ credentials " * 500),
    ]
    (tmp_path / "topics.yml").write_text(yaml.safe_dump(entries), encoding="utf-8")
    registry = SystemKnowledgeRegistry(tmp_path)
    assert registry.search("RabbitMQ credentials", limit=1)["items"][0]["id"] == "recovery"
