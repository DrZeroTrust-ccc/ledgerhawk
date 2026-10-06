import json
import os
from datetime import date
from pathlib import Path

import pytest

from ledgerhawk.pipeline.exclusions import parse_aliases
from ledgerhawk.pipeline.normalize import legal_form, money, normalize_name
from ledgerhawk.pipeline.run import run_pipeline
from ledgerhawk.pipeline.synthetic import make_synthetic

EXCL_DATE = date(2026, 10, 2)


@pytest.fixture(scope="module")
def synthetic(tmp_path_factory):
    d = tmp_path_factory.mktemp("syn")
    vendors, excl, _sam, planted = make_synthetic(d, n=3000, seed=11)
    res = run_pipeline(vendors, excl, EXCL_DATE)
    by_uei = res.vendors.set_index("uei")
    return res, by_uei, planted, vendors, excl


def test_normalize_name():
    assert normalize_name("The K2 Contracting Group, L.L.C.") == "K2 CONTRACTING GROUP"
    assert normalize_name("Smith & Sons Co") == "SMITH AND SONS"
    assert normalize_name("Acme Corporation Inc") == "ACME"
    assert legal_form("Acme, L.L.C.") == "LLC"
    assert legal_form("Acme Corporation") == "CORP"
    assert legal_form("Acme Holdings") == ""


def test_money():
    assert money(27_400_000) == "$27.4M"
    assert money(-1_900_000) == "-$1.9M"
    assert money(1.03e12) == "$1.03T"


def test_funnel_is_consistent(synthetic):
    res, *_ = synthetic
    f = res.funnel
    for prev, step in zip(f, f[1:]):
        assert prev.vendors - step.cut == step.vendors
        assert prev.dollars - step.cut_dollars == pytest.approx(step.dollars)


def test_every_cut_has_a_reason(synthetic):
    res, *_ = synthetic
    cut = res.vendors[res.vendors["lane"] != "outlier"]
    assert (cut["reason_code"] != "").all()
    assert (cut["reason"] != "").all()


def test_planted_cases(synthetic):
    _, v, p, *_ = synthetic
    assert v.loc[p["major_by_name"], "reason_code"] == "MAJOR_AUDITED"
    assert v.loc[p["major_by_size"], "reason_code"] == "MAJOR_AUDITED"
    assert v.loc[p["closeout"], "reason_code"] == "CLOSEOUT_NET"
    assert v.loc[p["s1_fade"], "queue"] == "strong"
    assert v.loc[p["s1_rise"], "queue"] == "strong"
    assert v.loc[p["s2_strong"], "queue"] == "strong"
    assert v.loc[p["s3"], "bucket"] == "watch"  # 22.5x and $9M: below the strong bar, deferred not cleared
    assert v.loc[p["s4"], "queue"] == "strong"
    assert v.loc[p["priority"], "queue"] == "priority"
    assert v.loc[p["jv"], "bucket"] == ""  # declared JV: no growth signal
    assert "joint venture" in v.loc[p["jv"], "suppression"].lower()


def test_signal_tooltips_carry_values(synthetic):
    _, v, p, *_ = synthetic
    s2 = [s for s in v.loc[p["s2_strong"], "signals"] if s["id"] == "S2"][0]
    assert s2["detail"] == "FY24 $0 → FY25 $27.4M"


def test_exclusion_overrides_set_aside(synthetic):
    _, v, p, *_ = synthetic
    assert v.loc[p["excluded_major"], "lane"] == "set_aside"
    assert "EXCLUDED" in v.loc[p["excluded_major"], "exclusion_flags"]
    assert v.loc[p["excluded_major"], "queue"] == "exclusion"
    assert v.loc[p["excluded_small"], "queue"] == "integrity"
    assert "STALE_PENDING" in v.loc[p["excluded_small"], "exclusion_flags"]


def test_alias_comment_links_vendor(synthetic):
    _, v, p, *_ = synthetic
    assert "ALIAS_MATCH" in v.loc[p["alias_target"], "exclusion_flags"]


def test_expired_exclusions_dropped(synthetic):
    res, *_ = synthetic
    # 14 synthetic records; only the EPA one that terminated in 2022 is inactive.
    assert res.manifest["exclusions_active_records"] == 13


def test_parse_aliases():
    c = "FALSE BUSINESS ALIAS OF ACME WIDGETS LLC; ALSO KNOWN AS JOHN Q SAMPLE. D/B/A BEST BUILDERS"
    assert parse_aliases(c) == ["ACME WIDGETS LLC", "JOHN Q SAMPLE", "BEST BUILDERS"]


