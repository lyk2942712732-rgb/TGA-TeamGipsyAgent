ARG BASE_IMAGE=ghcr.io/team-gipsy/tga-kali-base@sha256:REPLACE_WITH_RELEASE_DIGEST
FROM ${BASE_IMAGE}
ARG DEBIAN_FRONTEND=noninteractive

ARG CHAINSAW_VERSION=2.16.2
ARG CHAINSAW_SHA256=c670c3c6c7df9fa929a955892193c94dcc0dd071a2b2e273450bab1f7041602e
ARG HAYABUSA_VERSION=3.10.0
ARG HAYABUSA_SHA256=5879131758c142b0d30d14e4b3f4019155507384d22ad1ce55868670325a071c
ARG EVTX_VERSION=0.12.2
ARG EVTX_SHA256=c33c111c9832cff5b91dfca6fece51d603181a42dfb56f0ed0a25669f2600df9
USER root
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl plaso sigma-cli unzip \
    && curl --fail --location --show-error --silent \
        "https://github.com/WithSecureLabs/chainsaw/releases/download/v${CHAINSAW_VERSION}/chainsaw_x86_64-unknown-linux-gnu.tar.gz" -o /tmp/chainsaw.tar.gz \
    && echo "${CHAINSAW_SHA256}  /tmp/chainsaw.tar.gz" | sha256sum --check --strict - \
    && mkdir -p /opt/chainsaw \
    && tar -xzf /tmp/chainsaw.tar.gz -C /opt/chainsaw --strip-components=1 \
    && ln -s /opt/chainsaw/chainsaw /usr/local/bin/chainsaw \
    && curl --fail --location --show-error --silent \
        "https://github.com/Yamato-Security/hayabusa/releases/download/v${HAYABUSA_VERSION}/hayabusa-${HAYABUSA_VERSION}-lin-x64-gnu.zip" -o /tmp/hayabusa.zip \
    && echo "${HAYABUSA_SHA256}  /tmp/hayabusa.zip" | sha256sum --check --strict - \
    && unzip -q /tmp/hayabusa.zip -d /opt/hayabusa \
    && chmod 0755 "/opt/hayabusa/hayabusa-${HAYABUSA_VERSION}-lin-x64-gnu" \
    && ln -s "/opt/hayabusa/hayabusa-${HAYABUSA_VERSION}-lin-x64-gnu" /usr/local/bin/hayabusa \
    && rm -rf /opt/hayabusa/rules/.git \
    && curl --fail --location --show-error --silent \
        "https://github.com/omerbenamram/evtx/releases/download/v${EVTX_VERSION}/evtx_dump-v${EVTX_VERSION}-x86_64-unknown-linux-gnu" -o /usr/local/bin/evtx_dump \
    && echo "${EVTX_SHA256}  /usr/local/bin/evtx_dump" | sha256sum --check --strict - \
    && chmod 0755 /usr/local/bin/evtx_dump \
    && rm -rf /tmp/* /var/lib/apt/lists/*
COPY --chown=tga:tga toolset.json /opt/tga/toolset.json
USER 10001:10001
