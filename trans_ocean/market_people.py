"""Who writes in the freight groups (stage 6b).

Drivers rarely post: they read cargo ads and call. Ads are posted mostly by dispatchers and forwarders,
and through them a truck is found fastest. So the agent counts every sender seen in the groups:
- "Диспетчеры рынка": one row per sender — posts with ads, messages, routes, truck types, phones, groups;
  type: "Диспетчер" (3+ posts with ads), "Пишет иногда" (1–2), "Только общается" (no ads), "Канал".
- "Группы перевозчиков": per group — members, how many of them wrote since tracking started,
  dispatchers among them, and the share of silent members (mostly drivers).

Only counts of members are read, never the member list: silent members are not listed anywhere.
"""

from __future__ import annotations

import re
from collections import Counter
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional, Tuple

from .freight_parser import Ad

SENDERS_SHEET = "Диспетчеры рынка"
GROUPS_SHEET = "Группы перевозчиков"

TYPE_DISPATCHER = "Диспетчер"
TYPE_SOMETIMES = "Пишет иногда"
TYPE_CHAT = "Только общается"
TYPE_CHANNEL = "Канал"
DISPATCHER_MIN_POSTS = 3

COUNTER_RE = re.compile(r"^(.*?)\s*\((\d+)\)$")


def counter_to_text(counter: Counter, limit: int = 6) -> str:
    return "; ".join(f"{name} ({count})" for name, count in counter.most_common(limit))


def text_to_counter(text: str) -> Counter:
    out: Counter = Counter()
    for part in re.split(r";\s*", text or ""):
        part = part.strip()
        if not part:
            continue
        m = COUNTER_RE.match(part)
        if m:
            out[m.group(1).strip()] += int(m.group(2))
        else:
            out[part] += 1
    return out


def _int(value: Any) -> int:
    try:
        return int(float(str(value).replace(" ", "") or 0))
    except ValueError:
        return 0


def sender_type(sender_id: int, posts: int) -> str:
    if sender_id < 0:
        return TYPE_CHANNEL
    if posts >= DISPATCHER_MIN_POSTS:
        return TYPE_DISPATCHER
    if posts > 0:
        return TYPE_SOMETIMES
    return TYPE_CHAT


