"""Stage 2 tests: Economist on the in-memory spreadsheet."""

import pytest

from trans_ocean.base import Base
from trans_ocean.economist import (Economist, MissingParams, compute, find_distance,
                                   format_result, parse_command)
from tests.test_stage0 import FakeSpreadsheet

TRAL = {"Топливо (л/100 км)": "40", "Цена топлива (сум/л)": "12 000", "Водитель (сум/км)": "500",
        "Командировочные (сум/день)": "100000", "Постоянные расходы (сум/мес)": "11000000",
        "Рабочих дней в месяц": "22", "Км в день": "500", "Прочие расходы на рейс (сум)": "200000"}


def make():
    base = Base(FakeSpreadsheet([]))
    base.ensure_sheets()
    eco = Economist(lambda: base)
    eco.setup(base)
    return base, eco


def fill_tral(base):
    row = next(r for r in base.records("Параметры машин") if r["Тип машины"] == "Трал")
    base.update_fields("Параметры машин", int(row["_row"]), TRAL)


def test_setup_is_idempotent_and_distances_work_both_ways():
    base, eco = make()
    assert len(base.records("Параметры машин")) == 4
    assert eco.setup(base) == {}
    assert find_distance(base.records("Расстояния"), "Toshkent", "Нукус") == 1150
    assert find_distance(base.records("Расстояния"), "нукус", "ташкент") == 1150


def test_compute_own_truck():
    vehicle = dict(TRAL, **{"Тип машины": "Трал", "Обратный порожний пробег (%)": "100"})
    calc = compute(vehicle, 300, {"Маржа (%)": 10, "НДС (%)": 12, "Округление (сум)": 10000})
    assert calc.total_km == 600 and calc.days == 2
    assert calc.fuel == 600 * 0.40 * 12000            # 2 880 000
    assert calc.driver == 600 * 500 + 2 * 100000      # 500 000
    assert calc.fixed == 11000000 / 22 * 2            # 1 000 000
    assert calc.cost == 4580000
    assert calc.min_price == 5040000                  # 5 038 000 rounded up
    assert calc.min_price_vat == 5650000              # 5 644 800 rounded up


def test_compute_hired_truck_and_missing_params():
    calc = compute({"Тип машины": "Тент"}, 300, {"Маржа (%)": 15, "НДС (%)": 0, "Округление (сум)": 1000},
                   hired_rate=4000000)
    assert not calc.own and calc.cost == 4000000 and calc.min_price == 4600000
    with pytest.raises(MissingParams):
        compute({"Тип машины": "Тент"}, 300, {})


def test_parse_command():
    rows = [{"Тип машины": "Трал"}, {"Тип машины": "Тент 96 м³"}, {"Тип машины": "Контейнеровоз"}]
    a = parse_command("/price Ташкент - Нукус трал", rows)
    assert (a["origin"], a["destination"], a["vehicle"]["Тип машины"]) == ("Ташкент", "Нукус", "Трал")
    b = parse_command("/price@TransBot Ташкент Самарканд тент наём 4500000", rows)
    assert b["vehicle"]["Тип машины"] == "Тент 96 м³" and b["hired"] == 4500000
    c = parse_command("/price 450 км контейнеровоз", rows)
    assert c["km"] == 450 and c["vehicle"]["Тип машины"] == "Контейнеровоз"


def test_calculate_saves_draft_and_answers():
    base, eco = make()
    reply = eco.answer("/price Ташкент Самарканд трал")
    assert "Не заполнены параметры" in reply and "Км в день" in reply
    fill_tral(base)
    reply = eco.answer("/price Ташкент Самарканд трал")
    assert "Себестоимость" in reply and "минимальная цена" in reply
    saved = base.records("Расчёты цены")
    assert len(saved) == 1 and saved[0]["Статус"] == "Черновик" and saved[0]["Себестоимость (UZS)"] == "4580000"
    base.append("Ставки рынка", {"Откуда": "Ташкент", "Куда": "Самарканд", "Тип машины": "Трал",
                                 "Ставка": "4 800 000", "Валюта": "UZS"})
    calc = eco.calculate("Ташкент", "Самарканд", "трал", save=False)
    assert calc.market == [4800000] and "Рынок: 1 ставок" in format_result(calc)
    assert "Нет расстояния" in eco.answer("/price Ташкент Москва трал")
    assert "Экономист" in eco.answer("/price")
