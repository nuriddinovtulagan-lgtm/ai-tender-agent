"""Dispatcher + Price agent (stage 6): reads freight groups through a separate Telegram account.

The account (not the bot) is a member of freight groups ("Yuk Markazi", "AVTO TRASSA UZ" ...).
Every DISPATCHER_TICK_MINUTES the agent reads new messages there and parses each ad
(trans_ocean/freight_parser.py):
- every ad with a route and a price or phone   -> sheet "Объявления";
- ads with a route, truck type and price       -> sheet "Ставки рынка" (the Economist uses them in /price);
- free trucks ("bo'sh", "машина есть") with a phone or @username -> sheet "Перевозчики"
  (new carrier, or a new route added to a known one). Carriers are marked "not checked".

Nothing is ever posted to those groups and no one is contacted: the account only reads.

In the management group:
/mashina Бухара Ташкент [тент] — free trucks in Bukhara (last 3 days) and known carriers
/stavka Ташкент Нукус [реф]    — market rates for the route (last 30 days)
/rynok                         — what was read today
Once a day (DISPATCHER_DIGEST_HOUR) the same summary as /rynok is posted.

Login (once): /docs -> POST /dispatcher/login/start (phone) -> code arrives in that account's Telegram ->
POST /dispatcher/login/finish (code) -> the session string goes to Render as TG_SESSION.
"""

from __future__ import annotations

import asyncio
import html
import json
import logging
import re
import statistics
import time
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Callable, Dict, List, Optional, Tuple

from .base import TZ, Base, new_id, now_local, parse_date, stamp
from .freight_parser import KIND_CARGO, KIND_TRUCK, Ad, ad_key, find_places, find_vehicle, parse_ad, split_ads
from .market_people import GROUPS_SHEET, SENDERS_SHEET, TYPE_DISPATCHER, SenderBook, groups_text

logger = logging.getLogger("trans_ocean.dispatcher")
AGENT = "Диспетчер"
PRICE_AGENT = "Ценовой"
ADS_SHEET = "Объявления"
RATES_SHEET = "Ставки рынка"
CARRIERS_SHEET = "Перевозчики"
REGISTRY_KEY = "dispatcher:last_ids"
STATS_KEY = "dispatcher:stats"            # group id -> [oldest message id counted in "Диспетчеры рынка", its date]
BACKFILL_KEY = "dispatcher:backfill_done"

HELP = (
    "🚛 <b>Диспетчер и Ценовой</b>\n\n"
    "<code>/mashina Бухара Ташкент</code> — свободные машины в Бухаре (за 3 дня) и проверенные перевозчики\n"
    "<code>/mashina Ташкент трал</code> — с типом машины\n"
    "<code>/stavka Ташкент Нукус тент</code> — ставки рынка по маршруту за 30 дней\n"
    "<code>/rynok</code> — что прочитано в группах сегодня\n"
    "<code>/gruppy</code> — сколько в группах участников, сколько пишут и сколько молчат (в основном водители)\n\n"
    "Данные — из Telegram-групп перевозчиков. Перевозчики из объявлений не проверены: "
    "документы и машину проверяет диспетчер."
)


@dataclass
class Message:
    id: int
    date: datetime
    text: str
    group: str
    link: str = ""
    sender_name: str = ""
    sender_username: str = ""
    sender_id: int = 0


@dataclass
class Settings:
    tick_minutes: int = 15
    digest_hour: int = 18
    max_per_group: int = 300
    first_read_hours: int = 24
    groups_filter: Tuple[str, ...] = ()
    max_carrier_updates: int = 15
    backfill_days: int = 7
    backfill_per_group: int = 4000


# ---------------------------------------------------------------- Telegram account (Telethon)

class NeedPassword(Exception):
    """The account has two-step verification: finish the login with the cloud password."""


