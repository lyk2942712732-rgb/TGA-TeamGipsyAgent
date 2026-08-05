ARG BASE_IMAGE=tga-kali-base:release
FROM ${BASE_IMAGE}
ARG DEBIAN_FRONTEND=noninteractive

ARG FRIDA_TOOLS_VERSION=14.10.4
USER root
RUN apt-get update \
    && apt-get install -y --no-install-recommends gdb lldb ltrace python3-pip qemu-user rr strace valgrind \
    && python3 -m pip install --break-system-packages --no-cache-dir "frida-tools==${FRIDA_TOOLS_VERSION}" \
    && rm -rf /var/lib/apt/lists/*
COPY --chown=tga:tga toolset.json /opt/tga/toolset.json
USER 10001:10001
