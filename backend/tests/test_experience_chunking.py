from app.experience.chunking import chunk_document


def test_markdown_chunking_preserves_heading_context_and_stable_keys():
    content = """# Runbook

Overview.

## Backend

Check status first.

Check bounded logs second.
"""
    first = chunk_document(content, source_ref="docs/runbook.md", max_chars=200, overlap_chars=40)
    second = chunk_document(content, source_ref="docs/runbook.md", max_chars=200, overlap_chars=40)
    assert first == second
    assert [chunk.heading_path for chunk in first] == [("Runbook",), ("Runbook", "Backend")]
    assert first[1].content.startswith("# Runbook\n## Backend")


def test_content_change_keeps_identity_but_changes_content_hash():
    original = chunk_document("# Service\n\nStatus is healthy.", source_ref="service.md")
    changed = chunk_document("# Service\n\nStatus is degraded.", source_ref="service.md")
    assert original[0].chunk_key == changed[0].chunk_key
    assert original[0].content_hash != changed[0].content_hash


def test_heading_change_changes_chunk_identity():
    original = chunk_document("# Service\n\nStatus.", source_ref="service.md")
    changed = chunk_document("# API\n\nStatus.", source_ref="service.md")
    assert original[0].chunk_key != changed[0].chunk_key


def test_long_content_is_bounded_and_overlapped():
    content = "# Logs\n\n" + "failure detail " * 80
    chunks = chunk_document(content, source_ref="logs.md", max_chars=240, overlap_chars=40)
    assert len(chunks) > 1
    assert all(len(chunk.content) <= 240 for chunk in chunks)
