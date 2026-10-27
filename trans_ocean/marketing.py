"""Marketing agent (stage 5): finds new projects that will need transport.

- Every hour reads Uzbek business news (RSS: gazeta.uz, spot.uz, uzdaily.uz, kun.uz) and scores
  each item by keywords: power plants, plants and factories, roads and bridges, pipelines,
  equipment deliveries, big investments, named contractors. Good items go to the sheet "Лиды".
- Once a day (MARKETING_DIGEST_HOUR) posts the new leads to the management group with buttons
  "✅ В работу" (adds the project to "Компании" and a task for the sales manager) and "✖ Пропустить".
- /leads in the group: fetch now and show the newest leads.
"""

from __future__ import annotations

import asyncio
import email.utils
import html
import logging
import re
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Callable, Dict, List, Optional, Tuple

from .base import Base, new_id, now_local, stamp

logger = logging.getLogger("trans_ocean.marketing")
AGENT = "Маркетинг"
SHEET = "Лиды"

DEFAULT_FEEDS = [
    "https://www.gazeta.uz/ru/rss/",
    "https://www.spot.uz/ru/rss/",
    "https://uzdaily.uz/ru/rss",
    "https://kun.uz/news/rss",
]

# (pattern, weight, category). Lower-case match on title + description.
KEYWORDS: List[Tuple[str, int, str]] = [
    (r"ветрян|ветров|\bвэс\b|shamol", 3, "Энергетика"),
    (r"солнечн|\bсэс\b|quyosh", 3, "Энергетика"),
    (r"электростанц|elektr stansiya|\bтэс\b|\bгэс\b|\bаэс\b|атомн", 3, "Энергетика"),
    (r"подстанц|\bлэп\b|линии электропередач|energy storage|накопител", 3, "Энергетика"),
    (r"завод|фабрик|комбинат|zavod|fabrika|kombinat", 3, "Промышленность"),
    (r"месторожден|рудник|горно|\bkon\b|konchilik", 3, "Горнодобыча"),
    (r"газопровод|нефтепровод|трубопровод|quvur", 3, "Нефть и газ"),
    (r"железн\w* дорог|ж/д|temir yo'l|temir yo‘l", 2, "Транспорт"),
    (r"автомагистрал|трасс|скоростн\w* дорог|avtomagistral|ko'prik|ko‘prik|\bмост", 3, "Дороги"),
    (r"строительств|возвед|qurilish|quriladi|построят|строят", 2, "Строительство"),
    (r"оборудован|турбин|трансформатор|uskuna|негабарит|тяжеловес", 3, "Оборудование"),
    (r"генподряд|подрядчик|\bepc\b|pudratchi", 2, "Подрядчики"),
    (r"инвестиц|investitsiya|инвестпроект|инвестор", 1, "Инвестиции"),
    (r"технопарк|промзон|\bсэз\b|\bспз\b|свободн\w* экономическ|sanoat zonasi|erkin iqtisodiy", 2, "Промзоны"),
    (r"базов\w* станц|вышк|мачт|5g|telecom|телеком", 2, "Телеком"),
    (r"коридор|контейнерн\w* поезд|сухой порт|сухого порта|терминал|хаб|иркештам|торугарт|хайратон|"
     r"транзит|yo'lak|yo‘lak|konteyner|terminal", 2, "Коридоры и терминалы"),
    (r"цемент|металлург|стекольн|химическ", 2, "Промышленность"),
]
COMPANY_BONUS = re.compile(
    r"acwa|masdar|china energy|powerchina|sinohydro|cnpc|lukoil|лукойл|росатом|rosatom|huawei|zte|nokia|"
    r"siemens|ge vernova|voltalia|total|edf|saudi|catic|covec|sany|china railway|tbea|gezhouba|hyundai|"
    r"samsung|daewoo|posco|uzbekneftegaz|узбекнефтегаз|навоийуран|almalyk|алмалык|ngmk|нгмк", re.I)
MONEY = re.compile(r"(\$\s?\d[\d.,]*\s*(млн|млрд|mln|mlrd)?|\d[\d.,]*\s*(млн|млрд|mln|mlrd)\.?\s*(долл|\$|евро|dollar|yevro))", re.I)
NEGATIVE = re.compile(
    r"футбол|чемпионат|концерт|погод|воздух|дтп|авари|умер|погиб|суд |арест|кино|фильм|сериал|отравлен|"
    r"криптовалют|ipotek|ипотек|курс доллара|valyuta kursi", re.I)
