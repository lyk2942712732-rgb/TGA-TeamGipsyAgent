ARG BASE_IMAGE=ghcr.io/team-gipsy/tga-kali-base@sha256:REPLACE_WITH_RELEASE_DIGEST
FROM ${BASE_IMAGE}
ARG DEBIAN_FRONTEND=noninteractive

ARG SEMGREP_VERSION=1.172.0
USER root
RUN apt-get update \
    && apt-get install -y --no-install-recommends cloc graphviz tree universal-ctags \
    && PIPX_HOME=/opt/pipx PIPX_BIN_DIR=/usr/local/bin pipx install "semgrep==${SEMGREP_VERSION}" \
    && rm -rf /var/lib/apt/lists/*
COPY --chown=tga:tga toolset.json /opt/tga/toolset.json
USER 10001:10001
