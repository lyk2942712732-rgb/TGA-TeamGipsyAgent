ARG BASE_IMAGE=ghcr.io/team-gipsy/tga-kali-base@sha256:REPLACE_WITH_RELEASE_DIGEST
FROM ${BASE_IMAGE}

ARG KALI_MIRROR=http://kali.download/kali
USER root
RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt,sharing=locked \
    sed -i "s|^URIs: .*|URIs: ${KALI_MIRROR}/|" /etc/apt/sources.list.d/kali.sources \
    && apt-get update \
    && apt-get install -y --no-install-recommends \
        binutils binwalk checksec clang python3-pip radare2 rizin yara
RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt,sharing=locked \
    apt-get update \
    && apt-get install -y --no-install-recommends ghidra \
    && python3 -m pip install --break-system-packages --no-cache-dir flare-capa==9.2.1 \
    && headless="$(find /usr -type f -name analyzeHeadless -print -quit)" \
    && test -n "$headless" \
    && ln -s "$headless" /usr/local/bin/analyzeHeadless \
    && apt-get clean
COPY --chown=tga:tga toolset.json /opt/tga/toolset.json
USER 10001:10001
