"""Visibility, risk and JSON Schema policy for dynamic MCP tools."""

from __future__ import annotations

import json
import re
from typing import Any

from tga.contracts import TGATask
from tga.tools.mcp_config import MCPServerConfig
from tga.tools.mcp_registry import MCPCatalogSnapshot, MCPToolRoute


SENSITIVE_KEY = re.compile(r"password|passwd|secret|token|authorization|api[_-]?key|cookie", re.IGNORECASE)


class MCPPolicy:
    def catalog_denial(self, *, task: TGATask, server: MCPServerConfig, method: str) -> str | None:
        if server.enabled_tools and method not in server.enabled_tools:
            return "method is not enabled for this server"
        visibility = server.visibility
        method_policy = server.methods.get(method)
        if method_policy is not None and not method_policy.enabled:
            return "method is disabled"
        modes = method_policy.modes if method_policy and method_policy.modes is not None else visibility.modes
        if task.mode not in modes:
            return f"method is not available in {task.mode} mode"
        if visibility.allow_methods and method not in visibility.allow_methods:
            return "method is not in the server allowlist"
        if method in visibility.deny_methods:
            return "method is in the server denylist"
        return None

    def call_denial(self, *, task: TGATask, server_id: str, server: MCPServerConfig, method: str) -> str | None:
        if task.schema_version >= 4:
            if server_id not in task.mcp_capabilities.server_ids:
                return "MCP server was not in the Session creation capability snapshot"
            if not any(item.server_id == server_id and item.method == method for item in task.mcp_capabilities.tools):
                return "MCP method was not in the Session creation capability snapshot"
            denial = self.catalog_denial(task=task, server=server, method=method)
            if denial:
                return denial
            risk = self.risk_for(server=server, method=method)
            if risk == "destructive":
                action = f"mcp:{server_id}.{method}"
                if task.execution_policy.state_change.mode != "authorized" or action not in task.execution_policy.state_change.allowed_actions:
                    return f"destructive MCP method requires explicit state_change authorization for {action}"
            if risk == "active" and not self._active_boundary_allows(task):
                return "active MCP method is blocked by the Session execution boundaries"
            return None
        execution = task.execution_policy.mcp if task.schema_version >= 3 and task.execution_policy else None
        enabled_servers = execution.enabled_servers if execution is not None else task.mcp_servers
        if server_id not in task.mcp_servers or server_id not in enabled_servers:
            return "MCP server is not selected for this task"
        denial = self.catalog_denial(task=task, server=server, method=method)
        if denial:
            return denial
        risk = self.risk_for(server=server, method=method)
        if risk == "destructive":
            if execution is None:
                return "destructive MCP methods are forbidden"
            action = f"mcp:{server_id}.{method}"
            if task.execution_policy.state_change.mode != "authorized" or action not in task.execution_policy.state_change.allowed_actions:
                return f"destructive MCP method requires explicit state_change authorization for {action}"
        if execution is not None:
            configured_tools = set(execution.enabled_tools)
            if configured_tools and method not in configured_tools and f"{server_id}.{method}" not in configured_tools and f"mcp__{server_id}__{method}" not in configured_tools:
                return "MCP method is not in execution_policy enabled_tools"
            if risk == "active" and not execution.allow_active:
                return "active MCP method requires execution_policy.mcp.allow_active"
        elif risk == "active" and not task.allow_active_scan:
            return "active MCP method requires allow_active_scan"
        return None

    def visible(self, *, task: TGATask, server: MCPServerConfig, method: str, server_id: str | None = None) -> bool:
        # Compatibility helper: catalog visibility intentionally ignores risk;
        # actual calls must use authorize/call_denial.
        if task.schema_version >= 4 and server_id is not None:
            if server_id not in task.mcp_capabilities.server_ids:
                return False
            if not any(item.server_id == server_id and item.method == method for item in task.mcp_capabilities.tools):
                return False
        elif server_id is not None and server_id not in task.mcp_servers:
            return False
        return self.catalog_denial(task=task, server=server, method=method) is None

    def filter_snapshot(
        self, *, task: TGATask, snapshot: MCPCatalogSnapshot, servers: dict[str, MCPServerConfig]
    ) -> MCPCatalogSnapshot:
        allowed_servers = set(task.mcp_capabilities.server_ids if task.schema_version >= 4 else task.mcp_servers)
        allowed_methods = {
            (item.server_id, item.method) for item in task.mcp_capabilities.tools
        } if task.schema_version >= 4 else None
        routes = tuple(
            route
            for route in snapshot.routes
            if route.server_id in allowed_servers
            and (allowed_methods is None or (route.server_id, route.method) in allowed_methods)
            and route.server_id in servers
            and servers[route.server_id].enabled
            and self.catalog_denial(task=task, server=servers[route.server_id], method=route.method) is None
        )
        return snapshot.model_copy(update={"routes": routes})

    def authorize(
        self, *, task: TGATask, server: MCPServerConfig, route: MCPToolRoute, arguments: dict[str, Any]
    ) -> str | None:
        denial = self.call_denial(task=task, server_id=route.server_id, server=server, method=route.method)
        if denial:
            return denial
        if server.transport == "stdio" and server.stdio and server.stdio.source == "docker_image":
            if _contains_windows_absolute_path(arguments):
                return "host Windows paths are not valid inside MCP containers; materialize the input and use its /workspace path"
        error = validate_json_schema(route.input_schema, arguments)
        if error:
            return error
        method_policy = server.methods.get(route.method)
        if method_policy and method_policy.argument_schema:
            return validate_json_schema(method_policy.argument_schema, arguments)
        return None

    @staticmethod
    def risk_for(*, server: MCPServerConfig, method: str) -> str:
        method_policy = server.methods.get(method)
        return method_policy.risk if method_policy and method_policy.risk else server.visibility.risk

    @staticmethod
    def _active_boundary_allows(task: TGATask) -> bool:
        policy = task.execution_policy
        return bool(
            policy
            and (
                policy.network.mode != "none"
                or policy.process_execution.mode != "forbidden"
                or policy.state_change.mode in {"approval_required", "authorized"}
                or policy.containment.mode in {"approval_required", "authorized"}
            )
        )


