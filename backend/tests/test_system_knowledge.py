from app.system_knowledge.registry import system_knowledge_registry


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
    assert len(documents) == 5
    assert sum(len(document["items"]) for document in documents) == len(system_knowledge_registry.list())
    ssh = next(document for document in documents if document["id"] == "ssh-errors")
    assert ssh["title"] == "SSH 连接与安全"
    assert {item["id"] for item in ssh["items"]} >= {"ssh_host_key_mismatch", "ssh_credential_missing"}
