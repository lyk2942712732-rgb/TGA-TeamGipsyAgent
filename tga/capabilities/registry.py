from __future__ import annotations

from typing import Type

from pydantic import BaseModel

from .base import CapabilitySpec
from .schemas import (
    ArtifactInspectArguments,
    HTTPRequestArguments,
    ToolInvokeArguments,
    WorkspacePythonArguments,
    WorkspaceReadArguments,
    WorkspaceWriteArguments,
)


class RegisteredCapability:
    def __init__(self, spec: CapabilitySpec, arguments_model: Type[BaseModel]):
        self.spec = spec
        self.arguments_model = arguments_model


class CapabilityRegistry:
    def __init__(self) -> None:
        self._items: dict[str, RegisteredCapability] = {}

    def register(self, spec: CapabilitySpec, arguments_model: Type[BaseModel]) -> None:
        if spec.name in self._items:
            raise ValueError(f"duplicate capability: {spec.name}")
        self._items[spec.name] = RegisteredCapability(spec, arguments_model)

    def get(self, name: str) -> RegisteredCapability | None:
        return self._items.get(name)

    def validate(self, name: str, arguments: dict) -> BaseModel:
        item = self.get(name)
        if item is None:
            raise KeyError(name)
        return item.arguments_model.model_validate(arguments)

    def snapshot(self) -> dict:
        return {
            "capabilities": [
                {
                    **item.spec.model_dump(),
                    "input_schema": item.arguments_model.model_json_schema(),
                }
                for _, item in sorted(self._items.items())
            ]
        }


def build_default_registry() -> CapabilityRegistry:
    registry = CapabilityRegistry()
    register = registry.register
    register(CapabilitySpec(name="http.request", description="Scoped HTTP request with redirect verification.", kind="http", risk="passive", modes=["ctf", "web_audit"], parameter_schema={}, budget_key="http"), HTTPRequestArguments)
    register(CapabilitySpec(name="tool.invoke", description="Invoke an explicitly named, catalogued MCP tool method.", kind="tool", risk="active", modes=["ctf", "web_audit", "code_audit", "binary_ctf"], parameter_schema={}, budget_key="mcp"), ToolInvokeArguments)
    register(CapabilitySpec(name="workspace.read", description="Read a file from this solver's private workspace.", kind="workspace", risk="passive", modes=["ctf", "web_audit", "code_audit", "binary_ctf"], parameter_schema={}, budget_key="workspace"), WorkspaceReadArguments)
    register(CapabilitySpec(name="workspace.write", description="Write a file in this solver's private workspace.", kind="workspace", risk="active", modes=["ctf", "web_audit", "code_audit", "binary_ctf"], parameter_schema={}, budget_key="workspace"), WorkspaceWriteArguments)
    register(CapabilitySpec(name="workspace.python", description="Run a bounded Python helper inside a CTF solver workspace.", kind="workspace", risk="active", modes=["ctf", "binary_ctf"], parameter_schema={}, budget_key="python"), WorkspacePythonArguments)
    register(CapabilitySpec(name="artifact.inspect", description="Read a bounded excerpt of an existing artifact.", kind="workspace", risk="passive", modes=["ctf", "web_audit", "code_audit", "binary_ctf"], parameter_schema={}, budget_key="artifact"), ArtifactInspectArguments)
    return registry
