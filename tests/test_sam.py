"""Stages 3–5 on SAM data, checked against planted synthetic cases."""
from datetime import date

import pytest

from ledgerhawk.pipeline.run import run_pipeline
from ledgerhawk.pipeline.sam import building_key, load_sam, person_key, suite_key
from ledgerhawk.pipeline.synthetic import make_synthetic

EXCL_DATE = date(2026, 10, 2)
SAM_DATE = date(2026, 9, 6)


@pytest.fixture(scope="module")
def run(tmp_path_factory):
    d = tmp_path_factory.mktemp("sam")
    vendors, excl, sam, planted = make_synthetic(d, n=2000, seed=5)
    res = run_pipeline(vendors, excl, EXCL_DATE, sam_file=sam, sam_extract_date=SAM_DATE)
    return res, res.vendors.set_index("uei"), planted, (vendors, excl, sam)


def sig(v, uei):
    return {s["id"] for s in v.at[uei, "signals"]}


def test_address_and_person_keys():
    assert suite_key("10451 Mill Run Circle", "Suite 400", "21117-1234") == "10451MILLRUNCIRSTE400|21117"
    assert suite_key("10451 MILL RUN CIR", "STE 400", "21117") == "10451MILLRUNCIRSTE400|21117"
    assert suite_key("47 E South Street", "#002", "21701") == suite_key("47 East South St", "Unit 002", "21701")
    assert building_key("10451 Mill Run Cir Ste 400", "21117") == "10451 MILL RUN|21117"
    assert building_key("6600 Virginia Manor Road", "20705") == "6600 VIRGINIA MANOR|20705"
    assert person_key("Reginald", "Foster", "ny") == "REGINALD|FOSTER|NY"
    assert person_key("", "Foster", "NY") == ""


def test_sam_card_and_match_rate(run):
    res, v, planted, _ = run
    card = v.at[planted["split_nj"], "sam"]
    assert card["certs"] == ["8(a)"] and card["start_date"] == "2019-04-01" and card["active"]
    assert 0.8 < res.queue_counts["sam_matched"] / len(v) < 0.95


def test_split_registrations(run):
    _, v, p, _ = run
    for k in ("split_va", "split_nj", "split_md"):
        assert "R_split_cert" in sig(v, p[k])
    assert v.at[p["split_nj"], "queue"] == "relationship"  # S2 + R_split_cert


def test_linked_successor_and_affiliation(run):
    _, v, p, _ = run
    assert {"S3", "L_successor"} <= sig(v, p["succ_new"]) and v.at[p["succ_new"], "queue"] == "relationship"
    assert "L_successor" in sig(v, p["succ_old"])
    assert "L_affil_cert" in sig(v, p["affil_b"])
    assert v.at[p["affil_a"], "queue"] == "relationship"  # R_split_cert + L_affil_cert
    assert {"R_young", "S3"} <= sig(v, p["young"]) and v.at[p["young"], "queue"] == "relationship"


def test_hub_suppression(run):
    _, v, p, _ = run
    for u in p["hub"]:
        assert v.at[u, "links"] == [] and v.at[u, "neighbors"] == []
        card = v.at[u, "sam"]
        if card:
            assert card["suite_count"] > 5 and all(c["universe"] > 5 for c in card["pocs"])


def test_exclusion_links(run):
    _, v, p, _ = run
    aff = v.loc[p["ex_affiliate"]]
    assert {"R_EXADDR", "R_EXPOC"} <= set(aff["exclusion_flags"]) and aff["queue"] == "exclusion"
    kinds = {(h["kind"], h["agency"]) for h in aff["exclusion"]}
    assert ("address", "ICE") in kinds and ("person", "ICE") in kinds
    assert "R_EXPOC" in v.at[p["ex_person_vendor"], "exclusion_flags"]
    assert "NAME_MATCH_SUPPORTED" in v.at[p["name_supported"], "exclusion_flags"]
    assert "NAME_MATCH_SUPPORTED" not in v.at[p["name_collision"], "exclusion_flags"]
    assert v.at[p["name_collision"], "queue"] == ""
    assert v.at[p["facility_neighbor"], "exclusion_flags"] == []


def test_sam_cache_and_determinism(run, tmp_path):
    res, _, _, (vendors, excl, sam) = run
    a = load_sam(sam, SAM_DATE)
    b = load_sam(sam, SAM_DATE)  # second load reads the cache
    assert a.sha256 == b.sha256 and a.records == b.records and a.freq_suite == b.freq_suite
    again = run_pipeline(vendors, excl, EXCL_DATE, sam_file=sam, sam_extract_date=SAM_DATE)
    assert again.queue_counts == res.queue_counts
    assert res.manifest["sam_extract_date"] == "2026-09-06" and res.manifest["sam_sha256"] == a.sha256


def test_no_fraud_language(run):
    res, v, _, _ = run
    text = " ".join(str(x) for col in ("signals", "links", "exclusion") for x in v[col])
    assert "fraud" not in text.lower()
