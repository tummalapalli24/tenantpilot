"""Connector credential storage and resolution.

Connectors never hold raw credentials. They hold a *reference*:

* ``db://<uuid>``   - encrypted (Fernet/AES) in the tenant's ``secrets`` table (RLS-scoped).
                      Default for credentials entered through the control plane UI.
* ``env://NAME``    - an environment variable, restricted to the TP_CONNECTOR_ prefix so a
                      reference can never read platform secrets such as JWT_SECRET.
* ``file://name``   - a file under the mounted Kubernetes Secret directory
                      (``/var/run/secrets/tenantpilot``), no path traversal allowed.

In a cloud deployment the natural next step is an ``aws-sm://`` resolver backed by AWS
Secrets Manager (or External Secrets Operator syncing into ``file://``).
"""

import asyncio
import os
import uuid
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from packages.core.config import get_settings
from packages.core.models import Secret


class SecretResolutionError(Exception):
    pass


def _fernet() -> Fernet:
    return Fernet(get_settings().secrets_master_key.encode())


def encrypt(value: str) -> str:
    return _fernet().encrypt(value.encode()).decode()


def decrypt(ciphertext: str) -> str:
    try:
        return _fernet().decrypt(ciphertext.encode()).decode()
    except InvalidToken as exc:
        raise SecretResolutionError("secret could not be decrypted (wrong master key?)") from exc


async def store_secret(session: AsyncSession, tenant_id: uuid.UUID, name: str, value: str) -> str:
    """Encrypt and store a credential inside a tenant_session. Returns its reference."""
    secret = Secret(tenant_id=tenant_id, name=name, ciphertext=encrypt(value))
    session.add(secret)
    await session.flush()
    return f"db://{secret.id}"


def _read_mounted_secret(secret_dir: str, name: str) -> str:
    base = Path(secret_dir).resolve()
    path = (base / name).resolve()
    if base not in path.parents:
        raise SecretResolutionError("file secret must live inside the mounted secret directory")
    if not path.is_file():
        raise SecretResolutionError("mounted secret file not found")
    return path.read_text().strip()


async def resolve_secret(session: AsyncSession, ref: str | None) -> str | None:
    """Resolve a secret reference. ``session`` must be a tenant_session (RLS applies)."""
    if not ref:
        return None
    settings = get_settings()
    scheme, _, target = ref.partition("://")
    if scheme == "db":
        try:
            secret_id = uuid.UUID(target)
        except ValueError as exc:
            raise SecretResolutionError("malformed db:// secret reference") from exc
        row = (await session.execute(select(Secret).where(Secret.id == secret_id))).scalar_one_or_none()
        if row is None:  # also what RLS returns for another tenant's secret
            raise SecretResolutionError("secret not found for this tenant")
        return decrypt(row.ciphertext)
    if scheme == "env":
        if not target.startswith(settings.connector_env_prefix):
            raise SecretResolutionError(f"env secrets must start with {settings.connector_env_prefix}")
        value = os.environ.get(target)
        if value is None:
            raise SecretResolutionError(f"environment variable {target} is not set")
        return value
    if scheme == "file":
        return await asyncio.to_thread(_read_mounted_secret, settings.connector_secret_dir, target)
    raise SecretResolutionError(f"unsupported secret scheme '{scheme}'")
