"""Sign-in through Cloudflare Access, and roles.

Cloudflare Access sits in front of the site and signs every request it lets through with a JWT
(the Cf-Access-Jwt-Assertion header, or the CF_Authorization cookie). The app checks that token
against the team's public keys and the application's audience tag, so a request that skipped
Cloudflare (straight to the Render URL) has no identity and is refused.

Turned on by LEDGERHAWK_CF_TEAM_DOMAIN (e.g. drzerotrust.cloudflareaccess.com) and LEDGERHAWK_CF_AUD.
Without them the app runs as before: no sign-in, and analysts type their name.

Roles: admin, analyst, executive. LEDGERHAWK_ADMINS (comma-separated, "email" or "email=Name")
are always admins, so nobody can lock everyone out; everyone else gets a role from an admin.
"""
from __future__ import annotations

import os
from contextvars import ContextVar
from dataclasses import dataclass

import jwt

ROLES = {"admin": "Admin", "analyst": "Analyst", "executive": "Executive"}


@dataclass(frozen=True)
class User:
    email: str
    name: str
    role: str | None  # None: signed in, but no role yet
    bootstrap: bool = False


CURRENT_USER: ContextVar[User | None] = ContextVar("current_user", default=None)


def bootstrap_admins(raw: str | None = None) -> dict[str, str]:
    """{email: display name} from LEDGERHAWK_ADMINS."""
    out = {}
    for part in (os.environ.get("LEDGERHAWK_ADMINS", "") if raw is None else raw).split(","):
        email, _, name = part.strip().partition("=")
        if email.strip():
            out[email.strip().lower()] = name.strip()
    return out


def default_name(email: str) -> str:
    return email.split("@")[0].replace(".", " ").replace("_", " ")


class AccessVerifier:
    """Checks a Cloudflare Access JWT and returns the signed-in email."""

    def __init__(self, team_domain: str, aud: str):
        self.team = team_domain.strip().removeprefix("https://").rstrip("/")
        self.aud = aud.strip()
        self.issuer = f"https://{self.team}"
        self.jwks = jwt.PyJWKClient(f"{self.issuer}/cdn-cgi/access/certs", cache_keys=True, lifespan=3600)

    def email(self, token: str) -> str:
        key = self.jwks.get_signing_key_from_jwt(token).key
        claims = jwt.decode(token, key, algorithms=["RS256"], audience=self.aud, issuer=self.issuer,
                            options={"require": ["exp", "iat"]})
        email = str(claims.get("email") or "").strip().lower()
        if not email:
            raise jwt.InvalidTokenError("No email in the Access token (service tokens can't sign in here)")
        return email


def verifier_from_env() -> AccessVerifier | None:
    team, aud = os.environ.get("LEDGERHAWK_CF_TEAM_DOMAIN", ""), os.environ.get("LEDGERHAWK_CF_AUD", "")
    return AccessVerifier(team, aud) if team.strip() and aud.strip() else None


def token_from(headers, cookies) -> str:
    return headers.get("cf-access-jwt-assertion", "") or cookies.get("CF_Authorization", "")


def who(analyst: str) -> str:
    """The name to record for an action: the signed-in person's, whatever the form said; else the typed name."""
    u = CURRENT_USER.get()
    return u.name if u else analyst
