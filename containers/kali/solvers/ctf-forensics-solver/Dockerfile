ARG BASE_IMAGE=tga-kali-base:release
FROM ${BASE_IMAGE}
ARG DEBIAN_FRONTEND=noninteractive

ARG ZSTEG_VERSION=0.2.13
ARG VOLATILITY_VERSION=2.28.0
ARG OLETOOLS_VERSION=0.60.2
ARG DIDIER_COMMIT=dea6816048fb2fd3a3597f2e131449fc87f60138
ARG PDFID_SHA256=e958a01f1c2596470f27d3c308061b85d7059ff476a58d74f1957a71b4f1248f
ARG PDF_PARSER_SHA256=a9fa2faa25e88ab0293543d14aa168ff21fc9fd25a0a8ad657b0dad3171214ac
USER root
RUN apt-get update \
    && apt-get install -y --no-install-recommends binwalk bulk-extractor curl foremost libimage-exiftool-perl pngcheck python3-pip ruby scalpel steghide tshark \
    && gem install zsteg --no-document --version "${ZSTEG_VERSION}" \
    && python3 -m pip install --break-system-packages --no-cache-dir \
        "volatility3==${VOLATILITY_VERSION}" "oletools==${OLETOOLS_VERSION}" \
    && if ! command -v vol.py >/dev/null; then ln -s "$(command -v vol)" /usr/local/bin/vol.py; fi \
    && curl --fail --location --show-error --silent \
        "https://raw.githubusercontent.com/DidierStevens/DidierStevensSuite/${DIDIER_COMMIT}/pdfid.py" -o /usr/local/bin/pdfid \
    && curl --fail --location --show-error --silent \
        "https://raw.githubusercontent.com/DidierStevens/DidierStevensSuite/${DIDIER_COMMIT}/pdf-parser.py" -o /usr/local/bin/pdf-parser \
    && echo "${PDFID_SHA256}  /usr/local/bin/pdfid" | sha256sum --check --strict - \
    && echo "${PDF_PARSER_SHA256}  /usr/local/bin/pdf-parser" | sha256sum --check --strict - \
    && sed -i '1s|.*|#!/usr/bin/python3|' /usr/local/bin/pdfid /usr/local/bin/pdf-parser \
    && chmod 0755 /usr/local/bin/pdfid /usr/local/bin/pdf-parser \
    && rm -rf /var/lib/apt/lists/*
COPY --chown=tga:tga toolset.json /opt/tga/toolset.json
USER 10001:10001
