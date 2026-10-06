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
    assert "date=10%2F2026" in samgov.entity_url(2026, 10, "k") and "fileType=ENTITY" in samgov.entity_url(2026, 10, "k")
    assert samgov.entity_months(date(2026, 1, 9)) == [(2026, 1), (2025, 12)]
    assert samgov.entity_date("SAM_PUBLIC_MONTHLY_V2_20261005.ZIP", 2026, 10) == date(2026, 10, 5)
    assert samgov.entity_date(None, 2026, 10) == date(2026, 10, 4)


def test_zipped_extracts_load(tmp_path):
    _, excl, sam, _ = make_synthetic(tmp_path, n=300, seed=2)
    zs, ze = tmp_path / "sam.zip", tmp_path / "ex.zip"
    with zipfile.ZipFile(zs, "w") as z:
        z.write(sam, "SAM_PUBLIC_MONTHLY_V2_20260906.dat")
    with zipfile.ZipFile(ze, "w") as z:
        z.write(excl, "SAM_Exclusions_Public_Extract_V2_26275.CSV")
    assert load_sam(zs, date(2026, 9, 6)).records == load_sam(sam, date(2026, 9, 6)).records > 0
    assert len(load_exclusions(ze, date(2026, 10, 2)).records) == len(load_exclusions(excl, date(2026, 10, 2)).records)


def test_extract_read_whatever_the_record_breaks(tmp_path):
    """SAM.gov's file may put every record on one line ("!end" only), use CRLF, or start with a BOM."""
    _, _, sam, _ = make_synthetic(tmp_path, n=300, seed=5)
    want = load_sam(sam, date(2026, 9, 6)).records
    assert want > 250
    text = sam.read_text()
    for name, body in {"oneline": text.replace("!end\n", "!end"), "crlf": text.replace("\n", "\r\n"),
                       "bom": "\ufeff" + text}.items():
        p = tmp_path / name / "SAM_PUBLIC_MONTHLY_V2_20261005.dat"
        p.parent.mkdir()
        p.write_bytes(body.encode("utf-8"))
        z = p.with_suffix(".zip")
        with zipfile.ZipFile(z, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.write(p, p.name)
        assert load_sam(p, date(2026, 10, 5)).records == want, name
        assert load_sam(z, date(2026, 10, 5)).records == want, name


def test_unreadable_extract_is_an_error_not_an_empty_screen(tmp_path):
    p = tmp_path / "SAM_PUBLIC_MONTHLY_V2_20261005.dat"
    p.write_text("BOF PUBLIC V2 00000000 20261005 0000000 0000000\nEOF PUBLIC V2\n")
    try:
        load_sam(p, date(2026, 10, 5))
        raise AssertionError("expected an error")
    except ValueError as exc:
        assert "No SAM entities" in str(exc)


def test_refresh_fetches_newest_once_and_reports_errors(tmp_path):
    _, excl, sam, _ = make_synthetic(tmp_path / "syn", n=300, seed=3)
    calls = []

    def fake(url, dest: Path):
        calls.append(url)
        if "fileType=ENTITY" in url:
            if "date=10%2F2026" not in url and "date=09%2F2026" not in url:
                raise FileNotFoundError(dest.name)
            sent = "SAM_PUBLIC_MONTHLY_V2_20261004.ZIP" if "date=10" in url else "SAM_PUBLIC_MONTHLY_V2_20260906.ZIP"
            with zipfile.ZipFile(dest, "w") as z:
                z.write(sam, sent.replace(".ZIP", ".dat"))
            return sent
        name = url.split("fileName=")[-1]
        if name.endswith("26278.ZIP"):  # today's exclusions aren't out yet
            raise FileNotFoundError(name)
        with zipfile.ZipFile(dest, "w") as z:
            z.write(excl, name.replace(".ZIP", ".CSV"))
        return name

    st = Store(tmp_path / "app")
    out = st.refresh_sam_gov("k", today=date(2026, 10, 5), download=fake)
    assert out["exclusions"]["as_of"] == "2026-10-04" and out["sam"]["as_of"] == "2026-10-04"
    assert not out["exclusions"]["error"] and not out["running"]
    srcs = {m["kind"]: m for m in st.list_sources()}
    assert srcs["sam"]["uploaded_by"] == Store.AUTO_BY and srcs["sam"]["file"] == "SAM_PUBLIC_MONTHLY_V2_20261004.ZIP"
    assert list(Path(srcs["sam"]["path"]).parent.glob("*.sqlite"))  # lookup tables built at download time
    assert srcs["sam"]["entities"] > 250  # shown in Data sources so a misread file is obvious
    n = len(calls)
    assert len(calls) == 3 and all("api_key=k" in c for c in calls)
    st.refresh_sam_gov("k", today=date(2026, 10, 5), download=fake)
    assert len(calls) == n + 1  # only today's exclusions are retried; nothing new is downloaded
    assert out["sam"]["as_of"] == "2026-10-04"

    def denied(url, dest):
        raise RuntimeError("SAM.gov rejected the API key.")
    out = st.refresh_sam_gov("bad", today=date(2026, 10, 7), download=denied)
    assert out["exclusions"]["error"] == "SAM.gov rejected the API key." and out["exclusions"]["as_of"] == "2026-10-04"

    for day in range(6, 25):  # old automatic downloads are pruned
        st.refresh_sam_gov("k", today=date(2026, 10, day), download=fake)
    kinds = [m["kind"] for m in st.list_sources()]
    assert kinds.count("exclusions") == Store.AUTO_KEEP["exclusions"]


def test_demo_seed_does_not_block_real_extract(tmp_path):
    """A synthetic seed dated like a real extract must not stop the download; a month not out yet falls back."""
    _, excl, sam, _ = make_synthetic(tmp_path / "syn", n=200, seed=4)
    st = Store(tmp_path / "app")
    st.add_source("sam", sam, date(2026, 9, 6), "system")

    def fake(url, dest: Path):
        if "fileType=ENTITY" not in url:
            raise FileNotFoundError(dest.name)
        if "date=10%2F2026" in url:  # October's extract not published yet
            raise FileNotFoundError(dest.name)
        with zipfile.ZipFile(dest, "w") as z:
            z.write(sam, "SAM_PUBLIC_MONTHLY_V2_20260906.dat")
        return None  # no file name sent: the date falls back to the first Sunday

    out = st.refresh_sam_gov("k", today=date(2026, 10, 6), download=fake)
    assert out["sam"]["as_of"] == "2026-09-06" and not out["sam"]["error"]
    assert [m["uploaded_by"] for m in st.list_sources() if m["kind"] == "sam"].count(Store.AUTO_BY) == 1
    assert "no exclusions extract" in out["exclusions"]["error"]


def test_restart_clears_partial_downloads(tmp_path):
    Store(tmp_path)
    part = tmp_path / "tmpab12cd" / "SAM_PUBLIC_MONTHLY_V2_20261004.zip.part"
    part.parent.mkdir()
    part.write_bytes(b"x" * 10)
    Store(tmp_path)
    assert not part.parent.exists() and (tmp_path / "runs").exists()
