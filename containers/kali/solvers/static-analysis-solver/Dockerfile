ARG BASE_IMAGE=ghcr.io/team-gipsy/tga-kali-base@sha256:REPLACE_WITH_RELEASE_DIGEST
FROM ${BASE_IMAGE}

USER root
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        binutils binwalk checksec clang ghidra python3-pip radare2 rizin yara \
    && python3 -m pip install --break-system-packages --no-cache-dir flare-capa==9.2.1 \
    && headless="$(find /usr -type f -name analyzeHeadless -print -quit)" \
    && test -n "$headless" \
    && ln -s "$headless" /usr/local/bin/analyzeHeadless \
    && rm -rf /var/lib/apt/lists/*
COPY --chown=tga:tga toolset.json /opt/tga/toolset.json
USER 10001:10001
