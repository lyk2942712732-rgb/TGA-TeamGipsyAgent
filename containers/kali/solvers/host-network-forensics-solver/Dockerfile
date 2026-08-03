ARG BASE_IMAGE=ghcr.io/team-gipsy/tga-kali-base@sha256:REPLACE_WITH_RELEASE_DIGEST
FROM ${BASE_IMAGE}

ARG DEBIAN_FRONTEND=noninteractive
USER root
RUN echo 'wireshark-common wireshark-common/install-setuid boolean false' | debconf-set-selections \
    && apt-get update \
    && apt-get install -y --no-install-recommends ngrep tcpdump tcpflow tshark zeek \
    && rm -rf /var/lib/apt/lists/*
COPY --chown=tga:tga toolset.json /opt/tga/toolset.json
USER 10001:10001
