"""Benchmark token verification latency for the spike.

Two measurements:
  1) verifier-only: time the StaticJWTVerifier.verify_token() call directly
     (no network, no MCP framework, just JWT signature math).
  2) end-to-end: time a tools/call round-trip through the running MCP server.

Run: ./.venv/bin/python spike/bench.py [--samples 100]
"""

import argparse
import asyncio
import json
import statistics
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import jwt
from static_jwt_verifier import StaticJWTVerifier
from jwks_verifier import JWKSVerifier


STATIC_ISSUER = "https://localhost:8000/mcp"
STATIC_AUDIENCE = "https://localhost:8000/mcp"
STATIC_PRIVATE_KEY = (Path(__file__).parent / "keys" / "spike_private.pem").read_text()
STATIC_PUBLIC_KEY_PATH = str(Path(__file__).parent / "keys" / "spike_public.pem")

NAVIKT_ISSUER = "http://localhost:8080/default"
NAVIKT_JWKS = "http://localhost:8080/default/jwks"
NAVIKT_TOKEN_URL = "http://localhost:8080/default/token"


def mint_static(scopes="kdbx.read", ttl=3600):
    now = int(time.time())
    return jwt.encode(
        {
            "iss": STATIC_ISSUER,
            "aud": STATIC_AUDIENCE,
            "sub": "spike-user",
            "client_id": "spike-client",
            "scope": scopes,
            "iat": now,
            "exp": now + ttl,
        },
        STATIC_PRIVATE_KEY,
        algorithm="RS256",
    )


def mint_navikt(scopes="kdbx.read"):
    import urllib.parse
    body = urllib.parse.urlencode(
        {
            "grant_type": "client_credentials",
            "client_id": "spike-client",
            "client_secret": "spike-secret",
            "scope": scopes,
        }
    ).encode()
    req = urllib.request.Request(
        NAVIKT_TOKEN_URL,
        data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=5) as resp:
        return json.loads(resp.read())["access_token"]


async def bench_verifier(samples: int, mode: str):
    if mode == "layer-a":
        verifier = StaticJWTVerifier(STATIC_PUBLIC_KEY_PATH, STATIC_ISSUER, STATIC_AUDIENCE)
        token = mint_static()
    else:  # layer-b
        verifier = JWKSVerifier(
            NAVIKT_JWKS, NAVIKT_ISSUER, audience=None, verify_audience=False
        )
        token = mint_navikt()
    # measure cold (first call, includes JWKS fetch for layer-b)
    t0 = time.perf_counter()
    await verifier.verify_token(token)
    cold_ms = (time.perf_counter() - t0) * 1000.0
    # warm
    for _ in range(10):
        await verifier.verify_token(token)
    times = []
    for _ in range(samples):
        t0 = time.perf_counter()
        await verifier.verify_token(token)
        times.append((time.perf_counter() - t0) * 1000.0)
    return cold_ms, times


def initialize_and_get_session(token):
    req = urllib.request.Request(
        "http://127.0.0.1:8000/mcp",
        data=json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-11-25",
                    "capabilities": {},
                    "clientInfo": {"name": "bench", "version": "0.1"},
                },
            }
        ).encode(),
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "Authorization": f"Bearer {token}",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=5) as resp:
        sid = resp.headers.get("mcp-session-id")
        # drain body
        resp.read()
    # send initialized notification
    req = urllib.request.Request(
        "http://127.0.0.1:8000/mcp",
        data=json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}).encode(),
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "Authorization": f"Bearer {token}",
            "mcp-session-id": sid,
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            resp.read()
    except urllib.error.HTTPError:
        pass
    return sid


def call_tool(token, sid, query="select count(*) from trades"):
    req = urllib.request.Request(
        "http://127.0.0.1:8000/mcp",
        data=json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {
                    "name": "kdbx_run_sql_query",
                    "arguments": {"query": query},
                },
            }
        ).encode(),
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "Authorization": f"Bearer {token}",
            "mcp-session-id": sid,
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=5) as resp:
        resp.read()


def bench_end_to_end(samples: int, mode: str):
    token = mint_static() if mode == "layer-a" else mint_navikt()
    sid = initialize_and_get_session(token)
    # warm
    for _ in range(5):
        call_tool(token, sid)
    times = []
    for _ in range(samples):
        t0 = time.perf_counter()
        call_tool(token, sid)
        times.append((time.perf_counter() - t0) * 1000.0)
    return times


def report(label, times):
    print(
        f"{label}: n={len(times)} "
        f"min={min(times):.3f}ms "
        f"p50={statistics.median(times):.3f}ms "
        f"mean={statistics.mean(times):.3f}ms "
        f"p95={sorted(times)[int(0.95*len(times))-1]:.3f}ms "
        f"max={max(times):.3f}ms"
    )


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--samples", type=int, default=100)
    p.add_argument("--mode", choices=["layer-a", "layer-b"], default="layer-a")
    args = p.parse_args()

    print(f"== bench [{args.mode}], samples={args.samples} ==")
    cold_ms, v_times = asyncio.run(bench_verifier(args.samples, args.mode))
    print(f"verifier cold (1st call): {cold_ms:.3f}ms")
    report("verifier warm", v_times)
    e_times = bench_end_to_end(args.samples, args.mode)
    report("end-to-end   ", e_times)


if __name__ == "__main__":
    main()
