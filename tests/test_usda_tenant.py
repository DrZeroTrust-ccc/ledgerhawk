"""USDA demo tenant tooling (tenants/usda): builds a pipeline-ready recipient file without touching app data."""
import importlib.util
from pathlib import Path

from ledgerhawk.pipeline.ingest import load_vendor_file

_spec = importlib.util.spec_from_file_location(
    "usda_build", Path(__file__).resolve().parents[1] / "tenants" / "usda" / "build_spend_file.py")
usda = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(usda)


def fake_post(url, body):
    if "/sub_agency/" in url:
        return {"results": [{"name": "Farm Service Agency"}, {"name": "Food and Nutrition Service"}],
                "page_metadata": {"hasNext": False}}
    sub = body["filters"]["agencies"][0]["name"]
    fy = int(body["filters"]["time_period"][0]["end_date"][:4])
    if body["filters"]["award_type_codes"] != usda.GROUPS["grants"]:
        return {"results": [], "page_metadata": {"hasNext": False}}
    rows = {"Farm Service Agency": [{"name": "ACME FARMS LLC", "uei": "ACME00000001", "amount": 500_000.0 * (fy - 2023)},
                                    {"name": "MULTIPLE RECIPIENTS", "uei": None, "amount": 9e6}],
            "Food and Nutrition Service": [{"name": "MINNESOTA DEPARTMENT OF EDUCATION", "uei": None,
                                            "code": "MNDOE0000001", "amount": 1e9},
                                           {"name": "ACME FARMS LLC", "uei": "ACME00000001", "amount": 100.0}]}
    return {"results": rows[sub], "page_metadata": {"hasNext": False}}


def test_build_recipient_file():
    rows, summary = usda.build([2024, 2025], 0, fake_post, lambda *_: None)
    by = {r["Vendor UEI"]: r for r in rows}
    acme = by["ACME00000001"]
    assert acme["Total Fiscal Year 2024 Dollars Obligated"] == 500_100.0
    assert acme["Total Fiscal Year 2025 Dollars Obligated"] == 1_000_100.0
    assert acme["Mission Area"] == "Farm Production and Conservation"
    state = by["MNDOE0000001"]
    assert state["Entity Structure Type"] == "U.S. Government Entity"
    assert state["Mission Area"] == "Food, Nutrition and Consumer Services"
    assert summary["without_uei"]["FY2024"]["recipients"] == 1


def test_output_reads_in_unchanged_pipeline(tmp_path, monkeypatch):
    monkeypatch.setattr(usda, "build", lambda years, floor: usda_build_real(years, floor))
    usda.main(["--out", str(tmp_path), "--years", "2024,2025", "--floor", "0"])
    df, v = load_vendor_file(tmp_path / "USDA_recipients.csv")
    assert v.ok and v.rows == 2
    assert set(df["uei"]) == {"ACME00000001", "MNDOE0000001"}


_real_build = usda.build


def usda_build_real(years, floor):
    return _real_build(years, floor, fake_post, lambda *_: None)
