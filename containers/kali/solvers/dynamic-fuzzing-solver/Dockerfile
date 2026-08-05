ARG BASE_IMAGE=tga-kali-base:release

FROM ${BASE_IMAGE} AS fuzz-tools
ARG DEBIAN_FRONTEND=noninteractive
# The base image ends on USER 10001, so this build stage starts unprivileged
# and apt-get fails with "List directory /var/lib/apt/lists/partial is
# missing - Permission denied". Only the final stage below had switched back.
USER root
# honggfuzz 2.6 predates GCC 15's -Wunterminated-string-initialization, and its
# own Makefile builds with -Werror, so mangle.c's byte-array literals now break
# the build. GCC gives the specific -Wno-error= priority over the blanket
# -Werror, so every other warning stays fatal.
#
# CFLAGS must be passed through the environment, not as a make argument: a
# command-line variable overrides the Makefile's own `CFLAGS += $(COMMON_CFLAGS)
# $(ARCH_CFLAGS)`, which silently drops -I. and the arch defines. From the
# environment it only satisfies the upstream `CFLAGS ?=`, and the appends still
# apply.
#
# binutils-dev supplies libbfd/libopcodes. BUILD_LINUX_NO_BFD only adds a
# -D define in 2.6; the Makefile still puts `-lopcodes -lbfd` on the link line
# unconditionally, so the link fails without them. Build stage only -- the
# final image copies just the two binaries.
#
# radamsa's own `get-owl` target cannot run: it fetches
# github.com/aoh/owl-lisp/archive/v$OWLVERSION.tar.gz and then cd's into
# owl-lisp-$OWLVERSION, but that repository was renamed to `owl`, so GitHub now
# names the archive root owl-$OWLVERSION and the cd fails. Cloning owl at the
# expected path satisfies the target's `test -d` guard, skips the broken
# download, and lets the commit be pinned like every other source here.
#
# owl-lisp and radamsa both predate C23, where `f()` means "no parameters"
# rather than "unspecified"; GCC 15 defaults to -std=gnu23 and rejects
# `word vm();` against its two-argument definition. -std=gnu17 restores the
# semantics the sources were written against.
#
# Note the two CFLAGS mechanisms are not interchangeable. honggfuzz needs the
# environment (its Makefile appends to CFLAGS); radamsa needs a command-line
# variable (its Makefile assigns CFLAGS outright, which the environment cannot
# override). Command-line variables also reach the nested owl build.
ARG HONGGFUZZ_VERSION=2.6
ARG HONGGFUZZ_COMMIT=83a8415a372d84dcc69ac1e2c2f152190bcf76d1
ARG RADAMSA_VERSION=v0.5
ARG RADAMSA_COMMIT=3f53d530499e66e663803d857f2ba4729408e440
ARG OWL_COMMIT=e2f00648b84028fd08ad029a77cc76dd962096a9
RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential binutils-dev ca-certificates clang curl git libblocksruntime-dev libunwind-dev pkg-config \
    && rm -rf /var/lib/apt/lists/* \
    && git clone --depth 1 --branch "${HONGGFUZZ_VERSION}" https://github.com/google/honggfuzz.git /src/honggfuzz \
    && test "$(git -C /src/honggfuzz rev-parse HEAD)" = "${HONGGFUZZ_COMMIT}" \
    && CFLAGS="-O3 -mtune=native -funroll-loops -Wno-error=unterminated-string-initialization" \
       make -C /src/honggfuzz BUILD_LINUX_NO_BFD=true \
    && git clone --depth 1 --branch "${RADAMSA_VERSION}" https://github.com/aoh/radamsa.git /src/radamsa \
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

USER root
RUN apt-get update \
    && apt-get install -y --no-install-recommends afl++ clang libunwind8 \
    && rm -rf /var/lib/apt/lists/*
COPY --from=fuzz-tools /src/honggfuzz/honggfuzz /src/radamsa/bin/radamsa /usr/local/bin/
COPY --chown=tga:tga toolset.json /opt/tga/toolset.json
USER 10001:10001