class TelethonReader:
    """Reads groups as a normal Telegram user. Imported lazily: tests use a fake reader."""

    def __init__(self, api_id: str, api_hash: str, session: str = ""):
        self.api_id = (api_id or "").strip()
        self.api_hash = (api_hash or "").strip()
        self.session = (session or "").strip()
        self.client = None
        self._login_client = None
        self._phone = ""
        self._code_hash = ""
        self._groups: List[Dict[str, Any]] = []
        self._groups_at = 0.0
        self.account = ""

    @property
    def configured(self) -> bool:
        return bool(self.api_id and self.api_hash)

    def _new_client(self, session: str):
        from telethon import TelegramClient
        from telethon.sessions import StringSession
        return TelegramClient(StringSession(session), int(self.api_id), self.api_hash,
                              device_model="Trans Ocean Reader", app_version="1.0")

    async def _client(self):
        if self.client is None:
            self.client = self._new_client(self.session)
        if not self.client.is_connected():
            await self.client.connect()
        return self.client

    async def authorized(self) -> bool:
        if not (self.configured and self.session):
            return False
        client = await self._client()
        ok = await client.is_user_authorized()
        if ok and not self.account:
            me = await client.get_me()
            self.account = " ".join(x for x in [me.first_name, me.last_name] if x) or (me.username or "")
        return ok

    async def start_login(self, phone: str) -> Dict[str, Any]:
        if not self.configured:
            raise RuntimeError("TG_API_ID / TG_API_HASH не заданы в Render")
        if self._login_client is not None:
            await self._login_client.disconnect()
        self._login_client = self._new_client("")
        await self._login_client.connect()
        self._phone = phone.strip()
        sent = await self._login_client.send_code_request(self._phone)
        self._code_hash = sent.phone_code_hash
        return {"sent": True, "phone": self._phone}

    async def finish_login(self, code: str = "", password: str = "") -> Dict[str, str]:
        from telethon.errors import SessionPasswordNeededError
        client = self._login_client
        if client is None:
            raise RuntimeError("Сначала выполните /dispatcher/login/start")
        try:
            if code:
                await client.sign_in(phone=self._phone, code=code.strip(), phone_code_hash=self._code_hash)
            elif password:
                await client.sign_in(password=password)
            else:
                raise RuntimeError("Нужен код из Telegram")
        except SessionPasswordNeededError:
            if not password:
                raise NeedPassword()
            await client.sign_in(password=password)
        session = client.session.save()
        me = await client.get_me()
        if self.client is not None and self.client is not client:
            await self.client.disconnect()
        self.client, self._login_client, self.session = client, None, session
        self.account = " ".join(x for x in [me.first_name, me.last_name] if x) or (me.username or "")
        return {"session": session, "account": self.account}

    async def list_groups(self, max_age_seconds: int = 6 * 3600) -> List[Dict[str, Any]]:
        if self._groups and time.monotonic() - self._groups_at < max_age_seconds:
            return self._groups
        client = await self._client()
        groups = []
        async for dialog in client.iter_dialogs():
            if dialog.is_group or dialog.is_channel:
                groups.append({"id": dialog.id, "name": dialog.name or str(dialog.id),
                               "username": getattr(dialog.entity, "username", None) or "",
                               "entity": dialog.entity,
                               "members": await self._member_count(client, dialog.entity)})
        self._groups, self._groups_at = groups, time.monotonic()
        return groups

    @staticmethod
    async def _member_count(client, entity) -> int:
        """Number of members only — the member list itself is not read."""
        count = getattr(entity, "participants_count", None)
        if count:
            return int(count)
        try:
            return int((await client.get_participants(entity, limit=0)).total or 0)
        except Exception:
            pass
        try:
            from telethon.tl.functions.channels import GetFullChannelRequest
            full = await client(GetFullChannelRequest(entity))
            return int(full.full_chat.participants_count or 0)
        except Exception:
            return 0

    async def fetch(self, group: Dict[str, Any], min_id: int, since: Optional[datetime],
                    limit: int, max_id: int = 0) -> List[Message]:
        client = await self._client()
        out: List[Message] = []
        async for m in client.iter_messages(group.get("entity") or group["id"], limit=limit, min_id=min_id or 0,
                                            max_id=max_id or 0):
            if since and m.date and m.date < since:
                break
            text = m.message or ""
            if not text.strip():
                continue
            sender = getattr(m, "sender", None)
            name = " ".join(x for x in [getattr(sender, "first_name", None), getattr(sender, "last_name", None)] if x)
            out.append(Message(
                id=m.id, date=m.date.astimezone(TZ), text=text, group=group["name"],
                link=f"https://t.me/{group['username']}/{m.id}" if group.get("username") else "",
                sender_name=name or getattr(sender, "title", "") or "",
                sender_username=getattr(sender, "username", None) or "",
                sender_id=int(getattr(m, "sender_id", 0) or 0)))
        return out


