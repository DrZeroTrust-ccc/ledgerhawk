"""A vendor registered at a mailbox: a PMB or virtual office, or a suite shared by many SAM registrations."""
from ledgerhawk.exports.analysis import color, mailbox_address


def v(tot, **sam):
    return {"uei": "U", "queue": "", "exclusion_flags": [], "tot": tot, "sam": {"address": "1 MAIN ST, PMB 22", **sam}}


def test_mailbox_addresses():
    assert "mailbox or virtual office" in mailbox_address(v(1, virtual=True))
    assert "shared by 140 registrations: a registered agent, virtual office or a company headquarters" in mailbox_address(v(1, suite_count=140))
    assert mailbox_address(v(1, suite_count=6)) == "" and mailbox_address({"sam": None}) == ""


def test_big_money_at_a_mailbox_is_yellow_and_small_money_is_not():
    c, why = color(v(7.5e6, virtual=True), {}, None)
    assert c == "yellow" and why[0].startswith("Paid $7.5M while registered at a mailbox")
    assert color(v(900_000, virtual=True), {}, None) == ("", [])
    # a shared suite alone is mostly corporate headquarters: never yellow by itself, however much was paid
    assert color(v(7.4e9, suite_count=40), {}, None) == ("", [])
    # nor a major contractor set aside from the search, even at a PMB
    assert color({**v(7.5e6, virtual=True), "lane": "set_aside"}, {}, None) == ("", [])


def test_a_mailbox_is_an_added_reason_on_vendors_already_flagged():
    strong = {**v(100_000, suite_count=60), "queue": "strong"}
    c, why = color(strong, {}, None)
    assert c == "yellow" and why == ["One strong signal", mailbox_address(strong)]
    priority = {**v(100_000, virtual=True), "queue": "priority"}
    c, why = color(priority, {}, None)
    assert c == "red" and why[-1] == mailbox_address(priority)
