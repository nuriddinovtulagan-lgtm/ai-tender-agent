"""Stage 5 tests: Marketing agent."""

import asyncio
from datetime import datetime

from trans_ocean.base import TZ, Base
from trans_ocean.marketing import (Lead, MarketingAgent, parse_feed, parse_lead_callback, pick_leads, score)
from tests.test_stage0 import FakeSpreadsheet

RSS = """<?xml version="1.0" encoding="UTF-8"?><rss version="2.0"><channel><title>Spot</title>
<item><title>ACWA Power построит ВЭС мощностью 1,5 ГВт в Каракалпакстане за $1,8 млрд</title>
<link>https://www.spot.uz/ru/2026/10/26/acwa-wind/</link><description>Генподрядчик — China Energy, турбины доставят в 2027 году</description>
<pubDate>Mon, 26 Oct 2026 10:00:00 +0500</pubDate></item>
<item><title>В Ташкенте появится киногородок «Узбекфильма» площадью 10 га</title>
<link>https://www.spot.uz/ru/2026/10/26/cinema-city/</link><description>строительство</description>
<pubDate>Mon, 26 Oct 2026 09:00:00 +0500</pubDate></item>
<item><title>В Навои построят завод по производству цемента за 120 млн долларов</title>
<link>https://www.spot.uz/ru/2026/10/26/cement/</link><description>Оборудование поставит Sinoma</description>
<pubDate>Sun, 25 Oct 2026 12:00:00 +0500</pubDate></item>
<item><title>Apple Pay заработал в Узбекистане</title><link>https://x.uz/apple</link>
<pubDate>Mon, 26 Oct 2026 08:00:00 +0500</pubDate></item>
<item><title>Старая новость о заводе и турбинах</title><link>https://x.uz/old</link>
<pubDate>Mon, 01 Sep 2026 08:00:00 +0500</pubDate></item>
</channel></rss>"""
NOW = datetime(2026, 10, 26, 12, tzinfo=TZ)


class FakeTelegram:
    configured = True
    chat_id = "-1"

    def __init__(self):
        self.sent, self.edited, self.answers = [], [], []

    async def send(self, text, buttons=None, chat_id=None):
        self.sent.append((text, buttons))
        return {"message_id": len(self.sent)}

    async def edit(self, chat_id, message_id, text):
        self.edited.append(text)

    async def answer_callback(self, callback_id, text=""):
        self.answers.append(text)


def test_parse_and_score():
    items = parse_feed(RSS, "spot.uz")
    assert len(items) == 5
    wind = score(items[0])
    assert wind.score >= 9 and "Энергетика" in wind.categories and wind.region == "Каракалпакстан"
    assert "ACWA" in wind.companies
    assert score(items[1]).score == 0                       # cinema -> negative word
    leads = pick_leads(parse_feed(RSS, "spot.uz"), {"https://www.spot.uz/ru/2026/10/26/cement/"}, now=NOW)
    assert [l.link for l in leads] == ["https://www.spot.uz/ru/2026/10/26/acwa-wind/"]   # known + old + weak skipped


def test_collect_digest_and_buttons():
    base = Base(FakeSpreadsheet([]))
    base.ensure_sheets()
    tg = FakeTelegram()

    async def fetch(url):
        return RSS

    agent = MarketingAgent(lambda: base, tg, fetch=fetch, feeds=["https://www.spot.uz/ru/rss/"])
    result = asyncio.run(agent.collect())
    leads = base.records("Лиды")
    assert result["new_leads"] == len(leads) >= 1
    assert asyncio.run(agent.collect())["new_leads"] == 0           # no duplicates
    posted = asyncio.run(agent.post_digest(force=True))
    assert posted["posted"] == len(leads) and tg.sent[1][1][0][0]["callback_data"].startswith("l:")
    lead_id = base.records("Лиды")[0]["ID"]
    assert parse_lead_callback(f"l:{lead_id}:take") == {"lead_id": lead_id, "decision": "take"}
    callback = {"id": "c1", "data": f"l:{lead_id}:take", "from": {"first_name": "Тулаган"},
                "message": {"chat": {"id": -1}, "message_id": 2}}
    out = asyncio.run(agent.handle_callback(callback))
    assert out["status"] == "В работе"
    assert any(c["Тип"] == "Проект" for c in base.records("Компании"))
    assert any(t["Агент"] == "Маркетинг" for t in base.records("Задачи"))
    assert asyncio.run(agent.post_digest(force=True))["posted"] == 0          # all already posted


def test_broken_xml_and_atom_power_plant_headline():
    broken = RSS.replace("</title>\n<link>https://www.spot.uz/ru/2026/10/26/acwa-wind/", " &nbsp; </title>\n<link>https://www.spot.uz/ru/2026/10/26/acwa-wind/")
    broken += ("<item><title>В 2027 году на строительстве АЭС в Узбекистане начнутся масштабные работы — «Росатом»</title>"
               "<link>https://www.spot.uz/ru/2026/10/24/aes/</link><pubDate>Sat, 24 Oct 2026 15:48:00 GMT</pubDate></item>")
    items = parse_feed(broken, "spot.uz")
    assert len(items) == 6
    aes = score(items[-1])
    assert aes.score >= 5 and "Энергетика" in aes.categories and "Росатом" in aes.companies


def test_collect_reports_non_rss_source():
    base = Base(FakeSpreadsheet([]))
    base.ensure_sheets()

    async def fetch(url):
        return "<html><body>Checking your browser...</body></html>"

    result = asyncio.run(MarketingAgent(lambda: base, FakeTelegram(), fetch=fetch, feeds=["https://x.uz/rss"]).collect())
    assert result["per_feed"] == {"x.uz": 0} and "не RSS" in result["errors"][0]
