from dataclasses import dataclass
import base64
import hashlib
from urllib.parse import urlsplit

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.agent import AgentRun
from app.models.user import UserLLMSettings


@dataclass(frozen=True)
class ResolvedLLMConfiguration:
    provider: str
    base_url: str
    model: str
    api_key: str
    source: str


def normalize_base_url(value: str) -> str:
    return value.strip().rstrip("/")


def validate_base_url(value: str) -> str:
    normalized = normalize_base_url(value)
    parsed = urlsplit(normalized)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("Base URL 必须是有效的 http 或 https 地址")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("Base URL 不能包含账号、密码、查询参数或片段")
    settings = get_settings()
    if normalized not in settings.llm_allowed_base_url_list:
        raise ValueError("该 Base URL 不在服务器允许列表 LLM_ALLOWED_BASE_URLS 中")
    if settings.app_env.lower() in {"prod", "production"} and parsed.scheme != "https":
        raise ValueError("生产环境的模型 Base URL 必须使用 HTTPS")
    return normalized


def encrypt_api_key(value: str) -> str:
    return _fernet().encrypt(value.encode("utf-8")).decode("ascii")


def decrypt_api_key(value: str) -> str:
    try:
        return _fernet().decrypt(value.encode("ascii")).decode("utf-8")
    except InvalidToken as exc:
        raise RuntimeError("模型 API Key 无法解密，请在模型设置中重新填写") from exc


def resolve_llm_configuration(db: Session, run_id: str) -> ResolvedLLMConfiguration:
    settings = get_settings()
    run = db.get(AgentRun, run_id)
    profile = db.query(UserLLMSettings).filter_by(user_id=run.user_id).one_or_none() if run else None
    if profile:
        key = decrypt_api_key(profile.api_key_encrypted) if profile.api_key_encrypted else settings.llm_api_key
        if not key.strip():
            raise RuntimeError("当前用户尚未配置模型 API Key")
        return ResolvedLLMConfiguration(
            provider=profile.provider,
            base_url=profile.base_url,
            model=profile.model,
            api_key=key,
            source="user",
        )
    if not settings.llm_configured:
        raise RuntimeError("当前用户尚未配置模型 API Key，且部署环境没有默认模型配置")
    return ResolvedLLMConfiguration(
        provider=settings.llm_provider,
        base_url=normalize_base_url(settings.llm_base_url),
        model=settings.llm_model,
        api_key=settings.llm_api_key,
        source="deployment",
    )


def _fernet() -> Fernet:
    settings = get_settings()
    material = settings.llm_credential_encryption_key or settings.app_secret_key
    digest = hashlib.sha256(material.encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(digest))