def validate_json_schema(
    schema: dict[str, Any] | bool, value: Any, path: str = "arguments", root_schema: dict[str, Any] | None = None
) -> str | None:
    """Validate the useful JSON Schema subset emitted by MCP servers.

    No external dependency is required; unsupported keywords are left to the
    MCP server, while types, required fields, enums, bounds and nested shapes
    are enforced before a process is launched.
    """

    if schema is True:
        return None
    if schema is False:
        return f"{path} is not allowed by the schema"
    root_schema = root_schema or schema
    if "$ref" in schema:
        resolved = _resolve_local_ref(root_schema, str(schema["$ref"]))
        if resolved is None:
            return f"{path} uses an unsupported or missing schema reference {schema['$ref']}"
        return validate_json_schema(resolved, value, path, root_schema)
    for candidate in schema.get("allOf") or []:
        error = validate_json_schema(candidate, value, path, root_schema)
        if error:
            return error
    if "not" in schema and validate_json_schema(schema["not"], value, path, root_schema) is None:
        return f"{path} matches a forbidden schema"
    if "if" in schema:
        branch = schema.get("then") if validate_json_schema(schema["if"], value, path, root_schema) is None else schema.get("else")
        if isinstance(branch, dict):
            error = validate_json_schema(branch, value, path, root_schema)
            if error:
                return error
    if "oneOf" in schema:
        matches = [candidate for candidate in schema["oneOf"] if validate_json_schema(candidate, value, path, root_schema) is None]
        return None if len(matches) == 1 else f"{path} must match exactly one oneOf schema"
    if "anyOf" in schema:
        if any(validate_json_schema(candidate, value, path, root_schema) is None for candidate in schema["anyOf"]):
            return None
        return f"{path} does not match any allowed schema"
    expected = schema.get("type")
    if isinstance(expected, list):
        if any(_type_matches(item, value) for item in expected):
            expected = next((item for item in expected if _type_matches(item, value)), expected[0])
        else:
            return f"{path} must have one of types {expected}"
    elif expected and not _type_matches(expected, value):
        return f"{path} must be {expected}"
    if "enum" in schema and value not in schema["enum"]:
        return f"{path} must be one of {schema['enum']}"
    if "const" in schema and value != schema["const"]:
        return f"{path} must equal {schema['const']!r}"
    if isinstance(value, dict):
        if "minProperties" in schema and len(value) < int(schema["minProperties"]):
            return f"{path} must contain at least {schema['minProperties']} properties"
        if "maxProperties" in schema and len(value) > int(schema["maxProperties"]):
            return f"{path} must contain at most {schema['maxProperties']} properties"
        required = schema.get("required") or []
        missing = [key for key in required if key not in value]
        if missing:
            return f"{path} is missing required fields: {', '.join(missing)}"
        properties = schema.get("properties") or {}
        patterns = schema.get("patternProperties") or {}
        matched_by_pattern: set[str] = set()
        try:
            matched_by_pattern = {key for key in value for pattern in patterns if re.search(pattern, key)}
        except re.error as exc:
            return f"{path} has an invalid patternProperties expression: {exc}"
        if schema.get("additionalProperties") is False:
            unknown = [key for key in value if key not in properties and key not in matched_by_pattern]
            if unknown:
                return f"{path} contains unknown fields: {', '.join(unknown)}"
        for key, item in value.items():
            child = properties.get(key)
            if isinstance(child, dict):
                error = validate_json_schema(child, item, f"{path}.{key}", root_schema)
                if error:
                    return error
            for pattern, pattern_schema in patterns.items():
                if re.search(pattern, key):
                    error = validate_json_schema(pattern_schema, item, f"{path}.{key}", root_schema)
                    if error:
                        return error
            if key not in properties and key not in matched_by_pattern and isinstance(schema.get("additionalProperties"), dict):
                error = validate_json_schema(schema["additionalProperties"], item, f"{path}.{key}", root_schema)
                if error:
                    return error
    if isinstance(value, list):
        if "minItems" in schema and len(value) < int(schema["minItems"]):
            return f"{path} must contain at least {schema['minItems']} items"
        if "maxItems" in schema and len(value) > int(schema["maxItems"]):
            return f"{path} must contain at most {schema['maxItems']} items"
        if schema.get("uniqueItems") and len({json_key(item) for item in value}) != len(value):
            return f"{path} must contain unique items"
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            for index, item in enumerate(value):
                error = validate_json_schema(item_schema, item, f"{path}[{index}]", root_schema)
                if error:
                    return error
        if isinstance(schema.get("contains"), dict) and not any(
            validate_json_schema(schema["contains"], item, f"{path}[{index}]", root_schema) is None
            for index, item in enumerate(value)
        ):
            return f"{path} must contain an item matching contains"
    if isinstance(value, str):
        if "minLength" in schema and len(value) < int(schema["minLength"]):
            return f"{path} is shorter than minLength {schema['minLength']}"
        if "maxLength" in schema and len(value) > int(schema["maxLength"]):
            return f"{path} is longer than maxLength {schema['maxLength']}"
        if schema.get("pattern"):
            try:
                if re.search(str(schema["pattern"]), value) is None:
                    return f"{path} does not match the required pattern"
            except re.error:
                return f"{path} has an invalid schema pattern"
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            return f"{path} must be >= {schema['minimum']}"
        if "maximum" in schema and value > schema["maximum"]:
            return f"{path} must be <= {schema['maximum']}"
        if "exclusiveMinimum" in schema and value <= schema["exclusiveMinimum"]:
            return f"{path} must be > {schema['exclusiveMinimum']}"
        if "exclusiveMaximum" in schema and value >= schema["exclusiveMaximum"]:
            return f"{path} must be < {schema['exclusiveMaximum']}"
        if "multipleOf" in schema and abs((value / schema["multipleOf"]) - round(value / schema["multipleOf"])) > 1e-12:
            return f"{path} must be a multiple of {schema['multipleOf']}"
    return None


