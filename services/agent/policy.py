"""Tool governance: which tools a tenant's agent may see and call, and which need a human.

Enforced in code, outside the prompt. The model is never the security boundary: a
disabled tool is not offered to the model at all, and if the model asks for it anyway
the runtime refuses (and the MCP server independently refuses too).

    effective_requires_approval = permission.requires_approval
                                  OR (tool is not read-only AND tenant.approval_required)
"""

from dataclasses import dataclass
from typing import Any, Literal

import jsonschema

from packages.schemas.api import CatalogTool


@dataclass(frozen=True)
class ToolPolicy:
    name: str
    enabled: bool
    requires_approval: bool
    read_only: bool


@dataclass(frozen=True)
class Decision:
    outcome: Literal["allow", "deny", "approval"]
    reason: str


def resolve_policies(
    catalog: list[CatalogTool], permissions: dict[str, Any], approval_required: bool
) -> dict[str, ToolPolicy]:
    """``permissions`` maps tool name -> object with ``enabled`` / ``requires_approval``.
    Tools with no permission row are disabled (secure by default)."""
    policies = {}
    for tool in catalog:
        perm = permissions.get(tool.name)
        enabled = bool(perm and perm.enabled)
        requires = bool(perm and perm.requires_approval) or (not tool.read_only and approval_required)
        policies[tool.name] = ToolPolicy(tool.name, enabled, requires, tool.read_only)
    return policies


def authorize(policies: dict[str, ToolPolicy], tool_name: str) -> Decision:
    policy = policies.get(tool_name)
    if policy is None:
        return Decision("deny", f"Tool '{tool_name}' does not exist in the tool catalog.")
    if not policy.enabled:
        return Decision("deny", f"Tool '{tool_name}' is not enabled for this tenant.")
    if policy.requires_approval:
        kind = "changes the customer's systems" if not policy.read_only else "is configured to need review"
        return Decision("approval", f"'{tool_name}' {kind} and requires human approval.")
    return Decision("allow", "enabled read-only tool")


def validate_arguments(schema: dict[str, Any], arguments: Any) -> str | None:
    """Validate model-generated arguments against the tool's JSON schema before any call.
    Returns an error message (sent back to the model as a tool error) or None."""
    if not isinstance(arguments, dict):
        return "arguments must be a JSON object"
    try:
        jsonschema.validate(arguments, schema)
    except jsonschema.ValidationError as exc:
        where = ".".join(str(p) for p in exc.absolute_path) or "(root)"
        return f"invalid arguments at {where}: {exc.message}"
    return None
