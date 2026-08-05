# Build the TGA-Runtime filesystem.
#
# This image is never run. It exists to be `docker export`ed into the tarball
# that `wsl --import` turns into the dedicated distribution, which is why the
# last thing it does is delete the sources it was built from: whatever is left
# here is what ships on every user's machine.
#
# Section 10 of the design asks for a distribution with Python, the TGA
# package, the frontend bundle, Docker, runsc, nftables, sandboxd, systemd
# units and tga-internal already present, so that a first `tga up` has nothing
# left to install.
ARG UBUNTU_BASE=ubuntu:24.04
ARG GO_BUILDER=golang:1.26-bookworm

# sandboxd is built here rather than inside the runtime image. provision.sh
# takes a prebuilt binary when one is present and otherwise builds from source
# with Go -- and shipping a Go toolchain inside every user's distribution to
# compile one 20 MB binary at provision time would be absurd. Without this
# stage neither path is available and the export check fails on a missing
# /opt/tga/bin/tga-sandboxd.
FROM ${GO_BUILDER} AS sandboxd
COPY sandboxd /src/sandboxd
RUN cd /src/sandboxd \
    && CGO_ENABLED=0 go build -trimpath -o /out/tga-sandboxd ./cmd/tga-sandboxd

FROM ${UBUNTU_BASE}

ARG DEBIAN_FRONTEND=noninteractive

# provision.sh does the work, and it is the same script a Linux server runs.
# Keeping one installer means the distribution cannot drift from the server
# path: anything that breaks here breaks there, in CI, before release.
COPY . /src
COPY --from=sandboxd /out/tga-sandboxd /src/dist/tga-sandboxd
RUN SOURCE_DIR=/src /src/deploy/wsl-rootfs/provision.sh

COPY deploy/wsl-rootfs/wsl.conf /etc/wsl.conf

# The sources were copied into /opt/tga/app by provisioning; the build context
# itself has no reason to travel to a user's machine.
RUN rm -rf /src \
    && rm -rf /var/lib/apt/lists/* /var/cache/apt/archives/*.deb \
    && rm -rf /root/.cache /tmp/*

# A marker the launcher can read to tell a provisioned distribution from an
# empty one without running anything inside it.
ARG TGA_VERSION=dev
RUN printf '{"version":"%s","layout":1}\n' "$TGA_VERSION" > /opt/tga/version.json
