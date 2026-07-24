
from pathlib import Path
import socket

print("input=" + Path("/workspace/inputs/immutable.txt").read_text())
try:
    Path("/workspace/inputs/immutable.txt").write_text("changed")
except OSError:
    print("input_read_only=true")
else:
    print("input_read_only=false")

Path("/workspace/work/work-result.txt").write_text("work-ok")
Path("/workspace/artifacts/artifact-result.txt").write_text("artifact-ok")
try:
    Path("/workspace/../host-only.txt").read_text()
except OSError:
    print("host_path_hidden=true")
else:
    print("host_path_hidden=false")

try:
    socket.create_connection(("1.1.1.1", 53), timeout=1)
except OSError:
    print("network_disabled=true")
else:
    print("network_disabled=false")
