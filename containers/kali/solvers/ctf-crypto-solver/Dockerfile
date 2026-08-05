ARG BASE_IMAGE=tga-kali-base:release
FROM ${BASE_IMAGE}
ARG DEBIAN_FRONTEND=noninteractive

ARG MINIFORGE_VERSION=26.3.2-3
ARG MINIFORGE_SHA256=848194851a98903134187fbb4ab50efe87b003e0c0f808f97644b7524a62bf2c
ARG SAGE_VERSION=10.9
USER root
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl openssl pari-gp \
    && curl --fail --location --show-error --silent \
        "https://github.com/conda-forge/miniforge/releases/download/${MINIFORGE_VERSION}/Miniforge3-${MINIFORGE_VERSION}-Linux-x86_64.sh" \
        -o /tmp/miniforge.sh \
    && echo "${MINIFORGE_SHA256}  /tmp/miniforge.sh" | sha256sum --check --strict - \
    && bash /tmp/miniforge.sh -b -p /opt/sage \
    && /opt/sage/bin/conda install --yes --channel conda-forge "sage=${SAGE_VERSION}" \
    && /opt/sage/bin/conda clean --all --yes \
    && ln -s /opt/sage/bin/sage /usr/local/bin/sage \
    && rm -rf /tmp/* /root/.cache /var/lib/apt/lists/*
COPY --chown=tga:tga toolset.json /opt/tga/toolset.json
USER 10001:10001