REGIONS = {
    "Ташкент": r"ташкент|toshkent", "Самарканд": r"самарканд|samarqand", "Бухара": r"бухар|buxoro",
    "Навои": r"навои|navoiy", "Кашкадарья": r"кашкадар|qashqadaryo|карши|qarshi",
    "Сурхандарья": r"сурхандар|surxondaryo|термез|termiz", "Джизак": r"джизак|jizzax",
    "Сырдарья": r"сырдар|sirdaryo", "Фергана": r"фергана|farg'ona|farg‘ona", "Андижан": r"андижан|andijon",
    "Наманган": r"наманган|namangan", "Хорезм": r"хорезм|xorazm|ургенч|urganch",
    "Каракалпакстан": r"каракалпак|qoraqalpog|нукус|nukus",
}
MIN_SCORE = 5


@dataclass
class Lead:
    title: str
    link: str
    source: str
    published: Optional[datetime]
    text: str = ""
    score: int = 0
    categories: List[str] = field(default_factory=list)
    region: str = ""
    companies: str = ""


# ---------------------------------------------------------------- pure helpers

def _tag(block: str, name: str) -> str:
    m = re.search(rf"<{name}[^>]*>(.*?)</{name}>", block, flags=re.S | re.I)
    if not m:
        return ""
    value = m.group(1).strip()
    cdata = re.match(r"<!\[CDATA\[(.*)\]\]>$", value, flags=re.S)
    return cdata.group(1) if cdata else value


def parse_feed(xml_text: str, source: str) -> List[Lead]:
    """RSS items. Tolerant: if the XML is broken (stray HTML entities etc.), items are cut
    out with regular expressions instead of failing silently."""
    raw = []
    try:
        root = ET.fromstring(xml_text.encode("utf-8") if isinstance(xml_text, str) else xml_text)
        for item in root.iter("item"):
            raw.append((item.findtext("title") or "", item.findtext("link") or "",
                        item.findtext("description") or "", item.findtext("pubDate") or ""))
    except ET.ParseError:
        for block in re.findall(r"<item[\s>].*?</item>", xml_text or "", flags=re.S | re.I):
            raw.append((_tag(block, "title"), _tag(block, "link"), _tag(block, "description"), _tag(block, "pubDate")))
    items = []
    for title, link, desc, raw_date in raw:
        title, link = html.unescape(re.sub(r"<[^>]+>", " ", title)).strip(), link.strip()
        published = None
        if raw_date:
            try:
                published = email.utils.parsedate_to_datetime(raw_date.strip())
            except (TypeError, ValueError):
                published = None
        if title and link:
            text = html.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", desc))).strip()
            items.append(Lead(title=title, link=link, source=source, published=published, text=text))
    return items


def score(lead: Lead) -> Lead:
    blob = f"{lead.title} {lead.text}".lower()
    if NEGATIVE.search(lead.title.lower()):
        lead.score = 0
        return lead
    total, cats = 0, []
    for pattern, weight, category in KEYWORDS:
        if re.search(pattern, blob):
            total += weight
            if category not in cats:
                cats.append(category)
    if MONEY.search(blob):
        total += 2
    found = sorted({m.group(0) for m in COMPANY_BONUS.finditer(f"{lead.title} {lead.text}")}, key=str.lower)
    if found:
        total += 2
    lead.score, lead.categories, lead.companies = total, cats, ", ".join(found[:5])
    lead.region = next((name for name, pattern in REGIONS.items() if re.search(pattern, blob)), "")
    return lead


def pick_leads(items: List[Lead], known_links: set, now: Optional[datetime] = None,
               max_age_days: int = 5) -> List[Lead]:
    now = now or now_local()
    out, seen = [], set(known_links)
    for item in items:
        if item.link in seen:
            continue
        if item.published and (now - item.published).days > max_age_days:
            continue
        score(item)
        if item.score >= MIN_SCORE:
            seen.add(item.link)
            out.append(item)
    out.sort(key=lambda x: x.score, reverse=True)
    return out


def lead_buttons(lead_id: str) -> List[List[Dict[str, str]]]:
    return [[{"text": "✅ В работу", "callback_data": f"l:{lead_id}:take"},
             {"text": "✖ Пропустить", "callback_data": f"l:{lead_id}:skip"}]]


