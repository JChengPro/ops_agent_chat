from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.database import get_db
from app.core.security import get_current_user
from app.llm.configuration import encrypt_api_key, normalize_base_url, validate_base_url
from app.models.user import User, UserLLMSettings


router = APIRouter(tags=["llm-settings"])


class LLMSettingsUpdate(BaseModel):
    provider: str = Field(min_length=1, max_length=80)
    base_url: str = Field(min_length=8, max_length=500)
    model: str = Field(min_length=1, max_length=200)
    api_key: str | None = Field(default=None, min_length=8, max_length=1000)


def _settings_out(profile: UserLLMSettings | None) -> dict:
    settings = get_settings()
    if profile:
        return {
            "provider": profile.provider,
            "base_url": profile.base_url,
            "model": profile.model,
            "api_key_configured": bool(profile.api_key_encrypted or settings.llm_configured),
            "api_key_source": "user" if profile.api_key_encrypted else "deployment",
            "source": "user",
            "allowed_base_urls": settings.llm_allowed_base_url_list,
        }
    return {
        "provider": settings.llm_provider,
        "base_url": normalize_base_url(settings.llm_base_url),
        "model": settings.llm_model,
        "api_key_configured": settings.llm_configured,
        "api_key_source": "deployment" if settings.llm_configured else "none",
        "source": "deployment",
        "allowed_base_urls": settings.llm_allowed_base_url_list,
    }


@router.get("/llm-settings")
def get_llm_settings(
    db: Session = Depends(get_db), user: User = Depends(get_current_user)
):
    return _settings_out(db.query(UserLLMSettings).filter_by(user_id=user.id).one_or_none())


@router.put("/llm-settings")
def update_llm_settings(
    payload: LLMSettingsUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    try:
        base_url = validate_base_url(payload.base_url)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    provider = payload.provider.strip().lower()
    model = payload.model.strip()
    api_key = payload.api_key.strip() if payload.api_key else None
    if not provider or not model or (payload.api_key is not None and not api_key):
        raise HTTPException(status_code=422, detail="供应商、模型名称和已填写的 API Key 不能为空")
    profile = db.query(UserLLMSettings).filter_by(user_id=user.id).one_or_none()
    settings = get_settings()
    previous_base_url = profile.base_url if profile else None
    if not profile:
        profile = UserLLMSettings(
            user_id=user.id,
            provider=provider,
            base_url=base_url,
            model=model,
        )
        db.add(profile)
    profile.provider = provider
    profile.base_url = base_url
    profile.model = model
    if api_key:
        profile.api_key_encrypted = encrypt_api_key(api_key)
    elif profile.api_key_encrypted and previous_base_url != base_url:
        raise HTTPException(status_code=422, detail="切换模型服务地址时必须重新填写对应的 API Key")
    elif not profile.api_key_encrypted and not (
        settings.llm_configured
        and base_url == normalize_base_url(settings.llm_base_url)
    ):
        raise HTTPException(status_code=422, detail="切换模型服务时必须填写对应的 API Key")
    db.commit()
    db.refresh(profile)
    return _settings_out(profile)


@router.delete("/llm-settings")
def reset_llm_settings(
    db: Session = Depends(get_db), user: User = Depends(get_current_user)
):
    profile = db.query(UserLLMSettings).filter_by(user_id=user.id).one_or_none()
    if profile:
        db.delete(profile)
        db.commit()
    return _settings_out(None)
