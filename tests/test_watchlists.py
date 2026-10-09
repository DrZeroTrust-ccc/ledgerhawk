"""Every vendor against the OFAC sanctions list and HHS-OIG's exclusions, by name, on every import."""
from datetime import date

import pytest

from ledgerhawk.api.store import Store
from ledgerhawk.exports.analysis import color, watchlist_reasons
from ledgerhawk.pipeline import context, osint, watchlists
from ledgerhawk.pipeline.synthetic import make_synthetic

OFAC_CSV = ('36,"GLOBAL SHADOW TRADING LLC",-0- ,"SDGT",-0- ,-0- \n'
            '37,"PETROV, Ivan",individual,"RUSSIA-EO14024",-0- ,-0- \n'
            '38,"ACME",-0- ,"SDGT",-0- ,-0- \n')
ALT_CSV = '36,101,"aka","SHADOW GLOBAL TRADERS",-0- \n'
LEIE_CSV = ("LASTNAME,FIRSTNAME,MIDNAME,BUSNAME,GENERAL,SPECIALTY,UPIN,NPI,DOB,ADDRESS,CITY,STATE,ZIP,EXCLTYPE,EXCLDATE,REINDATE,WAIVERDATE,WVRSTATE\n"
            ",,,SUNRISE MEDICAL SUPPLY INC,DME COMPANY,,,,,1 MAIN ST,TAMPA,FL,33601,1128a1,20190315,00000000,00000000,\n"
            ",,,BLUE RIVER HEALTH SERVICES LLC,CLINIC,,,,,2 ELM ST,DALLAS,TX,75201,1128b7,20210601,00000000,00000000,\n"
            ",,,OLD REINSTATED CO LLC,CLINIC,,,,,3 OAK ST,RENO,NV,89501,1128b7,20150101,20200101,00000000,\n"
            "SMITH,JOHN,,,NURSE,,,,,4 PINE,AUSTIN,TX,73301,1128a1,20200101,00000000,00000000,\n")


def fetch(url, headers):
    return {context.OFAC_URL: OFAC_CSV, context.OFAC_ALT_URL: ALT_CSV, osint.LEIE_URL: LEIE_CSV}[url].encode("latin-1")


@pytest.fixture(autouse=True)
def fresh_lists():
    context._ofac_cache.update(at=0.0, rows=None)
    osint._leie.update(at=0.0, rows=None)
    yield
    context._ofac_cache.update(at=0.0, rows=None)
    osint._leie.update(at=0.0, rows=None)


def test_names_match_with_how_strong_the_match_is(tmp_path):
    ofac, leie, errors = watchlists.load_lists(fetch, tmp_path)
    assert errors == [] and len(leie) == 3  # the reinstated one is left out
    vendors = [
        {"uei": "A", "name": "Global Shadow Trading, L.L.C.", "struct": "Corporate Entity"},
        {"uei": "B", "name": "Some Name", "struct": "", "sam": {"dba": "Shadow Global Traders", "state": "VA"}},  # an OFAC alias
        {"uei": "C", "name": "SUNRISE MEDICAL SUPPLY, INC.", "sam": {"state": "FL"}},
        {"uei": "D", "name": "Blue River Health Services LLC", "sam": {"state": "OK"}},
        {"uei": "E", "name": "ACME", "struct": ""},  # one short word: never matched
        {"uei": "F", "name": "IVAN PETROV", "struct": "Sole Proprietorship"},
        {"uei": "G", "name": "JOHN SMITH", "struct": "Sole Proprietorship", "sam": {"state": "CA"}},  # common name, other state
        {"uei": "H", "name": "OLD REINSTATED CO LLC"},
    ]
    hits = watchlists.screen(vendors, ofac, leie)
    assert set(hits) == {"A", "B", "C", "D", "F"}
    assert hits["A"][0]["list"] == "OFAC SDN" and "SDGT" in hits["A"][0]["detail"]
    assert "alias of GLOBAL SHADOW TRADING LLC" in hits["B"][0]["name"]
    assert hits["C"][0]["match"] == "name and state" and hits["C"][0]["date"] == "2019-03-15"
    assert hits["D"][0]["match"] == "same name"
    assert hits["F"][0]["detail"].startswith("individual")

    def down(url, headers):
        raise OSError("unreachable")
    context._ofac_cache.update(at=0.0, rows=None)
    osint._leie.update(at=0.0, rows=None)
    assert len(watchlists.load_lists(down, tmp_path / "empty")[2]) == 2


def test_colors_from_watchlist_matches():
    v = {"uei": "A", "queue": "", "exclusion_flags": [], "watchlist": [
        {"list": "OFAC SDN", "name": "X", "detail": "program SDGT", "match": "same name"}]}
    assert color(v, {}, None)[0] == "red"
    leie_state = {**v, "watchlist": [{"list": "HHS-OIG LEIE", "name": "Y", "detail": "Tampa, FL", "match": "name and state"}]}
    assert color(leie_state, {}, None)[0] == "red"
    leie_name = {**v, "watchlist": [{"list": "HHS-OIG LEIE", "name": "Y", "detail": "Tampa, FL", "match": "same name"}]}
    c, why = color(leie_name, {}, None)
    assert c == "yellow" and "HHS-OIG" in why[0]
    assert watchlist_reasons({"watchlist": []}) == ([], [])


def test_every_import_is_screened_and_says_so(tmp_path):
    st = Store(tmp_path / "data")
    st.watchlists, st.watchlist_fetch = True, fetch
    vendors, excl, _, _ = make_synthetic(tmp_path / "in", n=200, seed=8)
    import pandas as pd
    df = pd.read_csv(vendors)
    name_col = next(c for c in df.columns if "name" in c.lower())
    df.loc[0, name_col] = "Global Shadow Trading LLC"
    df.to_csv(vendors, index=False)
    rid = st.create_run(vendors, excl, date(2026, 10, 2), synthetic=True, analyst="Ana")
    uei = str(df.iloc[0][next(c for c in df.columns if "uei" in c.lower())])
    v = st.vendors(rid)["by_uei"][uei]
    assert v["watchlist"][0]["list"] == "OFAC SDN"
    man = st.summary(rid)["manifest"]["watchlists"]
    assert man["vendors_matched"] >= 1 and man["ofac_entries"] == 4 and man["errors"] == []
    assert all("watchlist" in r for r in st.vendors(rid)["rows"])
