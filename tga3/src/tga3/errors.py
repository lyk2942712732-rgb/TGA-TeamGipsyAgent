"""Errors with stable meanings across HTTP, MCP and JSON-RPC boundaries."""


class TGA3Error(Exception):
    code = "tga3_error"


class NotFoundError(TGA3Error):
    code = "not_found"


class ConflictError(TGA3Error):
    code = "conflict"


class ContractError(TGA3Error):
    code = "contract_error"


class FindingRejectedError(ContractError):
    code = "finding_rejected"


class RuntimeUnavailableError(TGA3Error):
    code = "runtime_unavailable"
