"""Automatic SAM.gov extract downloads, with SAM.gov faked."""
import zipfile
from datetime import date
from pathlib import Path

from ledgerhawk.api.store import Store
from ledgerhawk.pipeline import samgov
from ledgerhawk.pipeline.exclusions import load_exclusions
from ledgerhawk.pipeline.sam import load_sam
from ledgerhawk.pipeline.synthetic import make_synthetic


def test_extract_dates_and_names():
    assert samgov.first_sunday(2026, 10) == date(2026, 10, 4)
    assert samgov.entity_dates(date(2026, 10, 3)) == [date(2026, 9, 6), date(2026, 8, 2)]
    assert samgov.entity_dates(date(2026, 10, 4))[0] == date(2026, 10, 4)
    assert "SAM_Exclusions_Public_Extract_V2_26278.ZIP" in samgov.exclusions_url(date(2026, 10, 5), "k")
    assert "SAM_PUBLIC_MONTHLY_V2_20261004.ZIP" in samgov.entity_url(date(2026, 10, 4), "k")


def test_zipped_extracts_load(tmp_path):
    _, excl, sam, _ = make_synthetic(tmp_path, n=300, seed=2)
    zs, ze = tmp_path / "sam.zip", tmp_path / "ex.zip"
    with zipfile.ZipFile(zs, "w") as z:
        z.write(sam, "SAM_PUBLIC_MONTHLY_V2_20260906.dat")
    with zipfile.ZipFile(ze, "w") as z:
        z.write(excl, "SAM_Exclusions_Public_Extract_V2_26275.CSV")
    assert load_sam(zs, date(2026, 9, 6)).records == load_sam(sam, date(2026, 9, 6)).records > 0
    assert len(load_exclusions(ze, date(2026, 10, 2)).records) == len(load_exclusions(excl, date(2026, 10, 2)).records)


def test_refresh_fetches_newest_once_and_reports_errors(tmp_path):
    _, excl, sam, _ = make_synthetic(tmp_path / "syn", n=300, seed=3)
    calls = []

    def fake(url, dest: Path):
        calls.append(url)
        name = url.split("fileName=")[-1]
        if name.endswith("26278.ZIP"):  # today's exclusions aren't out yet
            raise FileNotFoundError(name)
        with zipfile.ZipFile(dest, "w") as z:
            z.write(sam if "MONTHLY" in name else excl, name.replace(".ZIP", ".dat" if "MONTHLY" in name else ".CSV"))

    st = Store(tmp_path / "app")
    out = st.refresh_sam_gov("k", today=date(2026, 10, 5), download=fake)
    assert out["exclusions"]["as_of"] == "2026-10-04" and out["sam"]["as_of"] == "2026-10-04"
    assert not out["exclusions"]["error"] and not out["running"]
    srcs = {m["kind"]: m for m in st.list_sources()}
    assert srcs["sam"]["uploaded_by"] == Store.AUTO_BY and srcs["sam"]["file"].endswith(".zip")
    assert list(Path(srcs["sam"]["path"]).parent.glob("*.sqlite"))  # lookup tables built at download time
    n = len(calls)
    assert len(calls) == 3 and all("api_key=k" in c for c in calls)
    st.refresh_sam_gov("k", today=date(2026, 10, 5), download=fake)
    assert len(calls) == n + 1  # only today's exclusions are retried; nothing new is downloaded

    def denied(url, dest):
        raise RuntimeError("SAM.gov rejected the API key.")
    out = st.refresh_sam_gov("bad", today=date(2026, 10, 7), download=denied)
    assert out["exclusions"]["error"] == "SAM.gov rejected the API key." and out["exclusions"]["as_of"] == "2026-10-04"

    for day in range(6, 25):  # old automatic downloads are pruned
        st.refresh_sam_gov("k", today=date(2026, 10, day), download=fake)
    kinds = [m["kind"] for m in st.list_sources()]
    assert kinds.count("exclusions") == Store.AUTO_KEEP["exclusions"]


def test_restart_clears_partial_downloads(tmp_path):
    Store(tmp_path)
    part = tmp_path / "tmpab12cd" / "SAM_PUBLIC_MONTHLY_V2_20261004.zip.part"
    part.parent.mkdir()
    part.write_bytes(b"x" * 10)
    Store(tmp_path)
    assert not part.parent.exists() and (tmp_path / "runs").exists()
