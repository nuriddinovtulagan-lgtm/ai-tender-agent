"""Stage 6 tests: freight ad parser and the Dispatcher / Price agent (fake Telegram account)."""

import asyncio
from datetime import datetime, timedelta

from trans_ocean.base import TZ, Base
from trans_ocean.dispatcher import (DispatcherAgent, Message, Settings, merge_carriers, rates_answer, summary_text,
                                    trucks_answer)
from trans_ocean.economist import market_rates
from trans_ocean.freight_parser import KIND_CARGO, KIND_TRUCK, ad_key, find_phones, find_price, parse_ad, split_ads
from tests.test_stage0 import FakeSpreadsheet

NOW = datetime.now(TZ)


def test_parse_typical_ads():
    a = parse_ad("Toshkent - Buxoro fura tent 20t yuk bor 5 mln +998 90 123 45 67")
    assert (a.kind, a.origin, a.destination, a.vehicle, a.tonnage) == (KIND_CARGO, "Ташкент", "Бухара", "Тент (фура)", "20")
    assert a.price == 5_000_000 and a.currency == "UZS" and a.phones == ["+998 90 123 45 67"] and a.is_rate

    b = parse_ad("Тошкентдан Самарқандга трал керак 4 500 000 сўм")
    assert (b.origin, b.destination, b.vehicle, b.price) == ("Ташкент", "Самарканд", "Трал", 4_500_000)

    c = parse_ad("Фура бўш Бухорода, Тошкентга юк керак 90 1234567")
    assert (c.kind, c.origin, c.destination) == (KIND_TRUCK, "Бухара", "Ташкент")
    assert c.phones == ["+998 90 123 45 67"] and not c.price and c.useful and not c.is_rate

    d = parse_ad("Ташкент → Нукус, реф 20 т, 9,5 млн, тел. 99 765 43 21")
    assert (d.vehicle, d.price, d.tonnage) == ("Рефрижератор", 9_500_000, "20")

    e = parse_ad("ЛИТВАДАН МОШИНА БОР ТОШКЕНТГА ЮК КЕРАК 1800$ +998977001122")
    assert (e.kind, e.origin, e.destination, e.price, e.currency) == (KIND_TRUCK, "Литва", "Ташкент", 1800, "USD")

    f = parse_ad("Navoiyga Qarshidan 10 tonna yuk bor, narxi 3500 ming, tel 991112233")
    assert (f.origin, f.destination, f.price, f.tonnage) == ("Карши", "Навои", 3_500_000, "10")

    assert not parse_ad("Хайрли тонг! Ассалому алайкум").useful
    assert not parse_ad("Toshkent yuk bor").useful          # one place only


def test_phone_and_price_do_not_mix():
    phones, rest = find_phones("Tel: 90-555-44-33, narx 4 500 000")
    assert phones == ["+998 90 555 44 33"]
    assert find_price(rest) == (4_500_000, "UZS")
    assert find_price("20 000 kg") == (0, "")                # weight, not a price
    assert find_phones("4 500 000")[0] == []


def test_split_and_dedup_key():
    parts = split_ads("1) Toshkent - Buxoro tent 5 mln\n2) Samarqand - Nukus ref 8 mln\n3) tel 901234567")
    assert len(parts) == 2
    assert ad_key("Toshkent - Buxoro 5 mln!") == ad_key("toshkent buxoro 5 MLN")


def test_merge_carriers_new_and_known():
    existing = [{"_row": "2", "Телефон": "+998 90 123 45 67", "Маршруты": "Бухара → Ташкент", "Telegram": ""}]
    msg = Message(1, NOW, "", "Yuk Markazi", sender_name="Ali", sender_username="ali_fura")
    known = parse_ad("Fura bo'sh Samarqandda, Toshkentga yuk kerak 90 123 45 67")
    fresh = parse_ad("Tral bo'sh Navoiyda Toshkentga 99 111 22 33")
    new_rows, updates = merge_carriers(existing, [(known, msg), (fresh, msg)], max_updates=5)
    assert updates == [(2, {"Маршруты": "Самарканд → Ташкент; Бухара → Ташкент"})]
    assert len(new_rows) == 1 and new_rows[0]["Телефон"] == "+998 99 111 22 33"
    assert new_rows[0]["Документы проверены"] == "нет" and new_rows[0]["Telegram"] == "@ali_fura"


class FakeReader:
    configured = True
    session = "s"
    account = "Faruh"

    def __init__(self, messages):
        self.messages = messages
        self.calls = []

    async def authorized(self):
        return True

    async def list_groups(self):
        return [{"id": -100, "name": "Yuk Markazi", "username": "yukmarkazi"},
                {"id": -200, "name": "AVTO TRASSA UZ", "username": ""}]

    async def fetch(self, group, min_id, since, limit):
        self.calls.append((group["id"], min_id, since is not None))
        return [m for m in self.messages.get(group["id"], []) if m.id > min_id]


