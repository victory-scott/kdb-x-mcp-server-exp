"""Static JWT verifier — Layer A of the spike.

Validates RS256-signed bearer tokens against a local public key. No IdP, no
network. Used to validate the SDK's TokenVerifier wire-up before pointing at
a real OIDC provider in Layer B.
"""

import time
from pathlib import Path

import jwt
from mcp.server.auth.provider import AccessToken, TokenVerifier


class StaticJWTVerifier(TokenVerifier):
    """RS256 JWT verifier backed by a static public key on disk."""

    def __init__(self, public_key_path: str, issuer: str, audience: str):
        self.public_key = Path(public_key_path).read_text()
        self.issuer = issuer
        self.audience = audience

    async def verify_token(self, token: str) -> AccessToken | None:
        try:
            claims = jwt.decode(
                token,
                self.public_key,
                algorithms=["RS256"],
                audience=self.audience,
                issuer=self.issuer,
            )
        except jwt.PyJWTError:
            return None

        scope_claim = claims.get("scope", "")
        scopes = scope_claim.split() if isinstance(scope_claim, str) else list(scope_claim)

        return AccessToken(
            token=token,
            client_id=claims.get("client_id") or claims.get("sub", ""),
            scopes=scopes,
            expires_at=claims.get("exp"),
            resource=self.audience,
        )
