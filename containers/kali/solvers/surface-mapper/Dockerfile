ARG BASE_IMAGE=tga-kali-base:release
ARG GO_BUILDER=golang:1.25-bookworm@sha256:ea341baa9bd5ba6784f6d7161ace70544349a6242d54d34a0fbfd2c4d51c9d58

FROM ${GO_BUILDER} AS projectdiscovery
ARG DEBIAN_FRONTEND=noninteractive

ARG NAABU_VERSION=v2.3.4
ARG HTTPX_VERSION=v1.7.1
ARG SUBFINDER_VERSION=v2.8.0
ARG DNSX_VERSION=v1.2.2
ARG NUCLEI_VERSION=v3.8.0
RUN apt-get update \
    && apt-get install -y --no-install-recommends libpcap-dev \
    && rm -rf /var/lib/apt/lists/* \
    && CGO_ENABLED=1 go install github.com/projectdiscovery/naabu/v2/cmd/naabu@${NAABU_VERSION} \
    && CGO_ENABLED=0 go install github.com/projectdiscovery/httpx/cmd/httpx@${HTTPX_VERSION} \
    && CGO_ENABLED=0 go install github.com/projectdiscovery/subfinder/v2/cmd/subfinder@${SUBFINDER_VERSION} \
    && CGO_ENABLED=0 go install github.com/projectdiscovery/dnsx/cmd/dnsx@${DNSX_VERSION} \
    && CGO_ENABLED=0 go install github.com/projectdiscovery/nuclei/v3/cmd/nuclei@${NUCLEI_VERSION}

FROM ${BASE_IMAGE}
ARG DEBIAN_FRONTEND=noninteractive

USER root
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        curl dnsutils feroxbuster ffuf libpcap0.8t64 nmap wafw00f whatweb whois \
    && rm -rf /var/lib/apt/lists/* \
    && install -o tga -g tga -m 0644 /dev/null /opt/tga/toolset.json
COPY --from=projectdiscovery /go/bin/naabu /go/bin/httpx /go/bin/subfinder /go/bin/dnsx /go/bin/nuclei /usr/local/bin/
COPY --chown=tga:tga toolset.json /opt/tga/toolset.json
USER 10001:10001
