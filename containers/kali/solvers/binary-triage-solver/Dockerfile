ARG BASE_IMAGE=tga-kali-base:release
FROM ${BASE_IMAGE}
ARG DEBIAN_FRONTEND=noninteractive

ARG CAPA_VERSION=9.4.0
USER root
RUN apt-get update \
    && apt-get install -y --no-install-recommends binutils binwalk checksec libimage-exiftool-perl python3-pip radare2 yara \
    && python3 -m pip install --break-system-packages --no-cache-dir "flare-capa==${CAPA_VERSION}" \
    && rm -rf /var/lib/apt/lists/*
COPY --chown=tga:tga toolset.json /opt/tga/toolset.json
USER 10001:10001
