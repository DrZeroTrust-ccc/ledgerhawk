"""The weekly re-screen: every week, the newest real import under each policy pack is followed up against the newest
SAM and exclusions extracts, its excluded vendors are looked up in USAspending, and a short "new since last week"
note goes out as an alert. Older automatic follow-ups are removed so the disk doesn't fill.

  LEDGERHAWK_WEEKLY          "0" turns it off (on by default)
  LEDGERHAWK_WEEKLY_DAY      0 = Monday (default) ... 6 = Sunday
  LEDGERHAWK_WEEKLY_HOUR     the hour (UTC) it starts after (default 11, 7am Eastern)
  LEDGERHAWK_WEEKLY_KEEP     how many automatic follow-ups to keep (default 4)
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

BY = "LedgerHawk (weekly re-screen)"
STATUS = "weekly.json"
DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


def _week(d: datetime) -> str:
    y, w, _ = d.isocalendar()
    return f"{y}-W{w:02d}"


class Weekly:
    def __init__(self, store, digest: Callable[[str], None] | None = None, refresh: Callable[[], None] | None = None,
                 enabled: bool | None = None, day: int | None = None, hour: int | None = None, keep: int | None = None):
        e = os.environ.get
        self.store = store
        self.digest = digest  # digest(run_id): tell people what's new; called once the re-screen finishes
        self.refresh = refresh  # fetch the newest SAM.gov extracts first, when SAM.gov downloads are set up
        self.enabled = (e("LEDGERHAWK_WEEKLY", "1") != "0") if enabled is None else enabled
        self.day = int(e("LEDGERHAWK_WEEKLY_DAY") or 0) if day is None else day
        self.hour = int(e("LEDGERHAWK_WEEKLY_HOUR") or 11) if hour is None else hour
        self.keep = max(1, int(e("LEDGERHAWK_WEEKLY_KEEP") or 4)) if keep is None else keep

    # -- status --------------------------------------------------------------------------------------------------
    def _path(self) -> Path:
        return Path(self.store.root) / STATUS

    def _read(self) -> dict:
        try:
            return json.loads(self._path().read_text())
        except (OSError, ValueError):
            return {}

    def _write(self, **kw) -> None:
        self._path().write_text(json.dumps(self._read() | kw, indent=1))

    def slot(self, now: datetime) -> datetime:
        """This week's start time."""
        monday = (now - timedelta(days=now.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
        return monday + timedelta(days=self.day, hours=self.hour)

    def status(self, now: datetime | None = None) -> dict:
        now = now or datetime.now(timezone.utc)
        s = self._read()
        nxt = self.slot(now)
        if now >= nxt and s.get("last_week") == _week(now):
            nxt += timedelta(days=7)
        return {"enabled": self.enabled, "when": f"{DAYS[self.day]}s after {self.hour:02d}:00 UTC", "keep": self.keep,
                "next": nxt.isoformat(timespec="minutes") if self.enabled else None,
                "targets": [self.store.run_ref(r) for r in self.store.weekly_targets()],
                "last": s.get("last")}

    # -- running ---------------------------------------------------------------------------------------------------
    def due(self, now: datetime | None = None) -> bool:
        now = now or datetime.now(timezone.utc)
        return (self.enabled and now >= self.slot(now) and self._read().get("last_week") != _week(now)
                and not self.store.heavy_job_running() and bool(self.store.weekly_targets()))

    def run(self, now: datetime | None = None) -> list[dict]:
        """Start this week's re-screens (they queue behind each other and any import already running)."""
        now = now or datetime.now(timezone.utc)
        self._write(last_week=_week(now))
        jobs = []
        for i, target in enumerate(self.store.weekly_targets()):
            label = self.store.run_ref(target)["label"]
            refresh = self.refresh if i == 0 else None

            def fn(progress, target=target, refresh=refresh):
                if refresh:
                    progress("Checking SAM.gov for the newest extracts")
                    refresh()
                rid = self.store.follow_up_run(target, BY, progress=progress, auto=True)
                progress("Looking up excluded vendors in USAspending")
                self.store.check_excluded(rid, BY, progress)
                return rid

            jobs.append(self.store.start_import("follow_up", f"Weekly re-screen: {label}", BY, fn,
                                                on_done=lambda rid, target=target: self._finished(target, rid)))
        return jobs

    def _finished(self, target: str, rid: str) -> None:
        removed = self.store.prune_auto_runs(self.keep)
        self._write(last={"at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "followed": target,
                          "run_id": rid, "removed": removed})
        if self.digest:
            self.digest(rid)

    def start_loop(self, every: float = 900) -> None:
        def loop():
            while True:
                time.sleep(every)
                try:
                    if self.due():
                        self.run()
                except Exception:
                    logging.getLogger("ledgerhawk").exception("weekly re-screen")
        threading.Thread(target=loop, daemon=True, name="ledgerhawk-weekly").start()