def parse_lead_callback(data: str) -> Optional[Dict[str, str]]:
    parts = (data or "").split(":")
    if len(parts) == 3 and parts[0] == "l" and parts[1] and parts[2] in {"take", "skip"}:
        return {"lead_id": parts[1], "decision": parts[2]}
    return None


def lead_text(row: Dict[str, str]) -> str:
    e = html.escape
    meta = " · ".join(x for x in [row.get("Категории"), row.get("Регион"), row.get("Компании")] if x)
    return (f"📰 <b>{e(row.get('Заголовок', ''))}</b>\n{e(meta)}\n"
            f"Баллы: {e(row.get('Баллы', ''))} · {e(row.get('Источник', ''))}\n{e(row.get('Ссылка', ''))}")


# ---------------------------------------------------------------- agent

class MarketingAgent:
    def __init__(self, base_factory: Callable[[], Optional[Base]], telegram, fetch: Optional[Callable] = None,
                 registry_load: Optional[Callable[[], Dict[str, str]]] = None,
                 registry_save: Optional[Callable[[str, str, str], None]] = None,
                 feeds: Optional[List[str]] = None, digest_hour: int = 10, fetch_minutes: int = 60,
                 digest_limit: int = 7):
        self.base_factory = base_factory
        self.telegram = telegram
        self.fetch = fetch or self._http_fetch
        self.registry_load = registry_load or (lambda: {})
        self.registry_save = registry_save or (lambda k, v, d: None)
        self.feeds = feeds or DEFAULT_FEEDS
        self.digest_hour, self.fetch_minutes, self.digest_limit = digest_hour, fetch_minutes, digest_limit
        self._last_fetch: Optional[float] = None
        self._lock = asyncio.Lock()
        self.last_result: Dict[str, Any] = {}

    def _base(self) -> Base:
        base = self.base_factory()
        if base is None:
            raise RuntimeError("Google Sheets не настроен")
        return base

    async def _http_fetch(self, url: str) -> str:
        import httpx
        async with httpx.AsyncClient(timeout=20, follow_redirects=True,
                                     headers={"User-Agent": "Mozilla/5.0 TransOceanBot"}) as client:
            response = await client.get(url)
            response.raise_for_status()
            return response.text

    async def collect(self) -> Dict[str, Any]:
        items: List[Lead] = []
        errors = []
        per_feed: Dict[str, int] = {}
        for url in self.feeds:
            name = re.sub(r"^https?://(www\.)?", "", url).split("/")[0]
            try:
                body = await self.fetch(url)
                parsed = parse_feed(body, name)
                per_feed[name] = len(parsed)
                if not parsed:
                    start = re.sub(r"\s+", " ", body[:80])
                    errors.append(f"{name}: 0 новостей (сайт вернул не RSS: {start!r})")
                items += parsed
            except Exception as exc:
                per_feed[name] = 0
                errors.append(f"{name}: {exc}")
        base = await asyncio.to_thread(self._base)
        known = {r.get("Ссылка", "") for r in await asyncio.to_thread(base.records, SHEET)}
        leads = pick_leads(items, known)
        rows = [{
            "ID": new_id("L"), "Дата": stamp(), "Заголовок": lead.title[:300], "Ссылка": lead.link,
            "Источник": lead.source, "Баллы": lead.score, "Категории": ", ".join(lead.categories),
            "Регион": lead.region, "Компании": lead.companies, "Статус": "Новый", "Сообщение TG": "", "Заметки": "",
        } for lead in leads]
        if rows:
            await asyncio.to_thread(base.append_many, SHEET, rows)
            await asyncio.to_thread(base.log, AGENT, "Найдены новые лиды", f"{len(rows)} из {len(items)} новостей")
        return {"news": len(items), "new_leads": len(rows), "errors": errors, "per_feed": per_feed}

    async def post_digest(self, force: bool = False, chat_id: Optional[str] = None) -> Dict[str, Any]:
        key = f"marketing:digest:{now_local().strftime('%Y-%m-%d')}"
        if not force and key in await asyncio.to_thread(self.registry_load):
            return {"posted": 0, "reason": "already_today"}
        base = await asyncio.to_thread(self._base)
        rows = [r for r in await asyncio.to_thread(base.records, SHEET)
                if r.get("Статус") == "Новый" and not r.get("Сообщение TG")]
        rows.sort(key=lambda r: int(r.get("Баллы") or 0), reverse=True)
        rows = rows[:self.digest_limit]
        if not rows:
            if force:
                await self.telegram.send("📰 Новых проектов в новостях пока нет.", chat_id=chat_id)
            return {"posted": 0}
        await self.telegram.send(f"📈 <b>Маркетинг: новые проекты в новостях</b> — {len(rows)}", chat_id=chat_id)
        for row in rows:
            message = await self.telegram.send(lead_text(row), lead_buttons(row["ID"]), chat_id=chat_id)
            await asyncio.to_thread(base.update_fields, SHEET, int(row["_row"]),
                                    {"Сообщение TG": str(message.get("message_id", ""))})
        if not force:
            await asyncio.to_thread(self.registry_save, key, "sent", "Маркетинг: дневная подборка")
        return {"posted": len(rows)}

    def decide(self, lead_id: str, decision: str, who: str) -> Optional[Dict[str, str]]:
        base = self._base()
        row = base.find(SHEET, "ID", lead_id)
        if not row:
            return None
        if decision == "take":
            company_id = new_id("C")
            base.append("Компании", {
                "ID": company_id, "Название": row.get("Заголовок", "")[:150], "Тип": "Проект",
                "Направления": row.get("Регион", ""), "Что делают": row.get("Категории", ""),
                "Источник": row.get("Ссылка", ""), "Приоритет": "Высокий", "Статус": "Новый",
                "Ответственный": "Менеджер по продажам", "Добавлено": stamp(), "Добавил": f"Маркетинг ({who})",
                "Заметки": f"Компании в новости: {row.get('Компании') or '—'}",
            })
            base.create_task(f"Найти заказчика/генподрядчика и контакт: {row.get('Заголовок', '')[:100]}",
                             agent=AGENT, assignee="Менеджер по продажам",
                             due=(now_local() + timedelta(days=3)).strftime("%Y-%m-%d"), link=row.get("Ссылка", ""))
            status = "В работе"
        else:
            status = "Пропущен"
        base.update_fields(SHEET, int(row["_row"]), {"Статус": status, "Заметки": f"{who}, {stamp()}"})
        base.log(AGENT, f"Лид: {status}", row.get("Заголовок", "")[:200], lead_id, who)
        row["Статус"] = status
        return row

    async def handle_callback(self, callback: Dict[str, Any]) -> Dict[str, Any]:
        parsed = parse_lead_callback(callback.get("data", ""))
        if not parsed:
            return {"handled": False}
        message = callback.get("message") or {}
        user = callback.get("from") or {}
        who = " ".join(x for x in [user.get("first_name"), user.get("last_name")] if x) or user.get("username") or ""
        row = await asyncio.to_thread(self.decide, parsed["lead_id"], parsed["decision"], who)
        if row is None:
            await self.telegram.answer_callback(callback["id"], "Лид не найден")
            return {"handled": False}
        mark = "✅ В работе" if row["Статус"] == "В работе" else "✖ Пропущен"
        await self.telegram.edit(message["chat"]["id"], message["message_id"],
                                 f"{mark} · {html.escape(who)}\n\n{lead_text(row)}")
        await self.telegram.answer_callback(callback["id"], row["Статус"])
        return {"handled": True, "lead": parsed["lead_id"], "status": row["Статус"]}

    async def tick(self, force: bool = False) -> Dict[str, Any]:
        now_mono = time.monotonic()
        if not force and self._last_fetch is not None and now_mono - self._last_fetch < self.fetch_minutes * 60:
            return {"skipped": "not_due"}
        if self._lock.locked():
            return {"skipped": "busy"}
        async with self._lock:
            self._last_fetch = now_mono
            result: Dict[str, Any] = {"time": stamp(), "errors": []}
            try:
                result["collect"] = await asyncio.wait_for(self.collect(), timeout=180)
            except Exception as exc:
                result["errors"].append(f"collect: {exc!r}")
                logger.exception("Marketing collect failed")
            if now_local().hour >= self.digest_hour and now_local().weekday() < 6:
                try:
                    result["digest"] = await self.post_digest()
                except Exception as exc:
                    result["errors"].append(f"digest: {exc!r}")
                    logger.exception("Marketing digest failed")
            self.last_result = result
            return result