# ---------------------------------------------------------------- pure helpers

def ad_rows(messages: List[Message], seen: set) -> List[Tuple[Ad, Message]]:
    """Parse messages, split multi-load posts, drop duplicates (same ad in several groups)."""
    out = []
    for msg in messages:
        for part in split_ads(msg.text):
            key = ad_key(part)
            if key in seen:
                continue
            ad = parse_ad(part)
            if not ad.useful:
                continue
            seen.add(key)
            out.append((ad, msg))
    return out


def message_ads(text: str) -> List[Ad]:
    """Useful ads in one message, reposts included (used to count who posts what)."""
    return [ad for ad in (parse_ad(part) for part in split_ads(text)) if ad.useful]


def ad_record(ad: Ad, msg: Message) -> Dict[str, Any]:
    return {
        "Дата": msg.date.strftime("%Y-%m-%d %H:%M"), "Тип": ad.kind, "Откуда": ad.origin, "Куда": ad.destination,
        "Тип машины": ad.vehicle, "Тоннаж (т)": ad.tonnage, "Цена": int(ad.price) if ad.price else "",
        "Валюта": ad.currency, "Телефон": ", ".join(ad.phones),
        "Контакт TG": f"@{msg.sender_username}" if msg.sender_username else "",
        "Группа": msg.group, "Ссылка": msg.link, "Текст": re.sub(r"\s+", " ", ad.text)[:300], "Ключ": ad_key(ad.text),
    }


def rate_record(ad: Ad, msg: Message) -> Dict[str, Any]:
    return {
        "Дата": msg.date.strftime("%Y-%m-%d %H:%M"), "Откуда": ad.origin, "Куда": ad.destination,
        "Тип машины": ad.vehicle, "Тоннаж (т)": ad.tonnage, "Ставка": int(ad.price), "Валюта": ad.currency,
        "Источник": f"Telegram: {msg.group}", "Ссылка": msg.link, "Добавил": PRICE_AGENT,
    }


def route_text(origin: str, destination: str) -> str:
    return f"{origin} → {destination}" if destination else origin


def merge_carriers(existing: List[Dict[str, str]], trucks: List[Tuple[Ad, Message]],
                   max_updates: int) -> Tuple[List[Dict[str, Any]], List[Tuple[int, Dict[str, Any]]]]:
    """New carrier rows and (row number, fields) updates for known ones (matched by phone or @username)."""
    by_key: Dict[str, Dict[str, Any]] = {}
    for row in existing:
        for phone in re.split(r"[,;]\s*", row.get("Телефон", "")):
            digits = re.sub(r"\D", "", phone)[-9:]
            if len(digits) == 9:
                by_key[digits] = row
        tg = row.get("Telegram", "").strip().lower()
        if tg:
            by_key[tg] = row
    new_rows: List[Dict[str, Any]] = []
    updates: Dict[int, Dict[str, Any]] = {}
    for ad, msg in trucks:
        keys = [re.sub(r"\D", "", p)[-9:] for p in ad.phones]
        if msg.sender_username:
            keys.append(f"@{msg.sender_username}".lower())
        if not keys:
            continue
        row = next((by_key[k] for k in keys if k in by_key), None)
        route = route_text(ad.origin, ad.destination)
        if row is None:
            row = {
                "ID": new_id("P"), "Владелец / компания": msg.sender_name, "Телефон": ", ".join(ad.phones),
                "Telegram": f"@{msg.sender_username}" if msg.sender_username else "", "Тип машины": ad.vehicle,
                "Тоннаж (т)": ad.tonnage, "Госномер": "", "Маршруты": route, "Документы проверены": "нет",
                "Рейтинг (1-5)": "", "Рейсов с нами": "0", "Последний рейс": "",
                "Заметки": f"Из группы «{msg.group}», {msg.date.strftime('%d.%m.%Y')}. Не проверен.",
            }
            new_rows.append(row)
            for k in keys:
                by_key[k] = row
            continue
        routes = [r.strip() for r in re.split(r";\s*", row.get("Маршруты", "")) if r.strip()]
        if route in routes:
            continue
        routes = [route] + routes
        row["Маршруты"] = "; ".join(routes[:6])
        if "_row" in row:
            number = int(row["_row"])
            if number in updates or len(updates) < max_updates:
                updates[number] = {"Маршруты": row["Маршруты"]}
    return new_rows, list(updates.items())


