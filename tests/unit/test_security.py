"""Passwords, token audiences, RBAC matrix, secret refs, log redaction, retries."""

import os
import time
import uuid

import jwt
import pytest

from packages.core.logging import REDACTED, redact, redact_text
from packages.core.retry import RetryExhaustedError, retry_async
from packages.core.security import (
    create_access_token,
    create_service_token,
    decode_access_token,
    decode_service_token,
    has_permission,
    hash_password,
    verify_password,
)


def test_password_hashing():
    stored = hash_password("correct horse")
    assert stored.startswith("scrypt$") and "correct horse" not in stored
    assert verify_password("correct horse", stored)
    assert not verify_password("wrong", stored)
    assert not verify_password("x", "garbage")


def test_user_token_roundtrip_and_tenant_scope():
    uid = uuid.uuid4()
    principal = decode_access_token(create_access_token(uid, "e@x.dev", "engineer", ["t1"]))
    assert principal.user_id == uid and principal.can_access_tenant("t1") and not principal.can_access_tenant("t2")
    admin = decode_access_token(create_access_token(uid, "a@x.dev", "admin", ["*"]))
    assert admin.can_access_tenant(uuid.uuid4())


def test_service_and_user_tokens_are_not_interchangeable():
    service = create_service_token(tenant_id="t1", run_id="r", user_id="u", allowed_tools=["get_customer"])
    with pytest.raises(jwt.PyJWTError):
        decode_access_token(service)
    user = create_access_token(uuid.uuid4(), "e@x.dev", "admin", ["*"])
    with pytest.raises(jwt.PyJWTError):
        decode_service_token(user)
    assert decode_service_token(service)["allowed_tools"] == ["get_customer"]


def test_expired_token_rejected():
    token = create_service_token(tenant_id="t", run_id=None, user_id=None, allowed_tools=[], ttl_seconds=-5)
    with pytest.raises(jwt.ExpiredSignatureError):
        decode_service_token(token)


@pytest.mark.parametrize(
    "role,permission,allowed",
    [
        ("admin", "tenants:manage", True),
        ("engineer", "tenants:manage", False),
        ("engineer", "config:write", True),
        ("viewer", "config:write", False),
        ("engineer", "agent:approve", True),
        ("viewer", "agent:run", False),
        ("viewer", "runs:read", True),
        ("engineer", "connectors:manage", False),
    ],
)
def test_permission_matrix(role, permission, allowed):
    assert has_permission(role, permission) is allowed


def test_redaction_masks_keys_and_token_shaped_values():
    data = {"api_key": "abc", "nested": {"Authorization": "Bearer xyz"}, "note": "key sk-ant-api03-SECRETSECRET"}
    out = redact(data)
    assert out["api_key"] == REDACTED and out["nested"]["Authorization"] == REDACTED
    assert "SECRETSECRET" not in out["note"]
    assert "hunter2" not in redact_text("postgresql://reader:hunter2@db:5432/crm")
    assert "eyJhbGciOiJIUzI1NiJ9" not in redact_text(
        "token eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U"
    )


def test_env_secret_refs_are_restricted_to_connector_prefix():
    import asyncio

    from packages.core.secrets import SecretResolutionError, resolve_secret

    os.environ["TP_CONNECTOR_DEMO"] = "value"
    assert asyncio.run(resolve_secret(None, "env://TP_CONNECTOR_DEMO")) == "value"
    with pytest.raises(SecretResolutionError):
        asyncio.run(resolve_secret(None, "env://JWT_SECRET"))
    with pytest.raises(SecretResolutionError):
        asyncio.run(resolve_secret(None, "file://../../etc/passwd"))


def test_fernet_roundtrip():
    from packages.core.secrets import decrypt, encrypt

    token = encrypt("s3cret")
    assert token != "s3cret" and decrypt(token) == "s3cret"


async def test_retry_only_retries_retryable_errors():
    calls = {"n": 0}

    async def flaky():
        calls["n"] += 1
        raise TimeoutError("slow")

    started = time.perf_counter()
    with pytest.raises(RetryExhaustedError) as err:
        await retry_async(flaky, is_retryable=lambda e: isinstance(e, TimeoutError), attempts=3, base_delay=0.01)
    assert err.value.attempts == 3 and calls["n"] == 3 and time.perf_counter() - started < 1

    calls["n"] = 0

    async def fatal():
        calls["n"] += 1
        raise ValueError("bad input")

    with pytest.raises(ValueError):
        await retry_async(fatal, is_retryable=lambda e: isinstance(e, TimeoutError), attempts=3)
    assert calls["n"] == 1