class SenderBook:
    """In-memory book of senders, loaded from and saved to the sheet "Диспетчеры рынка"."""

    def __init__(self, rows: Iterable[Dict[str, str]] = ()):
        self.people: Dict[str, Dict[str, Any]] = {}
        for row in rows:
            key = str(row.get("ID TG", "")).strip()
            if not key:
                continue
            self.people[key] = {
                "id": _int(key), "name": row.get("Имя", ""), "username": row.get("Telegram", ""),
                "phones": [p.strip() for p in row.get("Телефоны", "").split(",") if p.strip()],
                "posts": _int(row.get("Объявлений")), "messages": _int(row.get("Сообщений")),
                "routes": text_to_counter(row.get("Маршруты", "")),
                "vehicles": text_to_counter(row.get("Типы машин", "")),
                "groups": [g.strip() for g in row.get("Группы", "").split(";") if g.strip()],
                "first": row.get("Первое", ""), "last": row.get("Последнее", ""),
            }
        self.changed = False

    def add(self, sender_id: int, name: str, username: str, group: str, when: datetime, ads: List[Ad]) -> None:
        if not sender_id:
            return
        key = str(sender_id)
        stamp = when.strftime("%Y-%m-%d %H:%M")
        person = self.people.get(key)
        if person is None:
            person = {"id": sender_id, "name": "", "username": "", "phones": [], "posts": 0, "messages": 0,
                      "routes": Counter(), "vehicles": Counter(), "groups": [], "first": stamp, "last": stamp}
            self.people[key] = person
        person["name"] = name or person["name"]
        if username:
            person["username"] = f"@{username}"
        person["messages"] += 1
        if group and group not in person["groups"]:
            person["groups"].append(group)
        person["first"] = min(person["first"] or stamp, stamp)
        person["last"] = max(person["last"] or stamp, stamp)
        if ads:
            person["posts"] += 1
            for ad in ads:
                if ad.origin and ad.destination:
                    person["routes"][f"{ad.origin} → {ad.destination}"] += 1
                if ad.vehicle:
                    person["vehicles"][ad.vehicle] += 1
                for phone in ad.phones:
                    if phone not in person["phones"] and len(person["phones"]) < 3:
                        person["phones"].append(phone)
        self.changed = True

    def kind(self, person: Dict[str, Any]) -> str:
        return sender_type(person["id"], person["posts"])

    def rows(self) -> List[Dict[str, Any]]:
        people = sorted(self.people.values(), key=lambda p: (-p["posts"], -p["messages"]))
        return [{
            "ID TG": p["id"], "Имя": p["name"], "Telegram": p["username"], "Телефоны": ", ".join(p["phones"]),
            "Тип": self.kind(p), "Объявлений": p["posts"], "Сообщений": p["messages"],
            "Маршруты": counter_to_text(p["routes"]), "Типы машин": counter_to_text(p["vehicles"], 3),
            "Группы": "; ".join(p["groups"]), "Первое": p["first"], "Последнее": p["last"],
        } for p in people]

    def for_route(self, origin: str, destination: str = "", vehicle: str = "", limit: int = 7) -> List[Tuple[int, Dict]]:
        """Dispatchers who post this route most often: [(posts on the route, person)]."""
        out = []
        o, d, v = origin.lower(), destination.lower(), vehicle.lower()[:4]
        for person in self.people.values():
            if person["id"] < 0 or not (person["phones"] or person["username"]):
                continue
            if v and not any(v in name.lower() for name in person["vehicles"]):
                continue
            hits = 0
            for route, count in person["routes"].items():
                start, _, end = route.lower().partition(" → ")
                if (start == o and (not d or end == d)) or (not d and end == o):
                    hits += count
            if hits:
                out.append((hits, person))
        out.sort(key=lambda x: (-x[0], -x[1]["posts"]))
        return out[:limit]

    def group_rows(self, groups: List[Dict[str, Any]], since: Dict[str, str], updated: str) -> List[Dict[str, Any]]:
        rows = []
        for group in groups:
            name = group["name"]
            writers = [p for p in self.people.values() if name in p["groups"] and p["id"] > 0]
            dispatchers = sum(1 for p in writers if self.kind(p) == TYPE_DISPATCHER)
            sometimes = sum(1 for p in writers if self.kind(p) == TYPE_SOMETIMES)
            members = int(group.get("members") or 0)
            share = round(100 * len(writers) / members, 1) if members else ""
            rows.append({
                "Группа": name,
                "Ссылка": f"https://t.me/{group['username']}" if group.get("username") else "",
                "Участников": members or "", "Писали": len(writers), "Из них диспетчеры": dispatchers,
                "Пишут иногда": sometimes, "Писали (%)": share,
                "Молчат (%)": round(100 - share, 1) if share != "" else "",
                "Учёт с": since.get(str(group["id"]), ""), "Обновлено": updated,
            })
        rows.sort(key=lambda r: -(r["Участников"] or 0))
        return rows


def groups_text(rows: List[Dict[str, Any]]) -> str:
    """Reply to /gruppy."""
    import html
    e = html.escape
    if not rows:
        return "📊 Статистики по группам пока нет: она появится после первого чтения групп."
    total_members = sum(_int(r.get("Участников")) for r in rows)
    lines = ["📊 <b>Кто есть в группах перевозчиков</b>",
             "Писали — отправили хотя бы одно сообщение с начала учёта; молчат — в основном водители.\n"]
    for r in rows:
        members = _int(r.get("Участников"))
        count = f"{members:,}".replace(",", " ") + " участн." if members else "участников не видно"
        lines.append(f"<b>{e(r.get('Группа', ''))}</b> — {count}")
        share = f", {r['Писали (%)']}%" if str(r.get("Писали (%)", "")) != "" else ""
        lines.append(f"   писали {r.get('Писали', 0)}{share} · диспетчеров {r.get('Из них диспетчеры', 0)} · "
                     f"молчат {r.get('Молчат (%)', '—')}%")
    since = min((str(r.get("Учёт с")) for r in rows if r.get("Учёт с")), default="")
    lines.append(f"\nВсего участников (с повторами между группами): {total_members:,}".replace(",", " "))
    if since:
        lines.append(f"Учёт с {e(since)}. Чем дольше учёт, тем точнее доля молчащих.")
    lines.append("Список: лист «Диспетчеры рынка» в базе.")
    return "\n".join(lines)
