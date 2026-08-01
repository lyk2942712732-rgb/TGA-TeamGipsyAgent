ARG KALI_BASE=kalilinux/kali-rolling@sha256:dea2bdf0e8c0ca1deb51b7a6253f481acae3ca9c2f1e2371077e6af55e5b2721
FROM ${KALI_BASE}

ARG DEBIAN_FRONTEND=noninteractive
RUN sed -i 's|http://http.kali.org/kali/|http://kali.download/kali/|' \
        /etc/apt/sources.list.d/kali.sources \
    && printf '%s\n' \
        'Acquire::Retries "5";' \
        'Acquire::http::Timeout "30";' \
        'Acquire::https::Timeout "30";' \
        > /etc/apt/apt.conf.d/80-tga-network \
    && apt-get update \
    && apt-get install -y --no-install-recommends \
        bash binutils ca-certificates coreutils file findutils gawk git grep jq \
        p7zip-full pipx python3 ripgrep sed tar tini unzip vim-common yq \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 10001 --user-group --shell /bin/bash tga \
    && mkdir -p /workspace/solver /workspace/inputs /workspace/shared /opt/tga \
    && chown -R tga:tga /workspace/solver /opt/tga

USER 10001:10001
WORKDIR /workspace/solver
ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["/usr/bin/tail", "-f", "/dev/null"]