def _same(a: str, b: str) -> bool:
    return (a or "").strip().lower() == (b or "").strip().lower()


def _vehicle_ok(wanted: str, value: str) -> bool:
    return not wanted or wanted.lower()[:4] in (value or "").lower()


def parse_query(text: str, command: str) -> Tuple[str, str, str]:
    body = re.sub(rf"^/{command}(@\w+)?", "", (text or "").strip(), flags=re.I)
    places = [name for name, _ in find_places(body)]
    vehicle = find_vehicle(body)
    return (places[0] if places else ""), (places[1] if len(places) > 1 else ""), vehicle


def money(value: float, currency: str = "UZS") -> str:
    if currency == "USD":
        return f"${value:,.0f}".replace(",", " ")
    return f"{value:,.0f}".replace(",", " ") + " сум"


def trucks_answer(ads: List[Dict[str, str]], carriers: List[Dict[str, str]], text: str,
                  now: Optional[datetime] = None, days: int = 3, book: Optional[SenderBook] = None) -> str:
    origin, destination, vehicle = parse_query(text, "mashina")
    if not origin:
        return HELP
    now = now or now_local()
    found = []
    for row in reversed(ads):
        when = parse_date(row.get("Дата"))
        if when is None or (now - when).days > days:
            continue
        if row.get("Тип") != KIND_TRUCK or not _same(row.get("Откуда"), origin):
            continue
        if destination and row.get("Куда") and not _same(row.get("Куда"), destination):
            continue
        if not _vehicle_ok(vehicle, row.get("Тип машины", "")):
            continue
        found.append(row)
        if len(found) >= 10:
            break
    known = [c for c in carriers if origin.lower() in c.get("Маршруты", "").lower()
             and _vehicle_ok(vehicle, c.get("Тип машины", ""))]
    known.sort(key=lambda c: (c.get("Документы проверены", "").lower() != "да", -int(c.get("Рейсов с нами") or 0)))
    e = html.escape
    title = f"🚛 <b>Машины: {e(route_text(origin, destination))}</b>" + (f" · {e(vehicle)}" if vehicle else "")
    lines = [title]
    route_people = book.for_route(origin, destination, vehicle) if book else []
    if book is not None:
        lines.append(f"\n<b>Диспетчеры этого маршрута</b> — {len(route_people)} (через них быстрее всего найти машину)")
        for hits, person in route_people:
            contact = ", ".join(person["phones"][:2]) or person["username"]
            mark = "⭐" if person["posts"] >= 3 else "•"
            lines.append(f"{mark} {e(person['name'] or person['username'] or '—')} · 📞 {e(contact)}\n"
                         f"   по маршруту {hits} объявл., всего {person['posts']} · последнее {e(person['last'][5:])}")
    lines.append(f"\n<b>Свободные машины в объявлениях за {days} дня</b> — {len(found)}")
    for row in found:
        contact = row.get("Телефон") or row.get("Контакт TG") or "—"
        price = f" · {e(row['Цена'])} {e(row.get('Валюта', ''))}" if row.get("Цена") else ""
        lines.append(f"• {e(row.get('Дата', '')[5:])} · {e(route_text(row.get('Откуда', ''), row.get('Куда', '')))} · "
                     f"{e(row.get('Тип машины') or 'тип ?')}{price}\n  📞 {e(contact)} · {e(row.get('Группа', ''))}")
    lines.append(f"\n<b>Перевозчики в базе</b> — {len(known)}")
    for c in known[:5]:
        checked = "✅" if c.get("Документы проверены", "").lower() == "да" else "❔"
        lines.append(f"{checked} {e(c.get('Владелец / компания') or '—')} · {e(c.get('Тип машины') or '')} · "
                     f"📞 {e(c.get('Телефон') or c.get('Telegram') or '—')}")
    lines.append("\n❔ — не проверен: перед рейсом проверить документы и машину.")
    return "\n".join(lines)


