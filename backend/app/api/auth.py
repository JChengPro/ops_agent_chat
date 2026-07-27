from datetime import datetime, timedelta, timezone
import hashlib
import hmac
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import case, delete, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.audit.service import append_audit_event
from app.core.config import get_settings
from app.core.database import get_db
from app.core.security import (
    create_access_token,
    decode_access_token,
    get_current_user,
    hash_password,
    oauth2_scheme,
    verify_password,
)
from app.models.governance import LoginThrottle
from app.models.user import User, UserSession
from app.schemas.auth import (
    LoginRequest,
    LoginResponse,
    PasswordChangeRequest,
    ProfileUpdateRequest,
    RegisterRequest,
    RegistrationConfigOut,
    UserOut,
    UserSessionOut,
)

router = APIRouter(prefix="/auth", tags=["auth"])


def _client_ip(request: Request) -> str | None:
    # Backend is bound to localhost by default and reached through the bundled
    # Nginx proxy, which supplies the original client address.
    forwarded = request.headers.get("x-real-ip", "").strip()
    return forwarded[:64] or (request.client.host[:64] if request.client else None)


def _login_response(
    user: User,
    request: Request,
    db: Session,
    *,
    remember_me: bool,
) -> LoginResponse:
    settings = get_settings()
    lifetime = settings.jwt_remember_expire_minutes if remember_me else settings.jwt_expire_minutes
    now = datetime.now(timezone.utc)
    session_id = str(uuid4())
    db.execute(
        delete(UserSession).where(
            UserSession.user_id == user.id,
            or_(
                UserSession.expires_at <= now,
                UserSession.revoked_at <= now - timedelta(days=30),
            ),
        )
    )
    db.add(
        UserSession(
            id=session_id,
            user_id=user.id,
            user_agent=(request.headers.get("user-agent") or "")[:500] or None,
            ip_address=_client_ip(request),
            remember_me=remember_me,
            expires_at=now + timedelta(minutes=lifetime),
            last_seen_at=now,
        )
    )
    token = create_access_token(
        str(user.id),
        {"role": user.role, "ver": user.token_version, "sid": session_id},
        expires_minutes=lifetime,
    )
    db.commit()
    return LoginResponse(access_token=token, user=UserOut.model_validate(user))


def _session_id(token: str) -> str | None:
    value = decode_access_token(token).get("sid")
    return str(value) if value else None


@router.get("/registration", response_model=RegistrationConfigOut)
def registration_config() -> RegistrationConfigOut:
    settings = get_settings()
    return RegistrationConfigOut(
        enabled=settings.registration_enabled,
        invite_code_required=bool(settings.registration_invite_code),
    )


@router.post("/register", response_model=LoginResponse, status_code=status.HTTP_201_CREATED)
def register(payload: RegisterRequest, request: Request, db: Session = Depends(get_db)) -> LoginResponse:
    settings = get_settings()
    if not settings.registration_enabled:
        raise HTTPException(status_code=403, detail="当前部署未开放注册")
    if settings.registration_invite_code and not hmac.compare_digest(
        payload.invite_code or "",
        settings.registration_invite_code,
    ):
        raise HTTPException(status_code=403, detail="注册码无效")

    duplicate = db.scalar(
        select(User.id).where(
            or_(
                func.lower(User.username) == payload.username,
                func.lower(User.email) == payload.email,
            )
        ).limit(1)
    )
    if duplicate:
        raise HTTPException(status_code=409, detail="用户名或邮箱已被使用")

    user = User(
        username=payload.username,
        email=payload.email,
        password_hash=hash_password(payload.password),
        role="user",
        is_active=True,
    )
    db.add(user)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="用户名或邮箱已被使用") from exc
    return _login_response(user, request, db, remember_me=payload.remember_me)


@router.post("/login", response_model=LoginResponse)
def login(payload: LoginRequest, request: Request, db: Session = Depends(get_db)) -> LoginResponse:
    remote = _client_ip(request) or "unknown"
    normalized_username = payload.username.strip().lower()
    identity = hashlib.sha256(f"{remote}:{normalized_username}".encode()).hexdigest()
    now = datetime.now(timezone.utc)
    throttle = db.scalar(select(LoginThrottle).where(LoginThrottle.identity_key == identity).with_for_update())
    if throttle and throttle.locked_until and throttle.locked_until > now:
        raise HTTPException(status_code=429, detail="Too many failed login attempts; try again later")
    if throttle and throttle.locked_until and throttle.locked_until <= now:
        throttle.failed_count = 0
        throttle.locked_until = None
        db.flush()
    user = db.scalar(
        select(User).where(
            or_(
                func.lower(User.username) == normalized_username,
                func.lower(User.email) == normalized_username,
            )
        )
    )
    if not user or not user.is_active or not verify_password(payload.password, user.password_hash):
        next_count = LoginThrottle.failed_count + 1
        statement = insert(LoginThrottle).values(
            identity_key=identity,
            failed_count=1,
            last_failed_at=now,
        ).on_conflict_do_update(
            index_elements=[LoginThrottle.identity_key],
            set_={
                "failed_count": next_count,
                "last_failed_at": now,
                "locked_until": case((next_count >= 5, now + timedelta(minutes=5)), else_=LoginThrottle.locked_until),
                "updated_at": now,
            },
        )
        db.execute(statement)
        db.commit()
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid username or password")
    if throttle:
        db.delete(throttle)
    return _login_response(user, request, db, remember_me=payload.remember_me)


