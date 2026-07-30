ARG KALI_BASE
FROM ${KALI_BASE} AS common

ARG DEBIAN_FRONTEND=noninteractive
RUN apt-get update \
    && apt-get install --yes --no-install-recommends ca-certificates tini \
    && groupadd --gid 10001 tga \
    && useradd --uid 10001 --gid tga --create-home --shell /usr/sbin/nologin tga \
    && mkdir -p /workspace/solver /workspace/inputs /workspace/shared \
    && chown -R tga:tga /workspace \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /workspace/solver
USER tga:tga
ENTRYPOINT ["/usr/bin/tini", "--"]

FROM common AS offline
USER root
RUN apt-get update \
    && apt-get install --yes --no-install-recommends binwalk file python3 yara \
    && rm -rf /var/lib/apt/lists/*
USER tga:tga

FROM common AS web
USER root
RUN apt-get update \
    && apt-get install --yes --no-install-recommends ffuf nuclei curl \
    && rm -rf /var/lib/apt/lists/*
USER tga:tga

FROM common AS network
USER root
RUN apt-get update \
    && apt-get install --yes --no-install-recommends iputils-ping libcap2-bin nmap tcpdump \
    && setcap cap_net_raw=eip /usr/bin/ping /usr/bin/nmap /usr/bin/tcpdump \
    && rm -rf /var/lib/apt/lists/*
USER tga:tga
