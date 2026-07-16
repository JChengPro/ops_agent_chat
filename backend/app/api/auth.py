from datetime import datetime, timedelta, timezone
import hashlib

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import case, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import create_access_token, get_current_user, verify_password
from app.models.governance import LoginThrottle
from app.models.user import User
from app.schemas.auth import LoginRequest, LoginResponse, UserOut

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login", response_model=LoginResponse)
def login(payload: LoginRequest, request: Request, db: Session = Depends(get_db)) -> LoginResponse:
    remote = request.client.host if request.client else "unknown"
    identity = hashlib.sha256(f"{remote}:{payload.username.strip().lower()}".encode()).hexdigest()
    now = datetime.now(timezone.utc)
    throttle = db.scalar(select(LoginThrottle).where(LoginThrottle.identity_key == identity).with_for_update())
    if throttle and throttle.locked_until and throttle.locked_until > now:
        raise HTTPException(status_code=429, detail="Too many failed login attempts; try again later")
    if throttle and throttle.locked_until and throttle.locked_until <= now:
        throttle.failed_count = 0
        throttle.locked_until = None
        db.flush()
    user = db.scalar(select(User).where(or_(User.username == payload.username, User.email == payload.username)))
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
        db.commit()
    token = create_access_token(str(user.id), {"role": user.role, "ver": user.token_version})
    return LoginResponse(access_token=token, user=UserOut.model_validate(user))


@router.get("/me", response_model=UserOut)
def me(user: User = Depends(get_current_user)) -> User:
    return user


@router.post("/logout", status_code=204)
def logout(db: Session = Depends(get_db), user: User = Depends(get_current_user)) -> None:
    user.token_version += 1
    db.commit()
    return None