@router.get("/me", response_model=UserOut)
def me(user: User = Depends(get_current_user)) -> User:
    return user


@router.post("/logout", status_code=204)
def logout(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    token: str = Depends(oauth2_scheme),
) -> None:
    session_id = _session_id(token)
    if session_id:
        user_session = db.get(UserSession, session_id)
        if user_session and user_session.user_id == user.id and user_session.revoked_at is None:
            user_session.revoked_at = datetime.now(timezone.utc)
    else:
        # Tokens issued before server-side sessions were introduced are revoked together.
        user.token_version += 1
    db.commit()
    return None


@router.patch("/me", response_model=UserOut)
def update_profile(
    payload: ProfileUpdateRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> User:
    if not verify_password(payload.current_password, user.password_hash):
        raise HTTPException(status_code=403, detail="当前密码不正确")
    duplicate = db.scalar(
        select(User.id).where(
            User.id != user.id,
            or_(
                func.lower(User.username) == payload.username,
                func.lower(User.email) == payload.email,
            ),
        ).limit(1)
    )
    if duplicate:
        raise HTTPException(status_code=409, detail="用户名或邮箱已被使用")
    previous = {"username": user.username, "email": user.email}
    user.username = payload.username
    user.email = payload.email
    try:
        append_audit_event(
            db,
            actor_type="user",
            actor_id=user.id,
            event_type="account.profile_updated",
            payload={"previous": previous, "current": {"username": user.username, "email": user.email}},
        )
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="用户名或邮箱已被使用") from exc
    db.refresh(user)
    return user


@router.post("/password", status_code=204)
def change_password(
    payload: PasswordChangeRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> None:
    if not verify_password(payload.current_password, user.password_hash):
        raise HTTPException(status_code=403, detail="当前密码不正确")
    user.password_hash = hash_password(payload.new_password)
    user.token_version += 1
    now = datetime.now(timezone.utc)
    db.execute(
        update(UserSession)
        .where(UserSession.user_id == user.id, UserSession.revoked_at.is_(None))
        .values(revoked_at=now)
    )
    append_audit_event(
        db,
        actor_type="user",
        actor_id=user.id,
        event_type="account.password_changed",
        payload={"all_sessions_revoked": True},
    )
    db.commit()
    return None


@router.get("/sessions", response_model=list[UserSessionOut])
def sessions(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    token: str = Depends(oauth2_scheme),
) -> list[dict]:
    current_session_id = _session_id(token)
    now = datetime.now(timezone.utc)
    rows = db.scalars(
        select(UserSession)
        .where(
            UserSession.user_id == user.id,
            UserSession.revoked_at.is_(None),
            UserSession.expires_at > now,
        )
        .order_by(UserSession.last_seen_at.desc())
    ).all()
    return [
        {
            "id": item.id,
            "current": item.id == current_session_id,
            "user_agent": item.user_agent,
            "ip_address": item.ip_address,
            "remember_me": item.remember_me,
            "created_at": item.created_at,
            "last_seen_at": item.last_seen_at,
            "expires_at": item.expires_at,
        }
        for item in rows
    ]


@router.delete("/sessions/{session_id}", status_code=204)
def revoke_session(
    session_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> None:
    user_session = db.get(UserSession, session_id)
    if not user_session or user_session.user_id != user.id:
        raise HTTPException(status_code=404, detail="登录会话不存在")
    if user_session.revoked_at is None:
        user_session.revoked_at = datetime.now(timezone.utc)
        append_audit_event(
            db,
            actor_type="user",
            actor_id=user.id,
            event_type="account.session_revoked",
            payload={"session_id": user_session.id},
        )
        db.commit()
    return None


@router.post("/sessions/revoke-others", status_code=204)
def revoke_other_sessions(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    token: str = Depends(oauth2_scheme),
) -> None:
    current_session_id = _session_id(token)
    if not current_session_id:
        raise HTTPException(status_code=409, detail="当前登录不支持单独管理会话，请重新登录")
    result = db.execute(
        update(UserSession)
        .where(
            UserSession.user_id == user.id,
            UserSession.id != current_session_id,
            UserSession.revoked_at.is_(None),
        )
        .values(revoked_at=datetime.now(timezone.utc))
    )
    append_audit_event(
        db,
        actor_type="user",
        actor_id=user.id,
        event_type="account.other_sessions_revoked",
        payload={"revoked_count": result.rowcount},
    )
    db.commit()
    return None
