import json
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import CurrentUser
from app.models import OperationLog, User
from app.schemas import LoginRequest, UserOut
from app.security import TOKEN_COOKIE_NAME, create_access_token, verify_password

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


def _log_login(db: Session, user: User | None, account: str, remark: str | None) -> None:
    db.add(
        OperationLog(
            operator_id=user.id if user else None,
            role=user.role if user else None,
            action="login",
            target_type="user",
            target_id=user.id if user else None,
            after_value=user.account if user else None,
            remark=remark or f"账号 {account} 登录成功",
        )
    )


@router.post("/login")
def login(
    payload: LoginRequest,
    response: Response,
    db: Annotated[Session, Depends(get_db)],
) -> UserOut:
    error = HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="账号或密码错误")
    user = db.scalars(select(User).where(User.account == payload.account)).first()
    if user is None or not verify_password(payload.password, user.password_hash):
        _log_login(db, user, payload.account, f"账号 {payload.account} 密码错误")
        db.commit()
        raise error
    if user.status != "enabled":
        _log_login(db, user, payload.account, f"账号 {payload.account} 已被停用")
        db.commit()
        raise error

    _log_login(db, user, payload.account, None)
    db.commit()

    token, expire_seconds = create_access_token(user.id, user.role)
    response.set_cookie(
        key=TOKEN_COOKIE_NAME,
        value=token,
        max_age=expire_seconds,
        httponly=True,
        samesite="lax",
    )
    return user


@router.post("/logout")
def logout(
    response: Response,
    db: Annotated[Session, Depends(get_db)],
    current_user: CurrentUser,
) -> dict[str, str]:
    db.add(
        OperationLog(
            operator_id=current_user.id,
            role=current_user.role,
            action="logout",
            target_type="user",
            target_id=current_user.id,
            after_value=json.dumps(
                {
                    "account": current_user.account,
                    "name": current_user.name,
                    "role": current_user.role,
                },
                ensure_ascii=False,
            ),
            remark=f"账号 {current_user.account} 退出登录",
        )
    )
    db.commit()
    response.delete_cookie(key=TOKEN_COOKIE_NAME)
    return {"status": "ok"}


@router.get("/me")
def me(current_user: CurrentUser) -> UserOut:
    return current_user
