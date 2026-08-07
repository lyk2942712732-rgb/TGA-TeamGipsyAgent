ARG BASE_IMAGE=tga-kali-base:release
ARG GO_BUILDER=golang:1.25-bookworm@sha256:ea341baa9bd5ba6784f6d7161ace70544349a6242d54d34a0fbfd2c4d51c9d58

FROM ${GO_BUILDER} AS go-tools
ARG NUCLEI_VERSION=v3.8.0
ARG DALFOX_VERSION=v2.13.0
ARG FFUF_VERSION=v2.1.0
ARG GOSEC_VERSION=v2.27.1
ARG NAABU_VERSION=v2.3.4
ARG HTTPX_VERSION=v1.7.1
ARG SUBFINDER_VERSION=v2.8.0
ARG DNSX_VERSION=v1.2.2
ARG KITERUNNER_VERSION=1.0.2
ARG KITERUNNER_COMMIT=e7a7fa635c2271cb5aeea7a885c1dc66774bb5c7
RUN apt-get update \
    && apt-get install -y --no-install-recommends git libpcap-dev \
    && rm -rf /var/lib/apt/lists/* \
    && CGO_ENABLED=0 go install github.com/securego/gosec/v2/cmd/gosec@${GOSEC_VERSION} \
    && CGO_ENABLED=0 go install github.com/projectdiscovery/nuclei/v3/cmd/nuclei@${NUCLEI_VERSION} \
    && CGO_ENABLED=0 go install github.com/hahwul/dalfox/v2@${DALFOX_VERSION} \
    && CGO_ENABLED=0 go install github.com/ffuf/ffuf/v2@${FFUF_VERSION} \
    && CGO_ENABLED=1 go install github.com/projectdiscovery/naabu/v2/cmd/naabu@${NAABU_VERSION} \
    && CGO_ENABLED=0 go install github.com/projectdiscovery/httpx/cmd/httpx@${HTTPX_VERSION} \
    && CGO_ENABLED=0 go install github.com/projectdiscovery/subfinder/v2/cmd/subfinder@${SUBFINDER_VERSION} \
    && CGO_ENABLED=0 go install github.com/projectdiscovery/dnsx/cmd/dnsx@${DNSX_VERSION} \
    && git clone --depth 1 --branch "v${KITERUNNER_VERSION}" \
         https://github.com/assetnote/kiterunner.git /src/kiterunner \
    && test "$(git -C /src/kiterunner rev-parse HEAD)" = "${KITERUNNER_COMMIT}" \
    && CGO_ENABLED=0 go -C /src/kiterunner build -o /go/bin/kr ./cmd/kiterunner

FROM ${BASE_IMAGE} AS fuzz-tools
ARG DEBIAN_FRONTEND=noninteractive
ARG HONGGFUZZ_VERSION=2.6
ARG HONGGFUZZ_COMMIT=83a8415a372d84dcc69ac1e2c2f152190bcf76d1
ARG RADAMSA_VERSION=v0.5
ARG RADAMSA_COMMIT=3f53d530499e66e663803d857f2ba4729408e440
ARG OWL_COMMIT=e2f00648b84028fd08ad029a77cc76dd962096a9
USER root
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        binutils-dev build-essential ca-certificates clang curl git \
        libblocksruntime-dev libunwind-dev pkg-config \
    && rm -rf /var/lib/apt/lists/* \
    && git clone --depth 1 --branch "${HONGGFUZZ_VERSION}" \
         https://github.com/google/honggfuzz.git /src/honggfuzz \
    && test "$(git -C /src/honggfuzz rev-parse HEAD)" = "${HONGGFUZZ_COMMIT}" \
    && CFLAGS="-O3 -mtune=native -funroll-loops -Wno-error=unterminated-string-initialization" \
       make -C /src/honggfuzz BUILD_LINUX_NO_BFD=true \
    && git clone --depth 1 --branch "${RADAMSA_VERSION}" \
         https://github.com/aoh/radamsa.git /src/radamsa \
    && test "$(git -C /src/radamsa rev-parse HEAD)" = "${RADAMSA_COMMIT}" \
    && owl_version="$(sed -n 's/^OWLVERSION=//p' /src/radamsa/Makefile | tr -d ' ')" \
    && test -n "${owl_version}" \
    && git clone --depth 1 --branch "v${owl_version}" \
         https://github.com/aoh/owl-lisp.git "/src/radamsa/owl-lisp-${owl_version}" \
    && test "$(git -C "/src/radamsa/owl-lisp-${owl_version}" rev-parse HEAD)" = "${OWL_COMMIT}" \
    && make -C /src/radamsa CFLAGS="-Wall -O2 -std=gnu17" \
    && test -x /src/radamsa/bin/radamsa \
    && test -x /src/honggfuzz/honggfuzz

FROM ${BASE_IMAGE}
ARG DEBIAN_FRONTEND=noninteractive
ARG ARJUN_VERSION=2.2.7
ARG BANDIT_VERSION=1.9.4
ARG CAPA_VERSION=9.4.0
ARG FLOSS_VERSION=3.1.1
ARG FRIDA_TOOLS_VERSION=14.10.4
ARG LIEF_VERSION=0.17.6
ARG MITMPROXY_VERSION=12.2.3
ARG OLETOOLS_VERSION=0.60.2
ARG PEFILE_VERSION=2024.8.26
ARG SEMGREP_VERSION=1.172.0
ARG VOLATILITY_VERSION=2.28.0
ARG ZSTEG_VERSION=0.2.13
ARG CODEQL_VERSION=2.26.2
ARG TRIVY_VERSION=0.73.0
ARG SYFT_VERSION=1.50.0
ARG CASR_VERSION=2.13.1
ARG MINIFORGE_VERSION=26.3.2-3
ARG MINIFORGE_SHA256=848194851a98903134187fbb4ab50efe87b003e0c0f808f97644b7524a62bf2c
ARG SAGE_VERSION=10.9
ARG DIDIER_COMMIT=dea6816048fb2fd3a3597f2e131449fc87f60138
ARG PDFID_SHA256=e958a01f1c2596470f27d3c308061b85d7059ff476a58d74f1957a71b4f1248f
ARG PDF_PARSER_SHA256=a9fa2faa25e88ab0293543d14aa168ff21fc9fd25a0a8ad657b0dad3171214ac
ARG GRAPHQL_COP_VERSION=1.16
ARG GRAPHQL_COP_COMMIT=2b7e086efae672f28b419c7fcdfe6b48d846c9dc
ARG CHAINSAW_VERSION=2.16.2
ARG CHAINSAW_SHA256=c670c3c6c7df9fa929a955892193c94dcc0dd071a2b2e273450bab1f7041602e
ARG HAYABUSA_VERSION=3.10.0
ARG HAYABUSA_SHA256=5879131758c142b0d30d14e4b3f4019155507384d22ad1ce55868670325a071c
ARG EVTX_VERSION=0.12.2
ARG EVTX_SHA256=c33c111c9832cff5b91dfca6fece51d603181a42dfb56f0ed0a25669f2600df9
ARG ZEEK_REPO=https://download.opensuse.org/repositories/security:/zeek/Debian_Testing
USER root

# Install the union of the distro-packaged tools used by all Solver profiles.
RUN echo 'wireshark-common wireshark-common/install-setuid boolean false' | debconf-set-selections \
    && apt-get update \
    && apt-get install -y --no-install-recommends \
        afl++ apktool binutils binwalk bulk-extractor ca-certificates checksec \
        clang cloc cmake commix curl diffoscope dnsutils feroxbuster ffuf \
        foremost g++ gcc gdb ghidra git gnupg gobuster graphviz hashdeep \
        httpie jadx libimage-exiftool-perl libpcap0.8t64 libunwind8 lldb \
        llvm ltrace make musl ngrep nmap nodejs npm openssl pari-gp patchelf \
        plaso pngcheck python3-dev python3-pip python3-venv qemu-user radare2 \
        rizin rr ruby ruby-dev scalpel sigma-cli sqlmap steghide strace \
        tcpdump tcpflow tree tshark universal-ctags unzip valgrind wafw00f \
        whatweb whois xz-utils yara \
    && rm -rf /var/lib/apt/lists/*

# Python and Ruby applications are isolated where possible so their pinned
# dependencies cannot downgrade one another inside the universal image.
RUN PIPX_HOME=/opt/pipx PIPX_BIN_DIR=/usr/local/bin pipx install "semgrep==${SEMGREP_VERSION}" \
    && PIPX_HOME=/opt/pipx PIPX_BIN_DIR=/usr/local/bin pipx install "bandit==${BANDIT_VERSION}" \
    && PIPX_HOME=/opt/pipx PIPX_BIN_DIR=/usr/local/bin pipx install "mitmproxy==${MITMPROXY_VERSION}" \
    && PIPX_HOME=/opt/pipx PIPX_BIN_DIR=/usr/local/bin pipx install "arjun==${ARJUN_VERSION}" \
    && PIPX_HOME=/opt/pipx PIPX_BIN_DIR=/usr/local/bin pipx install "flare-capa==${CAPA_VERSION}" \
    && PIPX_HOME=/opt/pipx PIPX_BIN_DIR=/usr/local/bin pipx install "flare-floss==${FLOSS_VERSION}" \
    && PIPX_HOME=/opt/pipx PIPX_BIN_DIR=/usr/local/bin pipx install "volatility3==${VOLATILITY_VERSION}" \
    && PIPX_HOME=/opt/pipx PIPX_BIN_DIR=/usr/local/bin pipx install "oletools==${OLETOOLS_VERSION}" \
    && PIPX_HOME=/opt/pipx PIPX_BIN_DIR=/usr/local/bin pipx install "frida-tools==${FRIDA_TOOLS_VERSION}" \
    && PIPX_HOME=/opt/pipx PIPX_BIN_DIR=/usr/local/bin pipx install "pwntools==4.14.1" \
    && PIPX_HOME=/opt/pipx pipx inject pwntools "ropper==1.13.13" "ROPGadget==7.6" \
    && if ! command -v vol.py >/dev/null 2>&1; then ln -s /opt/pipx/venvs/volatility3/bin/vol /usr/local/bin/vol.py; fi \
    && ln -s /opt/pipx/venvs/pwntools/bin/ropper /usr/local/bin/ropper \
    && ln -s /opt/pipx/venvs/pwntools/bin/ROPgadget /usr/local/bin/ROPgadget \
    && python3 -m pip install --break-system-packages --no-cache-dir \
        "pefile==${PEFILE_VERSION}" "lief==${LIEF_VERSION}" \
    && gem install zsteg --no-document --version "${ZSTEG_VERSION}" \
    && gem install one_gadget --no-document --version 1.10.0 \
    && npm install --global eslint@9.39.1 \
    && rm -rf /root/.cache

# Install pinned standalone analysis and supply-chain binaries.
RUN curl --fail --location --show-error --silent \
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
       | sed "s#trivy_${TRIVY_VERSION}_Linux-64bit.tar.gz#/tmp/trivy.tar.gz#" | sha256sum --check --strict - \
    && tar -xzf /tmp/trivy.tar.gz -C /usr/local/bin trivy \
    && curl --fail --location --show-error --silent \
        "https://github.com/anchore/syft/releases/download/v${SYFT_VERSION}/syft_${SYFT_VERSION}_linux_amd64.tar.gz" -o /tmp/syft.tar.gz \
    && curl --fail --location --show-error --silent \
        "https://github.com/anchore/syft/releases/download/v${SYFT_VERSION}/syft_${SYFT_VERSION}_checksums.txt" -o /tmp/syft.checksums \
    && grep " syft_${SYFT_VERSION}_linux_amd64.tar.gz$" /tmp/syft.checksums \
       | sed "s#syft_${SYFT_VERSION}_linux_amd64.tar.gz#/tmp/syft.tar.gz#" | sha256sum --check --strict - \
    && tar -xzf /tmp/syft.tar.gz -C /usr/local/bin syft \
    && curl --fail --location --show-error --silent \
        "https://github.com/ispras/casr/releases/download/v${CASR_VERSION}/casr-x86_64-unknown-linux-gnu.tar.xz" -o /tmp/casr.tar.xz \
    && curl --fail --location --show-error --silent \
        "https://github.com/ispras/casr/releases/download/v${CASR_VERSION}/casr-x86_64-unknown-linux-gnu.tar.xz.sha256" -o /tmp/casr.sha256 \
    && sed 's#\*casr-x86_64-unknown-linux-gnu.tar.xz#/tmp/casr.tar.xz#' /tmp/casr.sha256 | sha256sum --check --strict - \
    && tar -xJf /tmp/casr.tar.xz -C /usr/local/bin --strip-components=1 \
        casr-x86_64-unknown-linux-gnu/casr-cli \
    && rm -rf /tmp/*

# Sage remains in its own conda environment; installing it through the Kali
# metapackage would couple the image to the rolling repository's current ABI.
RUN curl --fail --location --show-error --silent \
        "https://github.com/conda-forge/miniforge/releases/download/${MINIFORGE_VERSION}/Miniforge3-${MINIFORGE_VERSION}-Linux-x86_64.sh" \
        -o /tmp/miniforge.sh \
    && echo "${MINIFORGE_SHA256}  /tmp/miniforge.sh" | sha256sum --check --strict - \
    && bash /tmp/miniforge.sh -b -p /opt/sage \
    && /opt/sage/bin/conda install --yes --channel conda-forge "sage=${SAGE_VERSION}" \
    && /opt/sage/bin/conda clean --all --yes \
    && ln -s /opt/sage/bin/sage /usr/local/bin/sage \
    && rm -rf /tmp/* /root/.cache

# Web helpers use private environments to avoid their historical dependency
# pins changing the shared Python used by the other Solver toolchains.
RUN git clone --depth 1 --branch v2.3.0 https://github.com/ticarpi/jwt_tool.git /opt/jwt_tool \
    && test "$(git -C /opt/jwt_tool rev-parse HEAD)" = "3bc7407cf2222d6a821dcc19c776e5a1b1cb9a9b" \
    && python3 -m venv /opt/jwt_tool/venv \
    && /opt/jwt_tool/venv/bin/pip install --no-cache-dir -r /opt/jwt_tool/requirements.txt \
    && printf '#!/bin/sh\nexec /opt/jwt_tool/venv/bin/python /opt/jwt_tool/jwt_tool.py "$@"\n' > /usr/local/bin/jwt_tool \
    && chmod 0755 /usr/local/bin/jwt_tool \
    && git clone --depth 1 --branch "${GRAPHQL_COP_VERSION}" https://github.com/dolevf/graphql-cop.git /opt/graphql-cop \
    && test "$(git -C /opt/graphql-cop rev-parse HEAD)" = "${GRAPHQL_COP_COMMIT}" \
    && python3 -m venv /opt/graphql-cop/venv \
    && /opt/graphql-cop/venv/bin/pip install --no-cache-dir -r /opt/graphql-cop/requirements.txt \
    && printf '#!/bin/sh\nexec /opt/graphql-cop/venv/bin/python /opt/graphql-cop/graphql-cop.py "$@"\n' > /usr/local/bin/graphql-cop \
    && chmod 0755 /usr/local/bin/graphql-cop \
    && rm -rf /opt/jwt_tool/.git /opt/graphql-cop/.git /root/.cache

# Forensics utilities whose release binaries are not provided by Kali.
RUN curl --fail --location --show-error --silent \
        "https://raw.githubusercontent.com/DidierStevens/DidierStevensSuite/${DIDIER_COMMIT}/pdfid.py" -o /usr/local/bin/pdfid \
    && curl --fail --location --show-error --silent \
        "https://raw.githubusercontent.com/DidierStevens/DidierStevensSuite/${DIDIER_COMMIT}/pdf-parser.py" -o /usr/local/bin/pdf-parser \
    && echo "${PDFID_SHA256}  /usr/local/bin/pdfid" | sha256sum --check --strict - \
    && echo "${PDF_PARSER_SHA256}  /usr/local/bin/pdf-parser" | sha256sum --check --strict - \
    && sed -i '1s|.*|#!/usr/bin/python3|' /usr/local/bin/pdfid /usr/local/bin/pdf-parser \
    && chmod 0755 /usr/local/bin/pdfid /usr/local/bin/pdf-parser \
    && curl --fail --location --show-error --silent \
        "https://github.com/WithSecureLabs/chainsaw/releases/download/v${CHAINSAW_VERSION}/chainsaw_x86_64-unknown-linux-gnu.tar.gz" -o /tmp/chainsaw.tar.gz \
    && echo "${CHAINSAW_SHA256}  /tmp/chainsaw.tar.gz" | sha256sum --check --strict - \
    && mkdir -p /opt/chainsaw \
    && tar -xzf /tmp/chainsaw.tar.gz -C /opt/chainsaw --strip-components=1 \
    && ln -s /opt/chainsaw/chainsaw /usr/local/bin/chainsaw \
    && curl --fail --location --show-error --silent \
        "https://github.com/Yamato-Security/hayabusa/releases/download/v${HAYABUSA_VERSION}/hayabusa-${HAYABUSA_VERSION}-lin-x64-gnu.zip" -o /tmp/hayabusa.zip \
    && echo "${HAYABUSA_SHA256}  /tmp/hayabusa.zip" | sha256sum --check --strict - \
    && unzip -q /tmp/hayabusa.zip -d /opt/hayabusa \
    && chmod 0755 "/opt/hayabusa/hayabusa-${HAYABUSA_VERSION}-lin-x64-gnu" \
    && ln -s "/opt/hayabusa/hayabusa-${HAYABUSA_VERSION}-lin-x64-gnu" /usr/local/bin/hayabusa \
    && rm -rf /opt/hayabusa/rules/.git \
    && curl --fail --location --show-error --silent \
        "https://github.com/omerbenamram/evtx/releases/download/v${EVTX_VERSION}/evtx_dump-v${EVTX_VERSION}-x86_64-unknown-linux-gnu" -o /usr/local/bin/evtx_dump \
    && echo "${EVTX_SHA256}  /usr/local/bin/evtx_dump" | sha256sum --check --strict - \
    && chmod 0755 /usr/local/bin/evtx_dump \
    && rm -rf /tmp/*

# Zeek's Kali package is incompatible with the current rolling libc, so retain
# the upstream Debian Testing repository and pinned signing-key policy.
RUN curl -fsSL "${ZEEK_REPO}/Release.key" -o /tmp/zeek.asc \
    && test "$(gpg --show-keys --with-colons /tmp/zeek.asc | awk -F: '/^fpr:/{print $10; exit}')" = "F9FA0223B56B116C363737EF5DA57BDD6DD785CA" \
    && gpg --dearmor -o /usr/share/keyrings/zeek-archive-keyring.gpg /tmp/zeek.asc \
    && printf 'deb [signed-by=/usr/share/keyrings/zeek-archive-keyring.gpg] %s/ /\n' "${ZEEK_REPO}" > /etc/apt/sources.list.d/zeek.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends zeek-core \
    && ln -s /opt/zeek/bin/zeek /usr/local/bin/zeek \
    && rm -f /tmp/zeek.asc \
    && rm -rf /var/lib/apt/lists/*

COPY --from=go-tools /go/bin/gosec /go/bin/nuclei /go/bin/dalfox /go/bin/ffuf \
    /go/bin/naabu /go/bin/httpx /go/bin/subfinder /go/bin/dnsx /go/bin/kr /usr/local/bin/
COPY --from=fuzz-tools /src/honggfuzz/honggfuzz /src/radamsa/bin/radamsa /usr/local/bin/
COPY pe-library-tool.py /usr/local/lib/tga/pe-library-tool.py
RUN chmod 0755 /usr/local/lib/tga/pe-library-tool.py \
    && ln -s /usr/local/lib/tga/pe-library-tool.py /usr/local/bin/pefile \
    && ln -s /usr/local/lib/tga/pe-library-tool.py /usr/local/bin/lief \
    && headless="$(find /usr -type f -name analyzeHeadless -print -quit)" \
    && test -n "${headless}" \
    && ln -s "${headless}" /usr/local/bin/analyzeHeadless \
    && ln -s /usr/bin/plaso-log2timeline /usr/local/bin/log2timeline.py \
    && ln -s /usr/bin/plaso-psort /usr/local/bin/psort.py \
    && ln -s /usr/bin/sigma-cli /usr/local/bin/sigma
COPY --chown=tga:tga toolset.json /opt/tga/toolset.json
USER 10001:10001
