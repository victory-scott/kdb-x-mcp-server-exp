"""Mint a spike test token signed with the static private key.

Usage:
    python spike/mint_token.py [--scopes 'kdbx.read kdbx.write'] [--ttl 3600]
"""

import argparse
import time
from pathlib import Path

import jwt

ISSUER = "https://localhost:8000/mcp"
AUDIENCE = "https://localhost:8000/mcp"


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--scopes", default="kdbx.read")
    p.add_argument("--ttl", type=int, default=3600)
    p.add_argument("--sub", default="spike-user")
    p.add_argument("--client-id", default="spike-client")
    p.add_argument(
        "--key",
        default=str(Path(__file__).parent / "keys" / "spike_private.pem"),
    )
    args = p.parse_args()

    private_key = Path(args.key).read_text()
    now = int(time.time())
    claims = {
        "iss": ISSUER,
        "aud": AUDIENCE,
        "sub": args.sub,
        "client_id": args.client_id,
        "scope": args.scopes,
        "iat": now,
        "exp": now + args.ttl,
    }
    token = jwt.encode(claims, private_key, algorithm="RS256")
    print(token)


if __name__ == "__main__":
    main()
