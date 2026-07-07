from __future__ import annotations

import importlib.util
from pathlib import Path


def _load_bootstrap_module():
    script = Path(__file__).resolve().parents[1] / "scripts" / "tga_mcp_bootstrap.py"
    spec = importlib.util.spec_from_file_location("tga_mcp_bootstrap", script)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_classify_tls_failure() -> None:
    module = _load_bootstrap_module()
    assert module.classify_failure("net/http: TLS handshake timeout") == "network_tls"
    assert module.classify_failure("curl: (35) TLS connect error: unexpected eof while reading") == "network_tls"


def test_classify_timeout() -> None:
    module = _load_bootstrap_module()
    assert module.classify_failure("", timed_out=True) == "timeout"
    assert module.classify_failure("ReadTimeoutError: read timed out") == "network_timeout"


def test_cn_profile_patches_pip() -> None:
    module = _load_bootstrap_module()
    text = "FROM python:3.12-slim\nRUN pip install -r requirements.txt\n"
    patched = module._patch_python_pip(text)
    assert "# TGA_CN_PIP_MIRROR" in patched
    assert "PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple" in patched
    assert "ENV # TGA" not in patched


def test_classify_curl_download_failure() -> None:
    module = _load_bootstrap_module()
    assert module.classify_failure("curl: (22) The requested URL returned error: 502\nexit code") == "network_download"
    assert module.classify_failure("429 Too Many Requests") == "registry_rate_limit"


def test_cn_profile_patches_go_download() -> None:
    module = _load_bootstrap_module()
    text = (
        "FROM python:3.12-slim\n"
        "RUN curl -fsSL https://go.dev/dl/go1.22.8.linux-amd64.tar.gz | tar -C /usr/local -xz \\\n"
        "    && chmod -R a+rX /usr/local/go\n"
    )
    patched = module._patch_go_download(text)
    assert "TGA_CN_GO_APT_FALLBACK" in patched
    assert "golang-go" in patched
    assert "go.dev/dl" not in patched


def test_cn_profile_patches_go_proxy() -> None:
    module = _load_bootstrap_module()
    text = "FROM python:3.12-slim\nRUN go install example.com/tool@latest\n"
    patched = module._patch_go_proxy(text)
    assert "GOPROXY=https://goproxy.cn,direct" in patched
