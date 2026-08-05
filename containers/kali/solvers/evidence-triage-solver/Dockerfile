ARG BASE_IMAGE=tga-kali-base:release
FROM ${BASE_IMAGE}
ARG DEBIAN_FRONTEND=noninteractive

USER root
RUN apt-get update \
    && apt-get install -y --no-install-recommends binutils binwalk hashdeep libimage-exiftool-perl yara \
    && rm -rf /var/lib/apt/lists/*
COPY --chown=tga:tga toolset.json /opt/tga/toolset.json
USER 10001:10001