def rates_answer(rates: List[Dict[str, str]], text: str, now: Optional[datetime] = None, days: int = 30) -> str:
    origin, destination, vehicle = parse_query(text, "stavka")
    if not (origin and destination):
        return HELP
    now = now or now_local()
    uzs, usd, last = [], [], []
    for row in rates:
        when = parse_date(row.get("Дата"))
        if when is None or (now - when).days > days:
            continue
        if not (_same(row.get("Откуда"), origin) and _same(row.get("Куда"), destination)):
            continue
        if not _vehicle_ok(vehicle, row.get("Тип машины", "")):
            continue
        try:
            value = float(str(row.get("Ставка", "")).replace(" ", "").replace(",", "."))
        except ValueError:
            continue
        (usd if row.get("Валюта", "").upper() == "USD" else uzs).append(value)
        last.append(row)
    e = html.escape
    head = f"📊 <b>Ставки: {e(origin)} → {e(destination)}</b>" + (f" · {e(vehicle)}" if vehicle else "")
    if not last:
        return head + f"\nЗа {days} дней ставок нет. Записать вручную: <code>/rate {e(origin)} {e(destination)} тент 4500000</code>"
    lines = [head, f"За {days} дней: {len(last)} ставок"]
    if uzs:
        lines.append(f"Сум: медиана <b>{money(statistics.median(uzs))}</b>, от {money(min(uzs))} до {money(max(uzs))}")
    if usd:
        lines.append(f"USD: медиана <b>{money(statistics.median(usd), 'USD')}</b> ({len(usd)} шт.)")
    lines.append("\nПоследние:")
    for row in last[-5:][::-1]:
        lines.append(f"• {e(row.get('Дата', '')[5:])} · {e(row.get('Тип машины', ''))} · "
                     f"{e(row.get('Ставка', ''))} {e(row.get('Валюта', ''))} · {e(row.get('Источник', ''))}")
    lines.append("\nЭти ставки Экономист берёт в /price для наёмной машины.")
    return "\n".join(lines)


def summary_text(ads: List[Dict[str, str]], day: str, status: Dict[str, Any],
                 book: Optional[SenderBook] = None) -> str:
    today = [r for r in ads if r.get("Дата", "").startswith(day)]
    kinds = Counter(r.get("Тип", "") for r in today)
    routes = Counter(route_text(r.get("Откуда", ""), r.get("Куда", "")) for r in today if r.get("Куда"))
    groups = Counter(r.get("Группа", "") for r in today)
    priced = [r for r in today if r.get("Цена")]
    e = html.escape
    lines = [f"📡 <b>Рынок перевозок за {e(day)}</b>",
             f"Объявлений: {len(today)} (груз ищет машину: {kinds.get(KIND_CARGO, 0)}, "
             f"свободные машины: {kinds.get(KIND_TRUCK, 0)}), с ценой: {len(priced)}"]
    if status:
        lines.append(f"Групп читается: {status.get('groups', 0)} · сообщений сегодня: {status.get('messages_today', 0)} · "
                     f"новых перевозчиков: {status.get('carriers_today', 0)}")
    if routes:
        lines.append("\n<b>Частые маршруты</b>")
        lines += [f"• {e(route)} — {count}" for route, count in routes.most_common(7)]
    if groups:
        lines.append("\n<b>Активные группы</b>")
        lines += [f"• {e(name)} — {count}" for name, count in groups.most_common(5)]
    if book is not None and book.people:
        kinds = Counter(book.kind(p) for p in book.people.values())
        lines.append(f"\nПисали в группах с начала учёта: {len(book.people)} чел., из них диспетчеров "
                     f"{kinds.get(TYPE_DISPATCHER, 0)} (лист «Диспетчеры рынка»)")
    lines.append("\n/mashina Бухара Ташкент · /stavka Ташкент Нукус тент · /gruppy")
    return "\n".join(lines)


