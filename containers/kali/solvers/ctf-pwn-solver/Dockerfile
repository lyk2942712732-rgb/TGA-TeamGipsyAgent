ARG BASE_IMAGE=tga-kali-base:release
FROM ${BASE_IMAGE}
ARG DEBIAN_FRONTEND=noninteractive

USER root
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        binutils checksec clang gdb gcc make patchelf python3-pip qemu-user ruby ruby-dev strace \
    && python3 -m pip install --break-system-packages --no-cache-dir \
        pwntools==4.14.1 ropper==1.13.13 ROPGadget==7.6 \
    && gem install one_gadget --no-document --version 1.10.0 \
    && rm -rf /var/lib/apt/lists/*
COPY --chown=tga:tga toolset.json /opt/tga/toolset.json
USER 10001:10001
