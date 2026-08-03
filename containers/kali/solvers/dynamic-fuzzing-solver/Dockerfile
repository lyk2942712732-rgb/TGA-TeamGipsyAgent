ARG BASE_IMAGE=ghcr.io/team-gipsy/tga-kali-base@sha256:REPLACE_WITH_RELEASE_DIGEST

FROM ${BASE_IMAGE} AS fuzz-tools
ARG DEBIAN_FRONTEND=noninteractive
ARG HONGGFUZZ_VERSION=2.6
ARG HONGGFUZZ_COMMIT=83a8415a372d84dcc69ac1e2c2f152190bcf76d1
ARG RADAMSA_VERSION=v0.5
ARG RADAMSA_COMMIT=3f53d530499e66e663803d857f2ba4729408e440
RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential ca-certificates clang git libblocksruntime-dev libunwind-dev pkg-config \
    && rm -rf /var/lib/apt/lists/* \
    && git clone --depth 1 --branch "${HONGGFUZZ_VERSION}" https://github.com/google/honggfuzz.git /src/honggfuzz \
    && test "$(git -C /src/honggfuzz rev-parse HEAD)" = "${HONGGFUZZ_COMMIT}" \
    && make -C /src/honggfuzz BUILD_LINUX_NO_BFD=true \
    && git clone --depth 1 --branch "${RADAMSA_VERSION}" https://github.com/aoh/radamsa.git /src/radamsa \
    && test "$(git -C /src/radamsa rev-parse HEAD)" = "${RADAMSA_COMMIT}" \
    && make -C /src/radamsa

FROM ${BASE_IMAGE}
ARG DEBIAN_FRONTEND=noninteractive

USER root
RUN apt-get update \
    && apt-get install -y --no-install-recommends afl++ clang libunwind8 \
    && rm -rf /var/lib/apt/lists/*
COPY --from=fuzz-tools /src/honggfuzz/honggfuzz /src/radamsa/bin/radamsa /usr/local/bin/
COPY --chown=tga:tga toolset.json /opt/tga/toolset.json
USER 10001:10001
