"""Alerts: tell an Admin when something went wrong while nobody was looking. A job failed, the server restarted
without being asked to (nearly always out of memory), a nightly backup failed, or USAspending didn't answer for some
vendors. Every alert is kept on the server (the People page lists them) and, when set up, sent on:

  LEDGERHAWK_ALERT_WEBHOOK   a Slack, Teams, Google Chat or Discord incoming-webhook URL ({"text": ...} is posted)
  LEDGERHAWK_ALERT_EMAIL     who gets alert emails (comma-separated), sent through:
  LEDGERHAWK_SMTP_HOST, LEDGERHAWK_SMTP_PORT (default 587, STARTTLS), LEDGERHAWK_SMTP_USER,
  LEDGERHAWK_SMTP_PASSWORD, LEDGERHAWK_SMTP_FROM (default: the user)
"""
from __future__ import annotations

import json
import logging
import os
import smtplib
import threading
import time
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from pathlib import Path
from typing import Callable

LOG = "alerts.jsonl"
ALIVE = "alive.json"
KEEP = 200
QUIET = timedelta(hours=1)  # the same alert isn't sent again within this
BEAT = 60  # seconds between "still running" marks, so a restart can say roughly when the server stopped


def _now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class Channels:
    webhook: str = ""
    emails: list[str] = field(default_factory=list)
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from: str = ""

    def describe(self) -> list[str]:
        out = []
        if self.webhook:
            out.append("chat webhook")
        if self.emails and self.smtp_host:
            out.append("email to " + ", ".join(self.emails))
        return out


def channels_from_env() -> Channels:
    e = os.environ.get
    return Channels(webhook=e("LEDGERHAWK_ALERT_WEBHOOK", "").strip(),
                    emails=[x.strip() for x in e("LEDGERHAWK_ALERT_EMAIL", "").split(",") if x.strip()],
                    smtp_host=e("LEDGERHAWK_SMTP_HOST", "").strip(), smtp_port=int(e("LEDGERHAWK_SMTP_PORT") or 587),
                    smtp_user=e("LEDGERHAWK_SMTP_USER", "").strip(), smtp_password=e("LEDGERHAWK_SMTP_PASSWORD", ""),
                    smtp_from=e("LEDGERHAWK_SMTP_FROM", "").strip())


def _post_webhook(url: str, text: str) -> None:
    req = urllib.request.Request(url, data=json.dumps({"text": text, "content": text}).encode(),
                                 headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=15) as r:
        r.read()


def _send_email(ch: Channels, subject: str, body: str) -> None:
    msg = EmailMessage()
    msg["Subject"], msg["From"], msg["To"] = subject, ch.smtp_from or ch.smtp_user, ", ".join(ch.emails)
    msg.set_content(body)
    with smtplib.SMTP(ch.smtp_host, ch.smtp_port, timeout=20) as s:
        s.starttls()
        if ch.smtp_user:
            s.login(ch.smtp_user, ch.smtp_password)
        s.send_message(msg)