def redact_sensitive(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: "[REDACTED]" if SENSITIVE_KEY.search(str(key)) else redact_sensitive(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact_sensitive(item) for item in value]
    return value


def _contains_windows_absolute_path(value: Any) -> bool:
    if isinstance(value, dict):
        return any(_contains_windows_absolute_path(item) for item in value.values())
    if isinstance(value, list):
        return any(_contains_windows_absolute_path(item) for item in value)
    return isinstance(value, str) and bool(re.match(r"^(?:[A-Za-z]:[\\/]|\\\\)", value.strip()))


def _type_matches(expected: str, value: Any) -> bool:
    return {
        "object": isinstance(value, dict),
        "array": isinstance(value, list),
        "string": isinstance(value, str),
        "integer": isinstance(value, int) and not isinstance(value, bool),
        "number": isinstance(value, (int, float)) and not isinstance(value, bool),
        "boolean": isinstance(value, bool),
        "null": value is None,
    }.get(expected, True)


def _resolve_local_ref(root: dict[str, Any], reference: str) -> dict[str, Any] | None:
    if not reference.startswith("#/"):
        return None
    current: Any = root
    for raw_part in reference[2:].split("/"):
        part = raw_part.replace("~1", "/").replace("~0", "~")
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current if isinstance(current, dict) else None


def json_key(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
