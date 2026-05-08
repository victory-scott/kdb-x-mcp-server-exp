"""JWKS-based JWT verifier — Layer B of the spike.

Fetches public keys from a remote JWKS endpoint and validates RS256-signed
bearer tokens against them. Demonstrates that the SDK's TokenVerifier
abstraction is IdP-agnostic — same wire-up, different verifier, real
network round-trip on first call (and on key rotation).

Used against Navikt's mock-oauth2-server in the spike, but works against
any OIDC-compliant issuer (Keycloak, Auth0, etc.).
"""

import jwt
from jwt import PyJWKClient
from mcp.server.auth.provider import AccessToken, TokenVerifier


class JWKSVerifier(TokenVerifier):
    """RS256 JWT verifier that fetches keys via JWKS."""

    def __init__(
        self,
        jwks_uri: str,
        issuer: str,
        audience: str | None = None,
        verify_audience: bool = True,
    ):
        self.jwks_client = PyJWKClient(jwks_uri, cache_keys=True)
        self.issuer = issuer
        self.audience = audience
        self.verify_audience = verify_audience

    async def verify_token(self, token: str) -> AccessToken | None:
        try:
            signing_key = self.jwks_client.get_signing_key_from_jwt(token)
            options = {"verify_aud": self.verify_audience and self.audience is not None}
            kwargs = {
                "algorithms": ["RS256"],
                "issuer": self.issuer,
                "options": options,
            }
            if self.verify_audience and self.audience is not None:
                kwargs["audience"] = self.audience
            claims = jwt.decode(token, signing_key.key, **kwargs)
        except jwt.PyJWTError:
            return None

        scope_claim = claims.get("scope", "")
        if isinstance(scope_claim, str):
            scopes = scope_claim.split() if scope_claim else []
        else:
            scopes = list(scope_claim)
        # Navikt's mock-oauth2-server puts requested scope into `aud` for
        # client-credentials flows; treat aud as a scope hint when scope is
        # empty so the spike's required_scopes check has something to match.
        if not scopes and isinstance(claims.get("aud"), str):
            scopes = [claims["aud"]]

        return AccessToken(
            token=token,
            client_id=claims.get("client_id") or claims.get("sub", ""),
            scopes=scopes,
            expires_at=claims.get("exp"),
            resource=claims.get("aud") if isinstance(claims.get("aud"), str) else self.audience,
        )
