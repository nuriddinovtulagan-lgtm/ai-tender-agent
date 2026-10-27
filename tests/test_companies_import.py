"""Company import: fills empty fields only, adds new companies."""

import json
from pathlib import Path

from trans_ocean.base import Base, import_companies
from tests.test_stage0 import start_spreadsheet

DATA = Path(__file__).resolve().parent.parent / "trans_ocean" / "imports" / "companies_2026_10_27.json"


def test_import_real_batch():
    base = Base(start_spreadsheet())
    base.ensure_sheets()
    base.migrate_start_companies()
    items = json.loads(DATA.read_text(encoding="utf-8"))
    gw_before = next(c for c in base.records("Компании") if c["Название"] == "Gebrüder Weiss Uzbekistan")
    result = import_companies(base, items)
    companies = {c["Название"]: c for c in base.records("Компании")}
    gw = companies["Gebrüder Weiss Uzbekistan"]
    assert gw["Телефон"] == gw_before["Телефон"]                  # filled value kept
    assert "Ernazarov" in gw["Заметки"]                            # note appended
    assert "Gebrüder Weiss Uzbekistan" in result["updated"]
    assert companies["Sinotrans (Ташкент)"]["Телефон"] == "+998 71 231 88 36"
    assert companies["BYD Uzbekistan (завод, Джизак)"]["Тип"] == "Китайский грузоотправитель"
    again = import_companies(base, items)
    assert again["added"] == [] and len(base.records("Компании")) == len(companies)


def test_placeholder_is_replaced_but_real_value_kept():
    base = Base(start_spreadsheet())
    base.ensure_sheets()
    base.append("Компании", {"ID": "C-1", "Название": "X Co", "Телефон": "+998 78 140-XX-XX (уточнить)",
                             "E-mail": "real@x.uz"})
    import_companies(base, [{"Название": "x co", "Телефон": "+998 78 140 01 21", "E-mail": "other@x.uz"}])
    row = base.records("Компании")[0]
    assert row["Телефон"] == "+998 78 140 01 21" and row["E-mail"] == "real@x.uz"
