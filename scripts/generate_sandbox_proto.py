"""Generate and normalize Python sandbox protocol bindings."""

from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
PROTO_ROOT = ROOT / "sandboxd" / "api"
PROTO = PROTO_ROOT / "sandbox" / "v1" / "sandbox.proto"
PYTHON_OUT = ROOT / "tga" / "sandbox" / "api"


def main() -> int:
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "grpc_tools.protoc",
            f"-I{PROTO_ROOT}",
            f"--python_out={PYTHON_OUT}",
            f"--grpc_python_out={PYTHON_OUT}",
            str(PROTO),
        ],
        check=False,
    )
    if result.returncode:
        return result.returncode
    grpc_file = PYTHON_OUT / "sandbox" / "v1" / "sandbox_pb2_grpc.py"
    content = grpc_file.read_text(encoding="utf-8")
    content = content.replace(
        "from sandbox.v1 import sandbox_pb2 as sandbox_dot_v1_dot_sandbox__pb2",
        "from tga.sandbox.api.sandbox.v1 import sandbox_pb2 as sandbox_dot_v1_dot_sandbox__pb2",
    )
    grpc_file.write_text(content, encoding="utf-8", newline="\n")
    for directory in (
        PYTHON_OUT / "sandbox",
        PYTHON_OUT / "sandbox" / "v1",
    ):
        (directory / "__init__.py").touch()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
