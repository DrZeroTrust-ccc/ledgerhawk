"""Program integrity screens: a state's sponsor, site, retailer and vendor files, screened and kept on disk.

Each screen lives in <data>/programs/<id>/ with its input files, meta.json and result.json. Screens never mix with
award runs or subject screens; a simulated set is labeled data_class "simulated" everywhere it shows.
"""
from __future__ import annotations

import json
import uuid
from datetime import date, datetime, timezone
from pathlib import Path

from ..pipeline import program as program_mod
from ..pipeline import program_demo
from ..pipeline.exclusions import load_exclusions

DATA_CLASSES = {"simulated": "Simulated data", "public": "Public records", "client": "Agency data"}


class Programs:
    def __init__(self, root: Path, sources=None):
        self.root = Path(root) / "programs"
        self.sources = sources  # callable returning the store's data sources, newest first

    def _dir(self, pid: str) -> Path:
        d = self.root / pid
        if not pid or "/" in pid or ".." in pid or not d.is_dir():
            raise KeyError(pid)
        return d

    def _exclusions(self):
        for src in (self.sources() if self.sources else []):
            if src.get("kind") == "exclusions":
                try:
                    return load_exclusions(src["path"], date.fromisoformat(src["as_of"]))
                except (OSError, ValueError, KeyError):
                    return None
        return None

    def create(self, label: str, data_class: str, files: dict[str, str], analyst: str, note: str = "") -> str:
        if data_class not in DATA_CLASSES:
            raise ValueError("Pick what kind of data this is: simulated, public records or agency data.")
        parsed = {k: program_mod.read_csv(v) for k, v in files.items() if v and v.strip()}
        result = program_mod.screen(parsed, self._exclusions())  # raises ValueError on an empty set
        pid = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:8]
        d = self.root / pid
        (d / "inputs").mkdir(parents=True)
        for k, v in files.items():
            if v and v.strip():
                (d / "inputs" / f"{k}.csv").write_text(v)
        meta = {"id": pid, "label": label.strip() or "Program screen", "data_class": data_class,
                "data_class_label": DATA_CLASSES[data_class], "created": datetime.now(timezone.utc).isoformat(),
                "analyst": analyst, "note": note, "counts": result["counts"], "flagged": result["flagged"],
                "meals": result["meals"], "files": sorted(parsed)}
        (d / "result.json").write_text(json.dumps(result))
        (d / "meta.json").write_text(json.dumps(meta, indent=2))
        return pid

    def create_demo(self, analyst: str) -> str:
        return self.create("State of Northland, child nutrition and SNAP/WIC (simulated)", "simulated",
                           program_demo.generate(), analyst,
                           note="Fictional state, people and businesses. Planted patterns follow public fraud cases.")

    def list(self) -> list[dict]:
        if not self.root.is_dir():
            return []
        out = []
        for d in self.root.iterdir():
            try:
                out.append(json.loads((d / "meta.json").read_text()))
            except (OSError, ValueError):
                continue
        return sorted(out, key=lambda m: m["created"], reverse=True)

    def get(self, pid: str) -> dict:
        d = self._dir(pid)
        meta = json.loads((d / "meta.json").read_text())
        res = json.loads((d / "result.json").read_text())
        leads = [{k: v for k, v in l.items() if k not in ("graph", "hits")} |
                 {"headline": l["hits"][0]["text"] if l["hits"] else ""} for l in res["leads"]]
        return {"meta": meta, "counts": res["counts"], "meals": res["meals"], "by_signal": res["by_signal"],
                "signal_labels": res["signal_labels"], "checks": res["checks"], "outside": res.get("outside"),
                "leads": leads}

    def lead(self, pid: str, rank: int) -> dict:
        res = json.loads((self._dir(pid) / "result.json").read_text())
        for l in res["leads"]:
            if l["rank"] == rank:
                return l
        raise KeyError(rank)

    def leads_csv(self, pid: str) -> str:
        import csv
        import io
        res = json.loads((self._dir(pid) / "result.json").read_text())
        meta = json.loads((self._dir(pid) / "meta.json").read_text())
        buf = io.StringIO()
        w = csv.writer(buf)
        if meta["data_class"] == "simulated":
            w.writerow(["SIMULATED DATA: fictional entities for demonstration only"])
        w.writerow(["rank", "score", "kind", "id", "name", "address", "status", "sites", "meals_claimed", "signals",
                    "details"])
        for l in res["leads"]:
            w.writerow([l["rank"], l["score"], l["kind_label"], l["id"], l["name"], l["address"], l["status"],
                        l["sites"], l["meals"], "; ".join(res["signal_labels"][c] for c in l["signals"]),
                        " | ".join(h["text"] for h in l["hits"])])
        return buf.getvalue()
