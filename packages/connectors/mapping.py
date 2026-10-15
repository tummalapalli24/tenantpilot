"""Tiny declarative mapping helpers: dotted paths and request templates.

get_path({"data": {"account": {"ref": "C-1"}}}, "data.account.ref")  -> "C-1"
get_path({"items": [{"id": 1}]}, "items.0.id")                     -> 1
map_fields(src, {"customer_id": "customer_ref", "name": "profile.full_name"})
"""

import re
from datetime import date, datetime
from decimal import Decimal
from typing import Any
from urllib.parse import quote
from uuid import UUID

from packages.connectors.base import ConnectorError

_PLACEHOLDER = re.compile(r"\{(\w+)\}")


def get_path(obj: Any, path: str | None) -> Any:
    if not path:
        return obj
    current = obj
    for part in path.split("."):
        if isinstance(current, dict):
            current = current.get(part)
        elif isinstance(current, list) and part.isdigit():
            idx = int(part)
            current = current[idx] if idx < len(current) else None
        else:
            return None
        if current is None:
            return None
    return current


def json_safe(value: Any) -> Any:
    if isinstance(value, datetime | date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, dict):
        return {k: json_safe(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [json_safe(v) for v in value]
    return value


def map_fields(source: Any, field_map: dict[str, str]) -> dict[str, Any]:
    if not isinstance(source, dict):
        raise ConnectorError("unexpected response shape (expected an object)", kind="config")
    if not field_map:
        return json_safe(source)
    return {target: json_safe(get_path(source, path)) for target, path in field_map.items()}


def _lookup(payload: dict[str, Any], key: str) -> Any:
    if key not in payload or payload[key] is None:
        raise ConnectorError(f"missing required value '{key}'", kind="bad_request")
    return payload[key]


def render_path(template: str, payload: dict[str, Any]) -> str:
    # Values are URL-encoded so "../admin" or "a/b" can't change which endpoint is called.
    return _PLACEHOLDER.sub(lambda m: quote(str(_lookup(payload, m.group(1))), safe=""), template)


def render_template(template: Any, payload: dict[str, Any]) -> Any:
    """Fill "{name}" placeholders in a JSON body template. An exact "{name}" keeps the
    payload value's type; placeholders embedded in longer strings are stringified."""
    if isinstance(template, dict):
        return {k: render_template(v, payload) for k, v in template.items()}
    if isinstance(template, list):
        return [render_template(v, payload) for v in template]
    if isinstance(template, str):
        exact = _PLACEHOLDER.fullmatch(template)
        if exact:
            return _lookup(payload, exact.group(1))
        return _PLACEHOLDER.sub(lambda m: str(_lookup(payload, m.group(1))), template)
    return template
