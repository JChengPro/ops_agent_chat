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
    assert {document["id"] for document in documents} == {
        "ssh-errors", "runtime-errors", "approval-execution", "monitoring", "model-rag",
        "system-guide", "project-guide", "deployment-guide",
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
