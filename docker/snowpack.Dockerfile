# Pinned SNOWPACK engine image (same commit as scripts/build_snowpack.sh).
# Build: docker build -f docker/snowpack.Dockerfile -t snowagent/snowpack:b324cbd .
# NOTE: not built in the development session (native build used); see docs/decisions.md ADR-002.
FROM ubuntu:24.04
RUN apt-get update && apt-get install -y --no-install-recommends git cmake g++ make ca-certificates \
    && rm -rf /var/lib/apt/lists/*
COPY scripts/build_snowpack.sh /usr/local/bin/build_snowpack.sh
RUN SNOWPACK_SRC=/opt/snowpack-src/snowpack-model PREFIX=/opt/snowpack /usr/local/bin/build_snowpack.sh \
    && rm -rf /opt/snowpack-src/snowpack-model/Source/*/build
ENV PATH=/opt/snowpack/bin:$PATH LD_LIBRARY_PATH=/opt/snowpack/lib
ENTRYPOINT ["snowpack"]
