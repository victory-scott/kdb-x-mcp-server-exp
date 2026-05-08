# Spike: SDK bump + auth wire-up

This `spike/` directory is the only addition to the kdb-x MCP server
that is *not* upstreamable as-is. The src changes (auth wire-up in
`server.py`, `get_access_token()` in the SQL tool, dep bump in
`pyproject.toml` / `uv.lock`) are the actual deliverable.

## Reproducing the spike

```sh
# Generate the Layer A keypair (gitignored; regenerate locally each time)
mkdir -p spike/keys
openssl genrsa -out spike/keys/spike_private.pem 2048
openssl rsa -in spike/keys/spike_private.pem -pubout -out spike/keys/spike_public.pem

# Start q with the SQL module loaded
QHOME=$HOME/.kx QLIC=$HOME/.kx ~/.kx/bin/q spike/q_startup.q -p 5001 -q &

# Layer A: static JWT (no IdP)
SPIKE_AUTH=static \
  KDBX_DB_PORT=5001 PYKX_LICENSED=true QHOME=$HOME/.kx QLIC=$HOME/.kx \
  ./.venv/bin/python spike/run_server.py

# Layer B: Navikt mock-oauth2-server via Docker
docker run --rm -d -p 8080:8080 --name spike-mock-oauth ghcr.io/navikt/mock-oauth2-server:2.1.10
SPIKE_AUTH=jwks \
  SPIKE_ISSUER=http://localhost:8080/default \
  SPIKE_AUDIENCE=https://localhost:8000/mcp \
  SPIKE_JWKS_URI=http://localhost:8080/default/jwks \
  SPIKE_REQUIRED_SCOPES=kdbx.read \
  KDBX_DB_PORT=5001 PYKX_LICENSED=true QHOME=$HOME/.kx QLIC=$HOME/.kx \
  ./.venv/bin/python spike/run_server.py

# Mint a Layer A token
./.venv/bin/python spike/mint_token.py

# Mint a Layer B token (from Navikt)
curl -s -X POST http://localhost:8080/default/token \
  -H 'Content-Type: application/x-www-form-urlencoded' \
  -d 'grant_type=client_credentials&client_id=spike-client&client_secret=spike-secret&scope=kdbx.read' \
  | python3 -c 'import json,sys; print(json.load(sys.stdin)["access_token"])'

# Benchmarks
./.venv/bin/python spike/bench.py --mode layer-a --samples 100
./.venv/bin/python spike/bench.py --mode layer-b --samples 100
```

## Files

- `q_startup.q` — q startup script: loads `kx.sql` module + tiny test data
- `run_server.py` — launcher that adds `src/` to sys.path
- `static_jwt_verifier.py` — Layer A: `TokenVerifier` against a local public key
- `jwks_verifier.py` — Layer B: `TokenVerifier` against a remote JWKS endpoint
- `mint_token.py` — sign a Layer A test token with the local private key
- `bench.py` — verifier-only and end-to-end latency benchmarks
- `keys/` — RSA keypair for Layer A (NOT for production)

## What lands upstream eventually

Just the changes in `src/` and the dep bump. The `spike/` directory is
test scaffolding; in round-2 the auth wire-up moves to a proper
`mcp_server/auth/` package and the keys/q-startup live in the
deployment doc, not the repo.
