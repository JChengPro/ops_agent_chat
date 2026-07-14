import re

SECRET_PATTERNS = [
    re.compile(r"(?i)(password|passwd|secret|token|api[_-]?key|access[_-]?key|private[_-]?key)\s*[:=]\s*([^\s,;]+)"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.DOTALL),
    re.compile(r"(?i)(authorization\s*:\s*bearer)\s+[^\s,;]+"),
    re.compile(r"(?i)(https?://[^\s:/@]+:)[^\s@/]+@"),
    re.compile(r"\b(?:sk|rk|pk)-[A-Za-z0-9_-]{16,}\b"),
]


def redact_secrets(text: str | None) -> str:
    if not text:
        return ""
    redacted = text
    for pattern in SECRET_PATTERNS:
        redacted = pattern.sub(lambda match: f"{match.group(1)}<redacted>" if match.lastindex and match.lastindex >= 1 else "<redacted>", redacted)
    return redacted


def truncate_text(text: str | None, limit: int) -> tuple[str, bool]:
    safe = redact_secrets(text or "")
    if len(safe) <= limit:
        return safe, False
    return safe[:limit] + "\n...[truncated]", True
