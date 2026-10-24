# Disposable agent image for spike H1: OpenCode, python3, git and the quenching-opencode port.
FROM node:22-bookworm-slim
RUN apt-get update && apt-get install -y --no-install-recommends python3 git ca-certificates \
    && rm -rf /var/lib/apt/lists/*
ARG OPENCODE_VERSION=1.19.4
RUN npm install -g "opencode-ai@${OPENCODE_VERSION}" && npm cache clean --force
ARG QUENCHING_SHA=unknown
LABEL forge.spike=h1 quenching.sha=${QUENCHING_SHA} opencode.version=${OPENCODE_VERSION}
# The port's three parts, laid out as its README installs them; copied into $HOME at start.
COPY quenching-opencode/commands /opt/opencode/commands
COPY quenching-opencode/agents /opt/opencode/agents
COPY quenching-opencode/quenching /opt/opencode/quenching
COPY opencode.json /opt/opencode/opencode.json
COPY entrypoint.sh /opt/h1/
ENV XDG_CACHE_HOME=/opt/cache OPENCODE_DISABLE_AUTOUPDATE=1
RUN mkdir -p /opt/cache && chmod -R a+rwX /opt/cache /opt/opencode
ENTRYPOINT ["/bin/sh", "/opt/h1/entrypoint.sh"]
