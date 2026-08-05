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
FROM ${UBUNTU_BASE}

ARG DEBIAN_FRONTEND=noninteractive

# provision.sh does the work, and it is the same script a Linux server runs.
# Keeping one installer means the distribution cannot drift from the server
# path: anything that breaks here breaks there, in CI, before release.
COPY . /src
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
