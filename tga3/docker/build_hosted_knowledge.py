"""Build the immutable hosted library from a pinned upstream checkout."""
from pathlib import Path
import subprocess
from types import SimpleNamespace
from tga3.local_knowledge import LocalKnowledge

REVISION = "e225ddfcd420a7f54a5940f47336ebdf8c29b875"
base = Path("/opt/tga3-hosted/knowledge")
root = base / "ctf-wiki"
root.mkdir(parents=True)
subprocess.run(["git", "init", str(root)], check=True)
subprocess.run(["git", "-C", str(root), "remote", "add", "origin", "https://github.com/ctf-wiki/ctf-wiki.git"], check=True)
subprocess.run(["git", "-C", str(root), "sparse-checkout", "set", "--no-cone", "*.md", "LICENSE*"], check=True)
subprocess.run(["git", "-C", str(root), "fetch", "--filter=blob:none", "--depth=1", "origin", REVISION], check=True)
subprocess.run(["git", "-C", str(root), "checkout", "--detach", "FETCH_HEAD"], check=True)
config = SimpleNamespace(runtime=SimpleNamespace(ctf_wiki_root=str(root), ctf_wiki_index=str(base / "ctf-wiki.sqlite3")), resolve_path=lambda value: Path(value))
status = LocalKnowledge(config).rebuild()
assert status["available"] and status["revision"] == REVISION
# Keep upstream Markdown, licenses and attribution; omit checkout history.
import shutil
shutil.rmtree(root / ".git")
print(status)
