"""Passwords, user JWTs, and API->MCP service tokens.

Two token types with *different secrets and audiences*:

* User access token  (aud=tenantpilot-api): who the human is, their role, their tenants.
* Service token      (aud=tenantpilot-mcp): minted per agent run by the control plane.
  It pins the tenant and the exact tools this run may call, so the MCP server can
  enforce tenant scoping and tool permissions itself (defense in depth: the agent
  runtime already filtered tools, the MCP server checks again).
"""

import base64
import hashlib
import hmac
import secrets
import time
import uuid
from dataclasses import dataclass, field

import jwt

from packages.core.config import get_settings

ROLES = ("admin", "engineer", "viewer")

# Permission -> roles allowed. Endpoints depend on permissions, never on role names.
PERMISSIONS: dict[str, set[str]] = {
    "tenants:read": {"admin", "engineer", "viewer"},
    "tenants:manage": {"admin"},
    "connectors:manage": {"admin"},
    "tools:manage": {"admin"},
    "config:write": {"admin", "engineer"},
    "documents:write": {"admin", "engineer"},
    "agent:run": {"admin", "engineer"},
    "agent:approve": {"admin", "engineer"},
    "runs:read": {"admin", "engineer", "viewer"},
    "audit:read": {"admin", "engineer"},
}

_SCRYPT_N, _SCRYPT_R, _SCRYPT_P = 2**14, 8, 1
USER_AUDIENCE = "tenantpilot-api"
SERVICE_AUDIENCE = "tenantpilot-mcp"
ISSUER = "tenantpilot"


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P, dklen=32)
    salt_b64, digest_b64 = base64.b64encode(salt).decode(), base64.b64encode(digest).decode()
    return f"scrypt${_SCRYPT_N}${_SCRYPT_R}${_SCRYPT_P}${salt_b64}${digest_b64}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, n, r, p, salt_b64, hash_b64 = stored.split("$")
        if algo != "scrypt":
            return False
        digest = hashlib.scrypt(
            password.encode(), salt=base64.b64decode(salt_b64), n=int(n), r=int(r), p=int(p), dklen=32
        )
        return hmac.compare_digest(digest, base64.b64decode(hash_b64))
    except (ValueError, TypeError):
        return False


def has_permission(role: str, permission: str) -> bool:
    return role in PERMISSIONS.get(permission, set())


@dataclass(frozen=True)
class Principal:
    user_id: uuid.UUID
    email: str
    role: str
    tenant_ids: frozenset[str] = field(default_factory=frozenset)  # {"*"} = all tenants (admins)

    def can_access_tenant(self, tenant_id: str | uuid.UUID) -> bool:
        return "*" in self.tenant_ids or str(tenant_id) in self.tenant_ids


def create_access_token(user_id: uuid.UUID, email: str, role: str, tenant_ids: list[str]) -> str:
    settings = get_settings()
    now = int(time.time())
    claims = {
        "iss": ISSUER,
        "aud": USER_AUDIENCE,
        "sub": str(user_id),
        "email": email,
        "role": role,
        "tenants": tenant_ids,
        "iat": now,
        "exp": now + settings.jwt_ttl_minutes * 60,
    }
    return jwt.encode(claims, settings.jwt_secret, algorithm="HS256")


def decode_access_token(token: str) -> Principal:
    """Raises jwt.PyJWTError on any problem (bad signature, expired, wrong audience)."""
    claims = jwt.decode(
        token,
        get_settings().jwt_secret,
        algorithms=["HS256"],
        audience=USER_AUDIENCE,
        issuer=ISSUER,
        options={"require": ["exp", "sub", "aud", "iss"]},
    )
    if claims.get("role") not in ROLES:
        raise jwt.InvalidTokenError("unknown role")
    return Principal(
        user_id=uuid.UUID(claims["sub"]),
        email=claims.get("email", ""),
        role=claims["role"],
        tenant_ids=frozenset(claims.get("tenants", [])),
    )


def create_service_token(
    *,
    tenant_id: str | None,
    run_id: str | None,
    user_id: str | None,
    allowed_tools: list[str],
    ttl_seconds: int | None = None,
) -> str:
    settings = get_settings()
    now = int(time.time())
    claims = {
        "iss": ISSUER,
        "aud": SERVICE_AUDIENCE,
        "sub": "agent-runtime",
        "scope": "mcp:tools",
        "tenant_id": tenant_id,
        "run_id": run_id,
        "user_id": user_id,
        "allowed_tools": sorted(allowed_tools),
        "iat": now,
        "exp": now + (ttl_seconds or settings.service_token_ttl_seconds),
    }
    return jwt.encode(claims, settings.service_token_secret, algorithm="HS256")


def decode_service_token(token: str) -> dict:
    return jwt.decode(
        token,
        get_settings().service_token_secret,
        algorithms=["HS256"],
        audience=SERVICE_AUDIENCE,
        issuer=ISSUER,
        options={"require": ["exp", "aud", "iss"]},
    )
