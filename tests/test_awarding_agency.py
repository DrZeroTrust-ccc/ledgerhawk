from datetime import date

from ledgerhawk.pipeline import awards


def _capture():
    seen = []

    def post(url, body):
        seen.append(body)
        return {"results": [], "page_metadata": {"hasNext": False}}
    return seen, post


def test_lookups_cover_every_agency_by_default(monkeypatch):
    monkeypatch.delenv("LEDGERHAWK_AWARDING_AGENCY", raising=False)
    seen, post = _capture()
    awards.awards_for_uei("ABC123DEF456", post=post, today=date(2026, 10, 19))
    awards.actions_after("ABC123DEF456", "2026-01-01", post=post, today=date(2026, 10, 19))
    assert seen and all("agencies" not in b["filters"] for b in seen)
    assert awards.source_label() == "USAspending"


def test_tenant_setting_limits_every_lookup_to_one_agency(monkeypatch):
    monkeypatch.setenv("LEDGERHAWK_AWARDING_AGENCY", "Department of Agriculture")
    seen, post = _capture()
    today = date(2026, 10, 19)
    awards.awards_for_uei("ABC123DEF456", post=post, today=today)
    awards.actions_after("ABC123DEF456", "2026-01-01", post=post, today=today)
    awards.subawards_for_uei("ABC123DEF456", "2026-01-01", post=post, today=today)
    awards.history_for_uei("ABC123DEF456", post=post, today=today)
    want = [{"type": "awarding", "tier": "toptier", "name": "Department of Agriculture"}]
    assert len(seen) >= 6 and all(b["filters"]["agencies"] == want for b in seen)
    assert awards.source_label() == "USAspending (Department of Agriculture only)"
