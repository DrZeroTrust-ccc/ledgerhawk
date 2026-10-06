"""Download the SAM.gov extracts LedgerHawk screens against, so nobody has to fetch them by hand.

Uses the SAM.gov Extracts API (https://open.gsa.gov/api/sam-entity-extracts-api/) with a key from `SAM_API_KEY`.
The public entity extract (V2) is published monthly, on the first Sunday; the exclusions extract daily. A
non-federal key without a SAM role allows 10 calls a day, so each check makes as few calls as it can.
"""
from __future__ import annotations

import os
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, timedelta
from pathlib import Path
from typing import Callable

API = "https://api.sam.gov/data-services/v1/extracts"

# (url, dest) -> None; raises FileNotFoundError when SAM.gov has no file for that date, RuntimeError otherwise.
Download = Callable[[str, Path], None]


def api_key() -> str:
    return os.environ.get("SAM_API_KEY", "").strip()


def first_sunday(year: int, month: int) -> date:
    d = date(year, month, 1)
    return d + timedelta(days=(6 - d.weekday()) % 7)


def entity_dates(today: date, months: int = 2) -> list[date]:
    """The newest monthly public extract dates on or before today, newest first."""
    out = []
    y, m = today.year, today.month
    while len(out) < months:
        d = first_sunday(y, m)
        if d <= today:
            out.append(d)
        y, m = (y, m - 1) if m > 1 else (y - 1, 12)
    return out


def entity_url(d: date, key: str) -> str:
    return f"{API}?" + urllib.parse.urlencode({"api_key": key, "fileName": f"SAM_PUBLIC_MONTHLY_V2_{d:%Y%m%d}.ZIP"})


def exclusions_url(d: date, key: str) -> str:
    return f"{API}?" + urllib.parse.urlencode({"api_key": key, "fileName": f"SAM_Exclusions_Public_Extract_V2_{d:%y}{d.timetuple().tm_yday:03d}.ZIP"})


def http_download(url: str, dest: Path) -> None:
    req = urllib.request.Request(url, headers={"User-Agent": "LedgerHawk/1.0"})
    tmp = dest.with_name(dest.name + ".part")
    try:
        with urllib.request.urlopen(req, timeout=120) as resp, open(tmp, "wb") as f:
            while chunk := resp.read(1 << 22):
                f.write(chunk)
    except urllib.error.HTTPError as exc:
        tmp.unlink(missing_ok=True)
        if exc.code in (400, 404):
            raise FileNotFoundError(url.split("fileName=")[-1]) from None
        if exc.code in (401, 403):
            raise RuntimeError("SAM.gov rejected the API key. Generate a new one in your SAM.gov account and update "
                               "SAM_API_KEY in Render.") from None
        if exc.code == 429:
            raise RuntimeError("SAM.gov's daily limit for this API key is used up. It resets tomorrow.") from None
        raise RuntimeError(f"SAM.gov answered {exc.code}.") from None
    except (urllib.error.URLError, TimeoutError) as exc:
        tmp.unlink(missing_ok=True)
        raise RuntimeError(f"Could not reach SAM.gov ({getattr(exc, 'reason', exc)}).") from None
    tmp.replace(dest)
