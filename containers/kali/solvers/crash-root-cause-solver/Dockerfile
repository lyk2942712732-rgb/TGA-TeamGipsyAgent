ARG BASE_IMAGE=ghcr.io/team-gipsy/tga-kali-base@sha256:REPLACE_WITH_RELEASE_DIGEST
FROM ${BASE_IMAGE}
ARG DEBIAN_FRONTEND=noninteractive

ARG CASR_VERSION=2.13.1
USER root
RUN apt-get update \
    && apt-get install -y --no-install-recommends binutils curl gdb lldb llvm musl valgrind xz-utils \
    && curl --fail --location --show-error --silent \
        "https://github.com/ispras/casr/releases/download/v${CASR_VERSION}/casr-x86_64-unknown-linux-gnu.tar.xz" -o /tmp/casr.tar.xz \
    && curl --fail --location --show-error --silent \
        "https://github.com/ispras/casr/releases/download/v${CASR_VERSION}/casr-x86_64-unknown-linux-gnu.tar.xz.sha256" -o /tmp/casr.sha256 \
    && sed 's#\*casr-x86_64-unknown-linux-gnu.tar.xz#/tmp/casr.tar.xz#' /tmp/casr.sha256 | sha256sum --check --strict - \
    && tar -xJf /tmp/casr.tar.xz -C /usr/local/bin --strip-components=1 \
        casr-x86_64-unknown-linux-gnu/casr-cli \
    && test -x /usr/local/bin/casr-cli \
    && rm -rf /tmp/* /var/lib/apt/lists/*
COPY --chown=tga:tga toolset.json /opt/tga/toolset.json
USER 10001:10001
