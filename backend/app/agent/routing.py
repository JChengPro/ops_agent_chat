"""Conservative fast path admission; this module never grants execution permission."""
import re


def knowledge_route(question: str, capabilities: list[dict], *, execution_mode: str = "interactive") -> bool:
    if execution_mode != "interactive" or not any(item.get("name") == "experience.search" for item in capabilities):
        return False
    if not question.strip() or len(question) > 2000:
        return False
    # Ignore explicit negative clauses, but retain every positive clause for risk checks.
    clauses = re.split(r"[。.!?！？;；，\n]|,(?=\s*(?:请不要|请勿|不要|禁止|无需))", question.casefold())
    text = " ".join(clause for clause in clauses if not (
        re.match(r"\s*(do not|don't|never|请不要|请勿|不要|禁止|无需)", clause)
        and not re.search(r"但是|但|然后|顺便|而是|\b(but|then|instead)\b", clause)
    ))
    if re.search(r"重启|停止|启动一下|启动服务|执行|修复|部署|修改|删除|清空|重置|更新|安装|卸载|调整|处理一下|扩容|缩容|回滚|"
                 r"\b(restart|stop|start|execute|repair|fix|deploy|modify|delete|scale|rollback|shell)\b", text):
        return False
    if re.search(r"现在|当前|实时|线上|刚才|那个|上述|照着|顺便|然后|检查|诊断|查看.{0,12}日志|"
                 r"\b(now|currently|live|production|above|that one|inspect|diagnose)\b", text):
        return False
    return bool(re.search(r"历史|之前|以往|曾经|经验|知识库|项目文档|文档里|文档中|"
                          r"\b(historical|history|previously|documentation|knowledge|past|verified experience)\b", text))


def knowledge_request():
    return {"goal": "answer", "scope": "project", "time_focus": "historical",
            "requested_effect": "read", "subjects": [], "desired_output": "concise cited answer",
            "constraints": ["knowledge sources only; no live runtime or changes"],
            "confidence": 1.0, "summary": "Read verified project knowledge"}


def compact_evidence(evidence, max_chars):
    """Deduplicate retrieved chunks without detaching their evidence/source references."""
    selected = []
    seen = set()
    used = 0
    for observation in evidence:
        if observation.get("capability") != "experience.search":
            continue
        items = (observation.get("data") or {}).get("items") or []
        for item in items:
            key = (item.get("item_id"), item.get("chunk_key"))
            if key in seen:
                continue
            seen.add(key)
            content = str(item.get("content") or "")
            if used >= max_chars:
                break
            content = content[:max_chars - used]
            used += len(content)
            selected.append({"evidence_id": observation.get("evidence_id"), "item_id": item.get("item_id"),
                             "title": str(item.get("title") or "")[:255], "chunk_key": item.get("chunk_key"),
                             "source_type": item.get("source_type"), "source_ref": item.get("source_ref"),
                             "trust_status": item.get("trust_status"),
                             "content": content})
    return selected
