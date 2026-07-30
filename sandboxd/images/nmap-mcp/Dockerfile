ARG KALI_BASE
FROM ${KALI_BASE}

ARG DEBIAN_FRONTEND=noninteractive
RUN apt-get update \
    && apt-get install --yes --no-install-recommends iputils-ping libcap2-bin nmap python3 tcpdump tini \
    && groupadd --gid 10001 tga \
    && useradd --uid 10001 --gid tga --no-create-home --shell /usr/sbin/nologin tga \
    && setcap cap_net_raw=eip /usr/bin/ping /usr/bin/nmap /usr/bin/tcpdump \
    && rm -rf /var/lib/apt/lists/*
COPY --chown=10001:10001 server.py /opt/tga/server.py
USER 10001:10001
ENTRYPOINT ["/usr/bin/tini", "--", "/usr/bin/python3", "/opt/tga/server.py"]
