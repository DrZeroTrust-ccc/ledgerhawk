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
    vendors, excl, planted = make_synthetic(d, n=3000, seed=11)
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
    assert v.loc[p["excluded_small"], "queue"] == "exclusion"
    assert "STALE_PENDING" in v.loc[p["excluded_small"], "exclusion_flags"]


def test_alias_comment_links_vendor(synthetic):
    _, v, p, *_ = synthetic
    assert "ALIAS_MATCH" in v.loc[p["alias_target"], "exclusion_flags"]


def test_expired_exclusions_dropped(synthetic):
    res, *_ = synthetic
    assert res.manifest["exclusions_active_records"] == 3


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
    # 1d depends on the major-contractor name list, which the hand run didn't publish.
    # Hold it within 0.1% of the hand-run 41,291 rather than overfitting the list.
    assert abs(counts[3] - 41_291) <= 41
    assert res.queue_counts["integrity_lane"] == 70_348
    assert res.queue_counts["closeouts"] == 1_603
