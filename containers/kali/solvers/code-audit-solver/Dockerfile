ARG BASE_IMAGE=tga-kali-base:release
ARG GO_BUILDER=golang:1.24-bookworm@sha256:1a6d4452c65dea36aac2e2d606b01b4a029ec90cc1ae53890540ce6173ea77ac

FROM ${GO_BUILDER} AS go-tools
ARG DEBIAN_FRONTEND=noninteractive
ARG GOSEC_VERSION=v2.22.9
RUN CGO_ENABLED=0 go install github.com/securego/gosec/v2/cmd/gosec@${GOSEC_VERSION}

FROM ${BASE_IMAGE}
ARG DEBIAN_FRONTEND=noninteractive
ARG SEMGREP_VERSION=1.172.0
ARG BANDIT_VERSION=1.9.4
ARG ESLINT_VERSION=9.39.1
ARG CODEQL_VERSION=2.26.2
ARG TRIVY_VERSION=0.73.0
ARG SYFT_VERSION=1.50.0
USER root
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl nodejs npm python3-pip \
    && PIPX_HOME=/opt/pipx PIPX_BIN_DIR=/usr/local/bin pipx install "semgrep==${SEMGREP_VERSION}" \
    && PIPX_HOME=/opt/pipx PIPX_BIN_DIR=/usr/local/bin pipx install "bandit==${BANDIT_VERSION}" \
    && npm install --global "eslint@${ESLINT_VERSION}" \
    && curl --fail --location --show-error --silent \
        "https://github.com/github/codeql-cli-binaries/releases/download/v${CODEQL_VERSION}/codeql-linux64.zip" -o /tmp/codeql.zip \
    && curl --fail --location --show-error --silent \
        "https://github.com/github/codeql-cli-binaries/releases/download/v${CODEQL_VERSION}/codeql-linux64.zip.checksum.txt" -o /tmp/codeql.sha256 \
    && sed 's#codeql-linux64.zip#/tmp/codeql.zip#' /tmp/codeql.sha256 | sha256sum --check --strict - \
    && unzip -q /tmp/codeql.zip -d /opt \
    && ln -s /opt/codeql/codeql /usr/local/bin/codeql \
    && curl --fail --location --show-error --silent \
        "https://github.com/aquasecurity/trivy/releases/download/v${TRIVY_VERSION}/trivy_${TRIVY_VERSION}_Linux-64bit.tar.gz" -o /tmp/trivy.tar.gz \
    && curl --fail --location --show-error --silent \
        "https://github.com/aquasecurity/trivy/releases/download/v${TRIVY_VERSION}/trivy_${TRIVY_VERSION}_checksums.txt" -o /tmp/trivy.checksums \
    && grep " trivy_${TRIVY_VERSION}_Linux-64bit.tar.gz$" /tmp/trivy.checksums \
        | sed "s#trivy_${TRIVY_VERSION}_Linux-64bit.tar.gz#/tmp/trivy.tar.gz#" \
        | sha256sum --check --strict - \
    && tar -xzf /tmp/trivy.tar.gz -C /usr/local/bin trivy \
    && curl --fail --location --show-error --silent \
        "https://github.com/anchore/syft/releases/download/v${SYFT_VERSION}/syft_${SYFT_VERSION}_linux_amd64.tar.gz" -o /tmp/syft.tar.gz \
    && curl --fail --location --show-error --silent \
        "https://github.com/anchore/syft/releases/download/v${SYFT_VERSION}/syft_${SYFT_VERSION}_checksums.txt" -o /tmp/syft.checksums \
    && grep " syft_${SYFT_VERSION}_linux_amd64.tar.gz$" /tmp/syft.checksums \
        | sed "s#syft_${SYFT_VERSION}_linux_amd64.tar.gz#/tmp/syft.tar.gz#" \
        | sha256sum --check --strict - \
    && tar -xzf /tmp/syft.tar.gz -C /usr/local/bin syft \
    && rm -rf /tmp/* /root/.cache /var/lib/apt/lists/*
COPY --from=go-tools /go/bin/gosec /usr/local/bin/gosec
COPY --chown=tga:tga toolset.json /opt/tga/toolset.json
USER 10001:10001
