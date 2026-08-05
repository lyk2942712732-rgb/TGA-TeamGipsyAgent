ARG BASE_IMAGE=tga-kali-base:release
ARG GO_BUILDER=golang:1.25-bookworm@sha256:ea341baa9bd5ba6784f6d7161ace70544349a6242d54d34a0fbfd2c4d51c9d58

FROM ${GO_BUILDER} AS go-tools
ARG DEBIAN_FRONTEND=noninteractive
ARG NUCLEI_VERSION=v3.8.0
ARG FFUF_VERSION=v2.1.0
# kiterunner is built here rather than taken from its release tarball. That
# release is from 2021 and was compiled with go1.15.11, so Trivy reports every
# Go stdlib advisory fixed since -- four CRITICALs -- and no newer release
# exists to move to. Rebuilding the same pinned commit on the current toolchain
# removes them at the source instead of suppressing them.
ARG KITERUNNER_VERSION=1.0.2
ARG KITERUNNER_COMMIT=e7a7fa635c2271cb5aeea7a885c1dc66774bb5c7
RUN CGO_ENABLED=0 go install github.com/projectdiscovery/nuclei/v3/cmd/nuclei@${NUCLEI_VERSION} \
    && CGO_ENABLED=0 go install github.com/ffuf/ffuf/v2@${FFUF_VERSION} \
    && git clone --depth 1 --branch "v${KITERUNNER_VERSION}" \
         https://github.com/assetnote/kiterunner.git /src/kiterunner \
    && test "$(git -C /src/kiterunner rev-parse HEAD)" = "${KITERUNNER_COMMIT}" \
    && CGO_ENABLED=0 go -C /src/kiterunner build -o /go/bin/kr ./cmd/kiterunner

FROM ${BASE_IMAGE}
ARG DEBIAN_FRONTEND=noninteractive
ARG KITERUNNER_VERSION=1.0.2
ARG MITMPROXY_VERSION=12.2.3
ARG ARJUN_VERSION=2.2.7
ARG GRAPHQL_COP_VERSION=1.16
ARG GRAPHQL_COP_COMMIT=2b7e086efae672f28b419c7fcdfe6b48d846c9dc
USER root
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl git httpie python3-pip python3-venv sqlmap wafw00f \
    && PIPX_HOME=/opt/pipx PIPX_BIN_DIR=/usr/local/bin pipx install "mitmproxy==${MITMPROXY_VERSION}" \
    && PIPX_HOME=/opt/pipx PIPX_BIN_DIR=/usr/local/bin pipx install "arjun==${ARJUN_VERSION}" \
    && git clone --depth 1 --branch "${GRAPHQL_COP_VERSION}" https://github.com/dolevf/graphql-cop.git /opt/graphql-cop \
    && test "$(git -C /opt/graphql-cop rev-parse HEAD)" = "${GRAPHQL_COP_COMMIT}" \
    # graphql-cop pins requests 2.25.1, which drags urllib3 down to 1.26.x.
    # Installing that system-wide fails outright -- pip cannot uninstall the
    # dpkg-managed urllib3 2.7.0 and reports uninstall-no-record-file -- and
    # succeeding would be worse, since sqlmap and wafw00f in this same image
    # are built against 2.7.0. A private venv keeps the pins to graphql-cop.
    && python3 -m venv /opt/graphql-cop/venv \
    && /opt/graphql-cop/venv/bin/pip install --no-cache-dir -r /opt/graphql-cop/requirements.txt \
    && chmod 0755 /opt/graphql-cop/graphql-cop.py \
    && printf '#!/bin/sh\nexec /opt/graphql-cop/venv/bin/python /opt/graphql-cop/graphql-cop.py "$@"\n' \
         > /usr/local/bin/graphql-cop \
    && chmod 0755 /usr/local/bin/graphql-cop \
    && python3 -c "import urllib3, sys; sys.exit(not urllib3.__version__.startswith('2.'))" \
    && git clone --depth 1 --branch v2.3.0 https://github.com/ticarpi/jwt_tool.git /opt/jwt_tool \
    && test "$(git -C /opt/jwt_tool rev-parse HEAD)" = "3bc7407cf2222d6a821dcc19c776e5a1b1cb9a9b" \
    && python3 -m pip install --break-system-packages --no-cache-dir -r /opt/jwt_tool/requirements.txt \
    && ln -s /opt/jwt_tool/jwt_tool.py /usr/local/bin/jwt_tool \
    && chmod 0755 /opt/jwt_tool/jwt_tool.py \
    && rm -rf /tmp/* /opt/graphql-cop/.git /opt/jwt_tool/.git /var/lib/apt/lists/*
COPY --from=go-tools /go/bin/nuclei /go/bin/ffuf /go/bin/kr /usr/local/bin/
COPY --chown=tga:tga toolset.json /opt/tga/toolset.json
USER 10001:10001
