ARG BASE_IMAGE=tga-kali-base:release
FROM ${BASE_IMAGE}
ARG DEBIAN_FRONTEND=noninteractive

ARG ZSTEG_VERSION=0.2.13
ARG PDFID_COMMIT=dea6816048fb2fd3a3597f2e131449fc87f60138
ARG PDFID_SHA256=e958a01f1c2596470f27d3c308061b85d7059ff476a58d74f1957a71b4f1248f
USER root
RUN apt-get update \
    && apt-get install -y --no-install-recommends binutils binwalk checksec curl libimage-exiftool-perl pngcheck ruby tshark yara \
    && gem install zsteg --no-document --version "${ZSTEG_VERSION}" \
    && curl --fail --location --show-error --silent \
        "https://raw.githubusercontent.com/DidierStevens/DidierStevensSuite/${PDFID_COMMIT}/pdfid.py" \
        --output /usr/local/bin/pdfid \
    && echo "${PDFID_SHA256}  /usr/local/bin/pdfid" | sha256sum --check --strict - \
    && sed -i '1s|.*|#!/usr/bin/python3|' /usr/local/bin/pdfid \
    && chmod 0755 /usr/local/bin/pdfid \
    && rm -rf /var/lib/apt/lists/*
COPY --chown=tga:tga toolset.json /opt/tga/toolset.json
USER 10001:10001
