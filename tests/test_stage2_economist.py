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
    assert "Нет ставок найма" in reply
    reply = eco.answer("/price Ташкент Самарканд трал своя")
    assert "не заполнены параметры" in reply and "Км в день" in reply
    fill_tral(base)
    reply = eco.answer("/price Ташкент Самарканд трал")
    assert "Себестоимость" in reply and "минимальная цена" in reply
    saved = base.records("Расчёты цены")
    assert len(saved) == 1 and saved[0]["Статус"] == "Черновик" and saved[0]["Себестоимость (UZS)"] == "4580000"
    base.append("Ставки рынка", {"Откуда": "Ташкент", "Куда": "Самарканд", "Тип машины": "Трал",
                                 "Ставка": "4 800 000", "Валюта": "UZS"})
    calc = eco.calculate("Ташкент", "Самарканд", "трал", save=False, own=True)
    assert calc.own and calc.market == [4800000] and "Рынок: 1 ставок" in format_result(calc)
    hired = eco.calculate("Ташкент", "Самарканд", "трал", save=False)
    assert not hired.own and hired.hired == 4800000 and "средняя по 1" in hired.hired_source
    assert "Нет расстояния" in eco.answer("/price Ташкент Москва трал своя")
    assert "Нет ставок найма" in eco.answer("/price Ташкент Москва трал")
    assert "Экономист" in eco.answer("/price")


def test_rate_command_and_hired_default_with_overheads():
    base, eco = make()
    assert "Записать цену найма" in eco.add_rate("/rate Ташкент тент")
    reply = eco.add_rate("/rate Ташкент - Нукус тент 20т 4 000 000".replace("4 000 000", "4000000"), "Диспетчер")
    assert "Записано" in reply and "4 000 000" in reply
    eco.add_rate("/rate Toshkent Nukus тент 5000000", "Диспетчер")
    rates = base.records("Ставки рынка")
    assert len(rates) == 2 and rates[0]["Тоннаж (т)"] == "20" and rates[0]["Добавил"] == "Диспетчер"
    # overheads from "Параметры цены"
    params = {r["Параметр"]: r for r in base.records("Параметры цены")}
    for name, value in {"Накладные на рейс (сум)": "100000", "Риск (%)": "2",
                        "Отсрочка оплаты (дней)": "30", "Стоимость денег (% в месяц)": "2",
                        "Маржа (%)": "10", "НДС (%)": "0", "Округление (сум)": "1000"}.items():
        base.update_fields("Параметры цены", int(params[name]["_row"]), {"Значение": value})
    calc = eco.calculate("Ташкент", "Нукус", "тент", save=False)
    assert calc.hired == 4500000                      # average of 4.0 and 5.0 mln
    assert calc.overhead == 100000
    assert calc.risk == (4500000 + 100000) * 0.02     # 92 000
    assert calc.financing == 4500000 * 0.02           # 90 000
    assert calc.cost == 4782000 and calc.min_price == 5261000
    reply = eco.answer("/price Ташкент Нукус тент")
    assert "средняя по 2 ставкам" in reply and "Накладные" in reply


def test_setup_adds_new_price_params_to_old_sheet():
    base = Base(FakeSpreadsheet([]))
    base.ensure_sheets()
    base.append_many("Параметры цены", [{"Параметр": "Маржа (%)", "Значение": "20", "Описание": ""}])
    added = Economist(lambda: base).setup(base)
    names = [r["Параметр"] for r in base.records("Параметры цены")]
    assert added["Параметры цены"] == len(names) - 1 and names.count("Маржа (%)") == 1
    assert base.records("Параметры цены")[0]["Значение"] == "20"