class FakeTelegram:
    configured = True

    def __init__(self):
        self.sent = []

    async def send(self, text, buttons=None, chat_id=None):
        self.sent.append(text)
        return {"message_id": len(self.sent)}


def make_agent(messages):
    base = Base(FakeSpreadsheet([]))
    base.ensure_sheets()
    registry = {}
    agent = DispatcherAgent(lambda: base, FakeTelegram(), FakeReader(messages),
                            registry_load=lambda: dict(registry),
                            registry_save=lambda k, v, d: registry.__setitem__(k, v),
                            settings=Settings(digest_hour=99))
    return agent, base, registry


def test_collect_writes_ads_rates_carriers_and_remembers_last_ids():
    t = NOW - timedelta(minutes=30)
    cargo = "Toshkent - Nukus tent 20t yuk bor 9 mln 90 123 45 67"
    messages = {
        -100: [Message(10, t, cargo, "Yuk Markazi", "https://t.me/yukmarkazi/10"),
               Message(11, t, "Tral bo'sh Buxoroda, Toshkentga yuk kerak 99 111 22 33", "Yuk Markazi",
                       sender_name="Bobur", sender_username="bobur_tral"),
               Message(12, t, "Salom hammaga", "Yuk Markazi")],
        -200: [Message(5, t, cargo, "AVTO TRASSA UZ")],                    # same ad reposted -> skipped
    }
    agent, base, registry = make_agent(messages)
    result = asyncio.run(agent.collect())
    assert result["messages"] == 4 and result["ads"] == 2 and result["rates"] == 1 and result["carriers_new"] == 1

    ads = base.records("Объявления")
    assert [a["Тип"] for a in ads] == [KIND_CARGO, KIND_TRUCK] and ads[0]["Ссылка"] == "https://t.me/yukmarkazi/10"
    rates = base.records("Ставки рынка")
    assert rates[0]["Ставка"] == "9000000" and rates[0]["Источник"] == "Telegram: Yuk Markazi"
    assert market_rates(rates, "Ташкент", "Нукус", "тент") == [9_000_000]      # Economist sees it
    carriers = base.records("Перевозчики")
    assert carriers[0]["Telegram"] == "@bobur_tral" and carriers[0]["Маршруты"] == "Бухара → Ташкент"
    assert '"-100": 12' in registry["dispatcher:last_ids"]

    # second run: only newer messages, nothing duplicated
    agent.reader.messages[-100].append(Message(13, NOW, "Samarqand - Termiz ref 7 mln 93 555 44 33", "Yuk Markazi"))
    again = asyncio.run(agent.collect())
    assert again["ads"] == 1 and len(base.records("Объявления")) == 3
    assert agent.reader.calls[-2] == (-100, 12, False)


def test_commands_answers():
    t = NOW - timedelta(hours=2)
    messages = {-100: [
        Message(1, t, "Tral bo'sh Buxoroda, Toshkentga yuk kerak 99 111 22 33", "Yuk Markazi"),
        Message(2, t, "Fura tent bo'sh Buxoroda 90 222 33 44", "Yuk Markazi"),
        Message(3, t, "Toshkent - Nukus tent 20t yuk bor 9 mln", "Yuk Markazi"),
        Message(4, t, "Ташкент Нукус тент 11 млн срочно", "Yuk Markazi"),
    ]}
    agent, base, _ = make_agent(messages)
    asyncio.run(agent.collect())
    trucks = agent.trucks("/mashina Бухара Ташкент трал")
    assert "99 111 22 33" in trucks and "90 222 33 44" not in trucks
    assert "Объявления за 3 дня</b> — 2" in agent.trucks("/mashina Buxoro")
    rates = agent.rates("/stavka Ташкент Нукус тент")
    assert "2 ставок" in rates and "10 000 000 сум" in rates                     # median of 9 and 11 mln
    assert "ставок нет" in agent.rates("/stavka Ташкент Термез")
    summary = agent.summary()
    assert "Объявлений: 4" in summary and "Ташкент → Нукус — 2" in summary


def test_answers_without_arguments_show_help():
    assert "/mashina" in trucks_answer([], [], "/mashina")
    assert "/stavka" in rates_answer([], "/stavka Ташкент")
    assert "Объявлений: 0" in summary_text([], "2026-10-27", {})


def test_not_logged_in_skips():
    agent, _, _ = make_agent({})

    async def no():
        return False
    agent.reader.authorized = no
    assert asyncio.run(agent.collect()) == {"skipped": "not_logged_in"}
