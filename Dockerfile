# merkl-mcp as a container: python:3.12-slim, this package and its
# dependencies (mcp, merkl-sdk[xrpl]), nothing else.
#
#   docker run --rm -i \
#     -v "$PWD/merkl-agent:/agent:ro" -v merkl-mcp:/var/lib/merkl-mcp \
#     ghcr.io/ramcav/merkl-mcp:0.1.0
#
# stdio by default (add -i so the harness's stdin actually reaches the
# container). For streamable HTTP, publish the port and pass --http:
#
#   docker run --rm -p 8765:8765 \
#     -v "$PWD/merkl-agent:/agent:ro" -v merkl-mcp:/var/lib/merkl-mcp \
#     ghcr.io/ramcav/merkl-mcp:0.1.0 --http :8765
#
# /agent is read-only: this reads its bundle there (trader.toml,
# agent-ed25519.pem, wallet.json, and the optional relay/notary secret files
# — the same bundle `merkl treasury init` writes, see merkl-sdk's
# merkl/cli/bundle.py) and writes nothing back. The one proposal a person may
# be deciding on, and the local receipt store, live on /var/lib/merkl-mcp
# instead ($MERKL_MCP_STATE, merkl_mcp/state.py) — the volume this image
# mounts writable.
FROM python:3.12-slim

LABEL org.opencontainers.image.source="https://github.com/ramcav/merkl-mcp" \
      org.opencontainers.image.description="Merkl as a tool server: an MCP server for a Merkl-governed treasury"

COPY . /src
RUN pip install --no-cache-dir /src && rm -rf /src

# Unprivileged from the start, same reasoning as merkl-trader's image: no
# host-owned bind mount to chown before this starts — /agent arrives
# read-only, uid 10002 both places so a bundle prepared for one runs
# unmodified under the other.
RUN useradd --system --uid 10002 --user-group --no-create-home merkl-mcp \
    && mkdir -p /var/lib/merkl-mcp /agent \
    && chown merkl-mcp:merkl-mcp /var/lib/merkl-mcp
USER merkl-mcp

ENV MERKL_MCP_STATE=/var/lib/merkl-mcp \
    MERKL_AGENT_DIR=/agent \
    PYTHONUNBUFFERED=1

VOLUME ["/var/lib/merkl-mcp"]
EXPOSE 8765

ENTRYPOINT ["merkl-mcp"]
