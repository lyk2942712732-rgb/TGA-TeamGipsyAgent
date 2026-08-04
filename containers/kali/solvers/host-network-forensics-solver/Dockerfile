ARG BASE_IMAGE=tga-kali-base:release
FROM ${BASE_IMAGE}

ARG DEBIAN_FRONTEND=noninteractive
# Zeek comes from the project's own repository rather than Kali's. Kali still
# ships zeek 5.1.1-0kali3, which declares `Depends: libc6 (< 2.38)` while
# kali-rolling is on 2.42 -- the package is simply not installable any more,
# and it took the whole apt transaction down with it.
ARG ZEEK_REPO=https://download.opensuse.org/repositories/security:/zeek/Debian_Testing
# Pinning the signing key matters more than pinning the version here: the key
# is stable, whereas this repository only carries the current release.
ARG ZEEK_KEY_FINGERPRINT=F9FA0223B56B116C363737EF5DA57BDD6DD785CA

USER root
RUN echo 'wireshark-common wireshark-common/install-setuid boolean false' | debconf-set-selections \
    && apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl gnupg ngrep tcpdump tcpflow tshark \
    && curl -fsSL "${ZEEK_REPO}/Release.key" -o /tmp/zeek.asc \
    && test "$(gpg --show-keys --with-colons /tmp/zeek.asc | awk -F: '/^fpr:/{print $10; exit}')" = "${ZEEK_KEY_FINGERPRINT}" \
    && gpg --dearmor -o /usr/share/keyrings/zeek-archive-keyring.gpg /tmp/zeek.asc \
    && rm -f /tmp/zeek.asc \
    && printf 'deb [signed-by=/usr/share/keyrings/zeek-archive-keyring.gpg] %s/ /\n' "${ZEEK_REPO}" \
         > /etc/apt/sources.list.d/zeek.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends zeek-core \
    # zeek-core installs under /opt/zeek, which is not on PATH; the profile
    # declares a plain `zeek`, and readiness resolves it with shutil.which.
    && ln -s /opt/zeek/bin/zeek /usr/local/bin/zeek \
    && zeek --version \
    && apt-get purge -y --auto-remove curl gnupg \
    && rm -rf /var/lib/apt/lists/*
COPY --chown=tga:tga toolset.json /opt/tga/toolset.json
USER 10001:10001
