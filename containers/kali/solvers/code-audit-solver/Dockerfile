ARG BASE_IMAGE=tga-kali-base:release
# Go 1.25 is required by the gosec version below; every gosec release that
# carries the fixed grpc declares `go 1.25`. Only this image needs it, so the
# other solvers keep the 1.24 builder rather than having their binaries rebuilt
# on a new toolchain for no reason.
ARG GO_BUILDER=golang:1.25-bookworm@sha256:ea341baa9bd5ba6784f6d7161ace70544349a6242d54d34a0fbfd2c4d51c9d58

FROM ${GO_BUILDER} AS go-tools
ARG DEBIAN_FRONTEND=noninteractive
# gosec v2.22.9 builds against google.golang.org/grpc v1.75.0, which carries
# CVE-2026-33186 (CRITICAL, fixed in 1.79.3) and fails the release scan.
# v2.27.1 is on grpc v1.81.1 and declares `go 1.25.0`; v2.28.0 would also do
# but demands 1.25.8, needlessly tightening what the builder must ship.
ARG GOSEC_VERSION=v2.27.1
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
