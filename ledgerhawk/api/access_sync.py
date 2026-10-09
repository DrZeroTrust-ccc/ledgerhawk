"""Keep Cloudflare Access in step with the People page: adding someone in LedgerHawk also lets their email through
Cloudflare's sign-in, and removing them takes it out. Optional; without these settings an Admin adds the email to
the Access policy by hand.

  LEDGERHAWK_CF_API_TOKEN      a Cloudflare API token with "Access: Apps and Policies: Edit" on the account
  LEDGERHAWK_CF_ACCOUNT_ID     the Cloudflare account id
  LEDGERHAWK_CF_POLICY_ID      the Access policy that lists who may sign in ("LedgerHawk users")
  LEDGERHAWK_CF_APP_ID         the Access application id, only if that policy belongs to one application
"""
from __future__ import annotations

import json
import os
import urllib.request
from typing import Callable

API = "https://api.cloudflare.com/client/v4"
# fields Cloudflare returns but won't take back on an update
READ_ONLY = {"id", "uid", "created_at", "updated_at", "app_count", "reusable", "app_id"}


def _http(method: str, url: str, token: str, body: dict | None = None) -> dict:
    req = urllib.request.Request(url, method=method, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            out = json.loads(r.read())
    except urllib.error.HTTPError as exc:
        try:
            out = json.loads(exc.read())
        except ValueError:
            raise RuntimeError(f"Cloudflare answered {exc.code}") from None
    if not out.get("success"):
        raise RuntimeError("Cloudflare: " + "; ".join(e.get("message", "") for e in out.get("errors") or []) or "request failed")
    return out["result"]


class AccessSync:
    def __init__(self, token: str = "", account: str = "", policy: str = "", app: str = "",
                 http: Callable[[str, str, str, dict | None], dict] | None = None):
        e = os.environ.get
        self.token = token or e("LEDGERHAWK_CF_API_TOKEN", "").strip()
        self.account = account or e("LEDGERHAWK_CF_ACCOUNT_ID", "").strip()
        self.policy = policy or e("LEDGERHAWK_CF_POLICY_ID", "").strip()
        self.app = app or e("LEDGERHAWK_CF_APP_ID", "").strip()
        self._http = http or _http

    @property
    def configured(self) -> bool:
        return bool(self.token and self.account and self.policy)

    def _url(self) -> str:
        base = f"{API}/accounts/{self.account}/access"
        return f"{base}/apps/{self.app}/policies/{self.policy}" if self.app else f"{base}/policies/{self.policy}"

    def emails(self) -> list[str]:
        p = self._http("GET", self._url(), self.token, None)
        return [r["email"]["email"].lower() for r in p.get("include") or [] if "email" in r]

    def _update(self, change: Callable[[list[dict]], list[dict]]) -> None:
        p = self._http("GET", self._url(), self.token, None)
        body = {k: v for k, v in p.items() if k not in READ_ONLY}
        body["include"] = change(list(p.get("include") or []))
        self._http("PUT", self._url(), self.token, body)

    def allow(self, email: str) -> bool:
        """Let this email sign in. False if it was already allowed."""
        email = email.strip().lower()
        if email in self.emails():
            return False
        self._update(lambda inc: inc + [{"email": {"email": email}}])
        return True

    def revoke(self, email: str) -> bool:
        """Stop this email signing in. False if it wasn't listed by itself (it may be let in by a domain rule)."""
        email = email.strip().lower()
        if email not in self.emails():
            return False
        self._update(lambda inc: [r for r in inc if (r.get("email") or {}).get("email", "").lower() != email])
        return True