# ---------------------------------------------------------------- agent

class DispatcherAgent:
    def __init__(self, base_factory: Callable[[], Optional[Base]], telegram, reader,
                 registry_load: Optional[Callable[[], Dict[str, str]]] = None,
                 registry_save: Optional[Callable[[str, str, str], None]] = None,
                 settings: Optional[Settings] = None):
        self.base_factory = base_factory
        self.telegram = telegram
        self.reader = reader
        self.registry_load = registry_load or (lambda: {})
        self.registry_save = registry_save or (lambda k, v, d: None)
        self.settings = settings or Settings()
        self._lock = asyncio.Lock()
        self._last_tick: Optional[float] = None
        self._last_ids: Optional[Dict[str, int]] = None
        self._seen: Optional[set] = None
        self._stats: Optional[Dict[str, list]] = None
        self.book: Optional[SenderBook] = None
        self.group_info: List[Dict[str, Any]] = []
        self._sheet_ready = False
        self.last_result: Dict[str, Any] = {}
        self.today: Dict[str, Any] = {"day": "", "messages": 0, "ads": 0, "rates": 0, "carriers": 0}
        self.groups: List[str] = []

    def _base(self) -> Base:
        base = self.base_factory()
        if base is None:
            raise RuntimeError("Google Sheets не настроен")
        if not self._sheet_ready:
            titles = set(base.existing_titles())
            if not {ADS_SHEET, SENDERS_SHEET, GROUPS_SHEET} <= titles:
                base.ensure_sheets()
            self._sheet_ready = True
        return base

    def _count(self, **values: int) -> None:
        day = now_local().strftime("%Y-%m-%d")
        if self.today["day"] != day:
            self.today = {"day": day, "messages": 0, "ads": 0, "rates": 0, "carriers": 0}
        for key, value in values.items():
            self.today[key] += value

    def _wanted(self, group: Dict[str, Any]) -> bool:
        if not self.settings.groups_filter:
            return True
        name = str(group.get("name", "")).lower()
        return any(f.lower() in name or f == str(group.get("id")) for f in self.settings.groups_filter)

    def save(self, base: Base, pairs: List[Tuple[Ad, Message]]) -> Dict[str, int]:
        if not pairs:
            return {"ads": 0, "rates": 0, "carriers_new": 0, "carriers_updated": 0}
        base.append_many(ADS_SHEET, [ad_record(ad, msg) for ad, msg in pairs])
        rates = [rate_record(ad, msg) for ad, msg in pairs if ad.is_rate]
        if rates:
            base.append_many(RATES_SHEET, rates)
        trucks = [(ad, msg) for ad, msg in pairs if ad.kind == KIND_TRUCK]
        new_rows, updates = [], []
        if trucks:
            new_rows, updates = merge_carriers(base.records(CARRIERS_SHEET), trucks, self.settings.max_carrier_updates)
            if new_rows:
                base.append_many(CARRIERS_SHEET, new_rows)
            for row_number, fields in updates:
                base.update_fields(CARRIERS_SHEET, row_number, fields)
        base.log(PRICE_AGENT, "Прочитаны группы перевозчиков",
                 f"объявлений {len(pairs)}, ставок {len(rates)}, перевозчиков новых {len(new_rows)}, "
                 f"обновлено {len(updates)}")
        return {"ads": len(pairs), "rates": len(rates), "carriers_new": len(new_rows), "carriers_updated": len(updates)}

    async def collect(self) -> Dict[str, Any]:
        if not await self.reader.authorized():
            return {"skipped": "not_logged_in"}
        base = await asyncio.to_thread(self._base)
        if self._last_ids is None:
            raw = (await asyncio.to_thread(self.registry_load)).get(REGISTRY_KEY, "")
            try:
                self._last_ids = {str(k): int(v) for k, v in json.loads(raw or "{}").items()}
            except (ValueError, TypeError):
                self._last_ids = {}
        if self._seen is None:
            rows = await asyncio.to_thread(base.records, ADS_SHEET)
            self._seen = {r.get("Ключ", "") for r in rows[-5000:] if r.get("Ключ")}
        await self._load_people(base)
        groups = [g for g in await self.reader.list_groups() if self._wanted(g)]
        self.groups = [g["name"] for g in groups]
        self.group_info = groups
        since_first = now_local() - timedelta(hours=self.settings.first_read_hours)
        messages: List[Message] = []
        errors = []
        for group in groups:
            key = str(group["id"])
            last = self._last_ids.get(key, 0)
            try:
                batch = await self.reader.fetch(group, last, None if last else since_first, self.settings.max_per_group)
            except Exception as exc:
                errors.append(f"{group['name']}: {exc}")
                if type(exc).__name__ == "FloodWaitError":
                    break
                continue
            if batch:
                self._last_ids[key] = max(m.id for m in batch)
            messages += batch
        groups_by_name = {g["name"]: g for g in groups}
        self._count_people(messages, groups_by_name)
        pairs = ad_rows(sorted(messages, key=lambda m: m.date), self._seen)
        saved = await asyncio.to_thread(self.save, base, pairs)
        await asyncio.to_thread(self.registry_save, REGISTRY_KEY, json.dumps(self._last_ids),
                                "Диспетчер: последние прочитанные сообщения по группам")
        people = await asyncio.to_thread(self._save_people, base)
        self._count(messages=len(messages), ads=saved["ads"], rates=saved["rates"], carriers=saved["carriers_new"])
        return {"groups": len(groups), "messages": len(messages), **saved, **people, "errors": errors}

    # ---------- who writes in the groups ----------
    async def _load_people(self, base: Base) -> None:
        if self.book is None:
            self.book = SenderBook(await asyncio.to_thread(base.records, SENDERS_SHEET))
        if self._stats is None:
            raw = (await asyncio.to_thread(self.registry_load)).get(STATS_KEY, "")
            try:
                self._stats = {str(k): list(v) for k, v in json.loads(raw or "{}").items()}
            except (ValueError, TypeError):
                self._stats = {}

    def _count_people(self, messages: List[Message], groups_by_name: Dict[str, Dict[str, Any]]) -> None:
        for msg in messages:
            self.book.add(msg.sender_id, msg.sender_name, msg.sender_username, msg.group, msg.date,
                          message_ads(msg.text))
            group = groups_by_name.get(msg.group)
            if group is None:
                continue
            key = str(group["id"])
            current = self._stats.get(key)
            if current is None or msg.id < int(current[0]):
                self._stats[key] = [msg.id, msg.date.strftime("%Y-%m-%d %H:%M")]

    def _save_people(self, base: Base) -> Dict[str, int]:
        if not self.book.changed:
            return {"people": len(self.book.people)}
        base.replace_rows(SENDERS_SHEET, self.book.rows())
        since = {k: v[1] for k, v in self._stats.items()}
        base.replace_rows(GROUPS_SHEET, self.book.group_rows(self.group_info, since, stamp()))
        self.registry_save(STATS_KEY, json.dumps(self._stats), "Диспетчер: с какого сообщения ведётся учёт по группам")
        self.book.changed = False
        dispatchers = sum(1 for p in self.book.people.values() if self.book.kind(p) == TYPE_DISPATCHER)
        return {"people": len(self.book.people), "dispatchers": dispatchers}

    async def backfill(self, days: Optional[int] = None) -> Dict[str, Any]:
        """Read the last `days` days once (older than what was already counted): who writes, old ads and rates."""
        if not await self.reader.authorized():
            return {"skipped": "not_logged_in"}
        days = days or self.settings.backfill_days
        base = await asyncio.to_thread(self._base)
        await self._load_people(base)
        if self._last_ids is None or self._seen is None:
            await self.collect()
        groups = [g for g in await self.reader.list_groups() if self._wanted(g)]
        self.group_info = groups
        since = now_local() - timedelta(days=days)
        messages: List[Message] = []
        errors = []
        for group in groups:
            key = str(group["id"])
            below = int(self._stats[key][0]) if key in self._stats else self._last_ids.get(key, 0) + 1
            if below <= 1:
                continue
            try:
                messages += await self.reader.fetch(group, 0, since, self.settings.backfill_per_group, max_id=below)
            except Exception as exc:
                errors.append(f"{group['name']}: {exc}")
                if type(exc).__name__ == "FloodWaitError":
                    break
        self._count_people(messages, {g["name"]: g for g in groups})
        pairs = ad_rows(sorted(messages, key=lambda m: m.date), self._seen)
        saved = await asyncio.to_thread(self.save, base, pairs)
        people = await asyncio.to_thread(self._save_people, base)
        if not errors:
            await asyncio.to_thread(self.registry_save, BACKFILL_KEY, stamp(),
                                    f"Диспетчер: прочитана история групп за {days} дней")
        return {"days": days, "messages": len(messages), **saved, **people, "errors": errors}

    # ---------- commands ----------
    def trucks(self, text: str) -> str:
        base = self._base()
        book = self.book if self.book is not None else SenderBook(base.records(SENDERS_SHEET))
        return trucks_answer(base.records(ADS_SHEET), base.records(CARRIERS_SHEET), text, book=book)

    def groups_report(self) -> str:
        return groups_text(self._base().records(GROUPS_SHEET))

    def rates(self, text: str) -> str:
        return rates_answer(self._base().records(RATES_SHEET), text)

    def summary(self, day: Optional[str] = None) -> str:
        day = day or now_local().strftime("%Y-%m-%d")
        status = {"groups": len(self.groups), "messages_today": self.today["messages"] if self.today["day"] == day else 0,
                  "carriers_today": self.today["carriers"] if self.today["day"] == day else 0}
        return summary_text(self._base().records(ADS_SHEET), day, status, self.book)

    async def post_digest(self) -> Dict[str, Any]:
        day = now_local().strftime("%Y-%m-%d")
        key = f"dispatcher:digest:{day}"
        if key in await asyncio.to_thread(self.registry_load):
            return {"posted": False, "reason": "already_today"}
        text = await asyncio.to_thread(self.summary, day)
        await self.telegram.send(text)
        await asyncio.to_thread(self.registry_save, key, "sent", "Ценовой: итоги дня по рынку")
        return {"posted": True}

    async def tick(self, force: bool = False) -> Dict[str, Any]:
        now_mono = time.monotonic()
        if not force and self._last_tick is not None and now_mono - self._last_tick < self.settings.tick_minutes * 60:
            return {"skipped": "not_due"}
        if self._lock.locked():
            return {"skipped": "busy"}
        async with self._lock:
            self._last_tick = now_mono
            result: Dict[str, Any] = {"time": stamp(), "errors": []}
            try:
                result["collect"] = await asyncio.wait_for(self.collect(), timeout=240)
            except Exception as exc:
                result["errors"].append(f"collect: {exc!r}")
                logger.exception("Dispatcher collect failed")
            if result.get("collect", {}).get("groups") and BACKFILL_KEY not in await asyncio.to_thread(self.registry_load):
                try:
                    result["backfill"] = await asyncio.wait_for(self.backfill(), timeout=1500)
                except Exception as exc:
                    result["errors"].append(f"backfill: {exc!r}")
                    logger.exception("Dispatcher backfill failed")
            now = now_local()
            if (now.hour >= self.settings.digest_hour and self.telegram.configured
                    and result.get("collect", {}).get("skipped") is None):
                try:
                    result["digest"] = await self.post_digest()
                except Exception as exc:
                    result["errors"].append(f"digest: {exc!r}")
                    logger.exception("Dispatcher digest failed")
            self.last_result = result
            return result