def test_restore_is_reversible(synthetic):
    _, _, p, vendors, excl = synthetic
    res = run_pipeline(vendors, excl, EXCL_DATE, restore={p["closeout"]})
    row = res.vendors.set_index("uei").loc[p["closeout"]]
    assert row["lane"] == "outlier"
    assert row["reason_code"] == "RESTORED"
    assert row["restored_from"] == "CLOSEOUT_NET"
    assert res.queue_counts["restored"] == 1


def test_deterministic(synthetic, tmp_path):
    _, _, _, vendors, excl = synthetic
    a = run_pipeline(vendors, excl, EXCL_DATE).write(tmp_path / "a")
    b = run_pipeline(vendors, excl, EXCL_DATE).write(tmp_path / "b")
    assert (a / "vendors.jsonl").read_bytes() == (b / "vendors.jsonl").read_bytes()
    assert json.loads((a / "run.json").read_text()) == json.loads((b / "run.json").read_text())


def test_no_fraud_language(synthetic, tmp_path):
    res, *_ = synthetic
    out = res.write(tmp_path / "lang")
    for f in out.iterdir():
        assert "fraud" not in f.read_text().lower(), f.name


PILOT = os.environ.get("LEDGERHAWK_PILOT_FILE")


@pytest.mark.skipif(not PILOT, reason="set LEDGERHAWK_PILOT_FILE to the GSA pilot vendor file")
def test_pilot_funnel():
    """Acceptance test 1 from the rework brief."""
    res = run_pipeline(Path(PILOT))
    counts = [s.vendors for s in res.funnel]
    assert counts[:3] == [118_897, 114_538, 42_587]
    # 1d depends on the major-contractor name list. The hand run's list also caught ~36 unrelated small
    # firms (APPLE TEN, MAZER INTEL, CARRIER MILLS NURSING) and missed ~86 subsidiaries (DRS, GOODRICH,
    # OPTUM*), so we hold 1d near the hand-run 41,291 rather than copying its mistakes.
    assert abs(counts[3] - 41_291) <= 100
    # Queue parity with the hand-run second pass: same 15 priority vendors, 229 single-signal outliers
    # less AKTOR S.A, which this build recognizes as a foreign entity.
    assert res.queue_counts["priority"] == 15
    assert res.queue_counts["strong"] == 228
    assert res.queue_counts["integrity_lane"] == 70_348
    assert res.queue_counts["closeouts"] == 1_603


def test_fiscal_year_dollar_columns_named_loosely():
    from ledgerhawk.pipeline.ingest import map_columns
    m = map_columns(["Vendor UEI", "Vendor Name", "FY24 Obligated ($)", "Fiscal Year 2025 Net Obligations", "FY24 Notes"])
    assert m == {"Vendor UEI": "uei", "Vendor Name": "name", "FY24 Obligated ($)": "fy24",
                 "Fiscal Year 2025 Net Obligations": "fy25"}


def test_joint_venture_carrying_an_excluded_partners_name():
    import pandas as pd

    from ledgerhawk.pipeline.exclusions import ExclusionsExtract, exclusion_pass
    from ledgerhawk.pipeline.rules import RuleSet

    def rec(name, uei=""):
        return {"display_name": name, "uei": uei, "agency": "AF", "etype": "Proposed Debarment", "program": "Reciprocal",
                "ct_code": "", "active_date": "09/14/2026", "termination_date": "", "city": "DAYTON", "state": "OH",
                "comments": "", "scope": "", "classification": "Firm", "nn": normalize_name(name), "aliases": []}
    ex = ExclusionsExtract(pd.DataFrame([rec("Clemons, Inc.", "CLEMONSUEI01"), rec("Services Inc")]), date(2026, 10, 1), "x", 2)
    df = pd.DataFrame({"uei": ["JVUEI0000001", "OTHERUEI0001", "JVUEI0000002"],
                       "name": ["CLEMONS VAZQUEZ JV LLC", "CLEMONS VAZQUEZ LLC", "ACME SERVICES JV"]})
    df["nn"] = df["name"].map(normalize_name)
    out = exclusion_pass(df, ex, RuleSet()).set_index("uei")
    assert "JV_PARTNER_EXCLUDED" in out.loc["JVUEI0000001", "exclusion_flags"]
    assert out.loc["JVUEI0000001", "exclusion"][0]["kind"] == "jv_partner"
    assert out.loc["OTHERUEI0001", "exclusion_flags"] == []  # not a JV
    assert out.loc["JVUEI0000002", "exclusion_flags"] == []  # a generic word is not a partner name
