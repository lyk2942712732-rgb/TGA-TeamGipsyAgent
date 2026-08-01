from __future__ import annotations

from typing import Type

from pydantic import BaseModel

from .base import CapabilitySpec
from .schemas import (
    ArtifactInspectArguments,
    BinwalkAnalyzeArguments,
    FfufDirectoryScanArguments,
    HTTPRequestArguments,
    NmapScanArguments,
    NucleiScanArguments,
    Radare2AnalyzeArguments,
    SandboxExecArguments,
    WorkspacePythonArguments,
    WorkspaceReadArguments,
    WorkspaceShellArguments,
    WorkspaceWriteArguments,
    YaraScanArguments,
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
    all_modes = ["ctf", "penetration_test", "incident_response", "vulnerability_research", "reverse_engineering"]
    register(CapabilitySpec(name="http.request", description="Scoped HTTP request with redirect verification.", kind="http", risk="passive", modes=["ctf", "penetration_test", "incident_response", "vulnerability_research"], parameter_schema={}, budget_key="http"), HTTPRequestArguments)
    register(CapabilitySpec(name="workspace.read", description="Read a file from this solver's private workspace.", kind="workspace", risk="passive", modes=all_modes, parameter_schema={}, budget_key="workspace"), WorkspaceReadArguments)
    register(CapabilitySpec(name="workspace.write", description="Write a file in this solver's private workspace.", kind="workspace", risk="active", modes=all_modes, parameter_schema={}, budget_key="workspace"), WorkspaceWriteArguments)
    register(CapabilitySpec(name="workspace.python", description="Run a bounded Python helper in the Solver workspace.", kind="workspace", risk="active", modes=["ctf", "incident_response", "vulnerability_research", "reverse_engineering"], parameter_schema={}, budget_key="python"), WorkspacePythonArguments)
    register(CapabilitySpec(name="workspace.shell", description="Run a command in this Solver's private workspace and persist stdout/stderr.", kind="workspace", risk="active", modes=all_modes, parameter_schema={}, budget_key="shell"), WorkspaceShellArguments)
    register(CapabilitySpec(name="sandbox.exec", description="Execute one profile-allowlisted binary with a direct argv vector.", kind="tool", risk="active", modes=all_modes, parameter_schema={}, budget_key="sandbox_exec"), SandboxExecArguments)
    register(CapabilitySpec(name="artifact.inspect", description="Read a bounded excerpt of an existing artifact.", kind="workspace", risk="passive", modes=all_modes, parameter_schema={}, budget_key="artifact"), ArtifactInspectArguments)
    register(CapabilitySpec(name="nmap.scan", description="Run a bounded TCP connect scan in the Kali sandbox.", kind="tool", risk="active", modes=["penetration_test", "ctf"], parameter_schema={}, budget_key="network_scan"), NmapScanArguments)
    register(CapabilitySpec(name="ffuf.directory_scan", description="Run bounded content discovery in the Kali sandbox.", kind="tool", risk="active", modes=["penetration_test", "ctf", "vulnerability_research"], parameter_schema={}, budget_key="web_scan"), FfufDirectoryScanArguments)
    register(CapabilitySpec(name="nuclei.scan", description="Run a bounded template scan in the Kali sandbox.", kind="tool", risk="active", modes=["penetration_test", "ctf", "vulnerability_research"], parameter_schema={}, budget_key="web_scan"), NucleiScanArguments)
    register(CapabilitySpec(name="binwalk.analyze", description="Analyze a firmware or binary file in the Kali sandbox.", kind="tool", risk="active", modes=["ctf", "incident_response", "reverse_engineering"], parameter_schema={}, budget_key="binary_analysis"), BinwalkAnalyzeArguments)
    register(CapabilitySpec(name="yara.scan", description="Scan files with an authorized YARA ruleset in the Kali sandbox.", kind="tool", risk="active", modes=["ctf", "incident_response", "reverse_engineering"], parameter_schema={}, budget_key="binary_analysis"), YaraScanArguments)
    register(CapabilitySpec(name="radare2.analyze", description="Run bounded radare2 analysis commands in the Kali sandbox.", kind="tool", risk="active", modes=["ctf", "vulnerability_research", "reverse_engineering"], parameter_schema={}, budget_key="binary_analysis"), Radare2AnalyzeArguments)
    return registry
