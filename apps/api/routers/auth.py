from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select

from apps.api.deps import get_principal
from packages.core.db import platform_session
from packages.core.models import TenantMembership, User
from packages.core.security import Principal, create_access_token, hash_password, verify_password
from packages.schemas.api import LoginRequest, TokenResponse, UserOut

router = APIRouter(prefix="/api/auth", tags=["auth"])
# Verified against when the email doesn't exist, so response time doesn't reveal which
# emails have accounts.
_DUMMY_HASH = hash_password("timing-equalizer")


@router.post("/login", response_model=TokenResponse)
async def login(body: LoginRequest) -> TokenResponse:
    async with platform_session() as session:
        user = (
            await session.execute(select(User).where(User.email == body.email.strip().lower()))
        ).scalar_one_or_none()
        if user is None or not user.is_active:
            verify_password(body.password, _DUMMY_HASH)
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid email or password")
        if not verify_password(body.password, user.password_hash):
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid email or password")
        if user.role == "admin":
            tenant_ids = ["*"]
        else:
            rows = await session.execute(select(TenantMembership.tenant_id).where(TenantMembership.user_id == user.id))
            tenant_ids = [str(t) for t in rows.scalars()]
    token = create_access_token(user.id, user.email, user.role, tenant_ids)
    return TokenResponse(
        access_token=token, user=UserOut(id=user.id, email=user.email, role=user.role, tenant_ids=tenant_ids)
    )


@router.get("/me", response_model=UserOut)
async def me(principal: Principal = Depends(get_principal)) -> UserOut:
    return UserOut(
        id=principal.user_id, email=principal.email, role=principal.role, tenant_ids=sorted(principal.tenant_ids)
    )