class Alerts:
    def __init__(self, root: str | Path, channels: Channels | None = None, site: str = "",
                 post: Callable[[str, str], None] | None = None, mail: Callable[[Channels, str, str], None] | None = None,
                 inline: bool = False):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.ch = channels or Channels()
        self.site = site
        self._post = post or _post_webhook
        self._mail = mail or _send_email
        self.inline = inline  # tests deliver in the call instead of a thread
        self._pid = os.getpid()
        self._lock = threading.Lock()

    # -- raising one ---------------------------------------------------------------------------------------------
    def raise_alert(self, kind: str, title: str, detail: str = "", key: str = "", wait: bool = False) -> dict | None:
        """Keep the alert and send it on. An alert with the same key within the last hour is kept as a repeat but
        not sent again."""
        key = key or f"{kind}:{title}"
        now = _now()
        with self._lock:
            recent = self._read()
            repeat = any(a.get("key") == key and a["at"] >= (now - QUIET).isoformat(timespec="seconds") for a in recent)
            a = {"at": now.isoformat(timespec="seconds"), "kind": kind, "title": title, "detail": detail, "key": key,
                 "sent": [], "errors": [], "repeat": repeat}
            self._write(recent + [a])
        if not repeat:
            # a job's own process ends as soon as its work does, so it sends before returning, as tests do
            if wait or self.inline or os.getpid() != self._pid:
                self._deliver(a)
            else:
                threading.Thread(target=self._deliver, args=(a,), daemon=True).start()
        return a

    def _deliver(self, a: dict) -> None:
        text = f"LedgerHawk: {a['title']}" + (f"\n{a['detail']}" if a["detail"] else "") + (f"\n{self.site}" if self.site else "")
        sent, errors = [], []
        if self.ch.webhook:
            try:
                self._post(self.ch.webhook, text)
                sent.append("webhook")
            except Exception as exc:
                errors.append(f"webhook: {type(exc).__name__}: {exc}"[:200])
        if self.ch.emails and self.ch.smtp_host:
            try:
                self._mail(self.ch, f"LedgerHawk: {a['title']}", text)
                sent.append("email")
            except Exception as exc:
                errors.append(f"email: {type(exc).__name__}: {exc}"[:200])
        if errors:
            logging.getLogger("ledgerhawk").warning("alert not delivered: %s", "; ".join(errors))
        with self._lock:
            rows = self._read()
            for r in rows:
                if r["at"] == a["at"] and r["key"] == a["key"]:
                    r["sent"], r["errors"] = sent, errors
            self._write(rows)
        a["sent"], a["errors"] = sent, errors

    # -- the record ----------------------------------------------------------------------------------------------
    def _read(self) -> list[dict]:
        f = self.root / LOG
        if not f.exists():
            return []
        out = []
        for line in f.read_text().splitlines():
            try:
                out.append(json.loads(line))
            except ValueError:
                continue
        return out

    def _write(self, rows: list[dict]) -> None:
        f = self.root / LOG
        tmp = f.with_suffix(".tmp")
        tmp.write_text("".join(json.dumps(r) + "\n" for r in rows[-KEEP:]))
        tmp.replace(f)

    def recent(self, limit: int = 50) -> list[dict]:
        with self._lock:
            return list(reversed(self._read()))[:limit]

    # -- restarts --------------------------------------------------------------------------------------------------
    def started(self, boot: str) -> None:
        """Called once at startup. If the last run didn't shut down cleanly (a deploy or restart from Render shuts
        down cleanly; running out of memory doesn't), say so."""
        f = self.root / ALIVE
        try:
            last = json.loads(f.read_text())
        except (OSError, ValueError):
            last = None
        if last and not last.get("clean"):
            self.raise_alert("restart", "LedgerHawk restarted unexpectedly",
                             f"The server stopped without shutting down (last seen running {last.get('beat', '?')} UTC), "
                             "nearly always because it ran out of memory. Anything that was running then has to be "
                             "started again; the Imports page shows which.", key=f"restart:{boot}")
        self._mark(boot, clean=False)

    def _mark(self, boot: str, clean: bool) -> None:
        f = self.root / ALIVE
        tmp = f.with_suffix(".tmp")
        tmp.write_text(json.dumps({"boot": boot, "beat": _now().isoformat(timespec="seconds"), "clean": clean}))
        tmp.replace(f)

    def heartbeat(self, boot: str, every: float = BEAT) -> None:
        def loop():
            while True:
                time.sleep(every)
                try:
                    self._mark(boot, clean=False)
                except OSError:
                    pass
        threading.Thread(target=loop, daemon=True, name="ledgerhawk-heartbeat").start()

    def stopped(self, boot: str) -> None:
        """Called on a clean shutdown."""
        try:
            self._mark(boot, clean=True)
        except OSError:
            pass
