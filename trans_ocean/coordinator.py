"""Coordinator agent (stage 0).

- Morning digest of the whole base to the management Telegram group, once a day.
- Tasks that need approval are posted with buttons; a click updates the base and the journal.
- Everything it does is written to the sheet "Журнал".
"""

from __future__ import annotations

import asyncio
import html
import logging
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional

from .base import OPEN_TASK_STATUSES, Base, now_local, parse_date, stamp
from .telegram import Telegram, approval_buttons, parse_callback

logger = logging.getLogger("trans_ocean.coordinator")

FOLLOW_UP_STAGES = {"Письмо отправлено", "КП отправлено", "Ответили", "Переговоры"}
ACTIVE_DEAL_STAGES = ["Ищем контакт", "Письмо отправлено", "Ответили", "КП отправлено", "Переговоры"]


@dataclass
class Settings:
    digest_hour: int = 9
    window_minutes: int = 90
    tick_minutes: int = 5
    follow_up_days: int = 5
    document_warn_days: int = 30
    max_items: int = 5


def esc(value: Any) -> str:
    return html.escape(str(value or ""))


def days_between(earlier: datetime, later: datetime) -> int:
    return (later.date() - earlier.date()).days


def build_digest(base_data: Dict[str, List[Dict[str, str]]], tenders: List[Dict[str, Any]],
                 settings: Settings, now: Optional[datetime] = None) -> str:
    """Pure function: records of each sheet + active tenders -> HTML text."""
    now = now or now_local()
    today = now.date()
    n = settings.max_items
    lines = [f"☀️ <b>Утренняя сводка Trans Ocean</b> · {now.strftime('%d.%m.%Y')}", ""]

    # Tasks
    tasks = [t for t in base_data.get("Задачи", []) if t.get("Статус", "Новая") in OPEN_TASK_STATUSES]
    overdue = [t for t in tasks if (d := parse_date(t.get("Срок"))) and d.date() < today]
    waiting = [t for t in tasks if t.get("Статус") == "Ждёт утверждения"]
    lines.append(f"📋 <b>Задачи</b>: открыто {len(tasks)}, просрочено {len(overdue)}, "
                 f"ждут утверждения {len(waiting)}")
    for t in (overdue + [w for w in waiting if w not in overdue])[:n]:
        due = f" · срок {esc(t.get('Срок'))}" if t.get("Срок") else ""
        who = f" · {esc(t.get('Ответственный'))}" if t.get("Ответственный") else ""
        lines.append(f"   • {esc(t.get('Задача'))}{due}{who}")
    lines.append("")

    # Tenders
    lines.append(f"🏷 <b>Тендеры</b>: активных {len(tenders)}")
    for t in sorted(tenders, key=lambda x: x["deadline"])[:3]:
        left = max(0, days_between(now, t["deadline"]))
        lines.append(f"   • {esc(t.get('title') or 'Без названия')} · осталось {left} дн.")
    lines.append("")

    # Deals
    deals = base_data.get("Сделки", [])
    counts = {stage: sum(1 for d in deals if d.get("Этап") == stage) for stage in ACTIVE_DEAL_STAGES}
    active_counts = ", ".join(f"{k.lower()} {v}" for k, v in counts.items() if v)
    lines.append(f"🤝 <b>Сделки</b>: {active_counts or 'активных нет'}")
    follow = []
    for d in deals:
        next_date = parse_date(d.get("Дата следующего шага"))
        last = parse_date(d.get("Дата последнего касания"))
        if next_date and next_date.date() <= today and d.get("Этап") not in {"Договор", "Отказ"}:
            follow.append((d, f"шаг на {next_date.strftime('%d.%m')}: {d.get('Следующий шаг') or 'не указан'}"))
        elif d.get("Этап") in FOLLOW_UP_STAGES and last and days_between(last, now) >= settings.follow_up_days:
            follow.append((d, f"без ответа {days_between(last, now)} дн."))
    if follow:
        lines.append(f"   Нужно повторное касание: {len(follow)}")
        for d, why in follow[:n]:
            lines.append(f"   • {esc(d.get('Компания'))} · {esc(why)}")
    lines.append("")

    # Documents
    expiring = []
    for doc in base_data.get("Документы", []):
        until = parse_date(doc.get("Действует до"))
        if until is not None:
            left = days_between(now, until)
            if left <= settings.document_warn_days:
                expiring.append((left, doc))
    expiring.sort(key=lambda x: x[0])
    if expiring:
        lines.append(f"📄 <b>Документы</b>: истекают в ближайшие {settings.document_warn_days} дн.: {len(expiring)}")
        for left, doc in expiring[:n]:
            state = "просрочен" if left < 0 else f"осталось {left} дн."
            lines.append(f"   • {esc(doc.get('Документ'))} · {state}")
        lines.append("")

    # Base
    companies = base_data.get("Компании", [])
    untouched = sum(1 for c in companies if c.get("Статус", "Новый") in {"", "Новый"})
    lines.append(f"🏢 <b>База</b>: компаний {len(companies)}, ещё не в работе {untouched}, "
                 f"перевозчиков {len(base_data.get('Перевозчики', []))}")
    return "\n".join(lines).strip()


def approval_text(task: Dict[str, str]) -> str:
    parts = [f"🟡 <b>Нужно утверждение</b>", "", f"<b>{esc(task.get('Задача'))}</b>"]
    if task.get("Агент"):
        parts.append(f"Агент: {esc(task.get('Агент'))}")
    if task.get("Ответственный"):
        parts.append(f"Ответственный: {esc(task.get('Ответственный'))}")
    if task.get("Срок"):
        parts.append(f"Срок: {esc(task.get('Срок'))}")
    if task.get("Заметки"):
        parts.append(f"\n{esc(task.get('Заметки'))[:1500]}")
    if task.get("Ссылка"):
        parts.append(f"\n🔗 {esc(task.get('Ссылка'))}")
    parts.append(f"\nID: {esc(task.get('ID'))}")
    return "\n".join(parts)


class Coordinator:
    def __init__(self, base_factory: Callable[[], Optional[Base]], telegram: Telegram,
                 registry_load: Callable[[], Dict[str, str]],
                 registry_save: Callable[[str, str, str], None],
                 tenders_loader: Callable[[], List[Dict[str, Any]]],
                 settings: Optional[Settings] = None):
        self.base_factory = base_factory
        self.telegram = telegram
        self.registry_load = registry_load
        self.registry_save = registry_save
        self.tenders_loader = tenders_loader
        self.settings = settings or Settings()
        self._last_tick: Optional[float] = None
        self._lock = asyncio.Lock()
        self.last_result: Dict[str, Any] = {}

    def _base(self) -> Base:
        base = self.base_factory()
        if base is None:
            raise RuntimeError("Google Sheets не настроен (GOOGLE_SHEET_ID / GOOGLE_SERVICE_ACCOUNT_JSON)")
        return base

    def digest_window_open(self) -> bool:
        now = now_local()
        start = now.replace(hour=self.settings.digest_hour, minute=0, second=0, microsecond=0)
        return 0 <= (now - start).total_seconds() <= self.settings.window_minutes * 60

    async def send_digest(self, force: bool = False) -> Dict[str, Any]:
        key = f"coordinator:digest:{now_local().strftime('%Y-%m-%d')}"
        registry = await asyncio.to_thread(self.registry_load)
        if not force and key in registry:
            return {"sent": False, "reason": "already_sent_today"}
        base = await asyncio.to_thread(self._base)
        sheets = ["Задачи", "Сделки", "Документы", "Компании", "Перевозчики"]
        data = {}
        for name in sheets:
            try:
                data[name] = await asyncio.to_thread(base.records, name)
            except Exception as exc:
                logger.warning("Digest: sheet %s unreadable: %s", name, exc)
                data[name] = []
        try:
            tenders = await asyncio.to_thread(self.tenders_loader)
        except Exception as exc:
            logger.warning("Digest: tenders unreadable: %s", exc)
            tenders = []
        text = build_digest(data, tenders, self.settings)
        await self.telegram.send(text)
        await asyncio.to_thread(self.registry_save, key, "sent", "Утренняя сводка Координатора")
        await asyncio.to_thread(base.log, "Координатор", "Утренняя сводка отправлена",
                                f"задач {len(data['Задачи'])}, тендеров {len(tenders)}")
        return {"sent": True, "chars": len(text)}

    async def post_pending_approvals(self) -> int:
        base = await asyncio.to_thread(self._base)
        tasks = await asyncio.to_thread(base.records, "Задачи")
        posted = 0
        for task in tasks:
            if (task.get("Нужно утверждение", "").lower() != "да"
                    or task.get("Статус", "Новая") not in {"", "Новая"}
                    or task.get("Сообщение TG")):
                continue
            if not task.get("ID"):
                continue
            message = await self.telegram.send(approval_text(task), approval_buttons(task["ID"]))
            await asyncio.to_thread(base.update_fields, "Задачи", int(task["_row"]), {
                "Статус": "Ждёт утверждения", "Сообщение TG": str(message.get("message_id", "")),
            })
            await asyncio.to_thread(base.log, "Координатор", "Отправлено на утверждение",
                                    task.get("Задача", ""), task["ID"])
            posted += 1
        return posted

    async def tick(self, force: bool = False) -> Dict[str, Any]:
        """Called by the scheduler every minute; does real work every tick_minutes."""
        if not self.telegram.configured:
            return {"skipped": "telegram_not_configured"}
        now_mono = time.monotonic()
        if not force and self._last_tick is not None and now_mono - self._last_tick < self.settings.tick_minutes * 60:
            return {"skipped": "not_due"}
        if self._lock.locked():
            return {"skipped": "busy"}
        async with self._lock:
            self._last_tick = now_mono
            result: Dict[str, Any] = {"time": stamp(), "errors": []}
            try:
                result["approvals_posted"] = await self.post_pending_approvals()
            except Exception as exc:
                result["errors"].append(f"approvals: {exc}")
                logger.exception("Coordinator approvals failed")
            if self.digest_window_open():
                try:
                    result["digest"] = await self.send_digest()
                except Exception as exc:
                    result["errors"].append(f"digest: {exc}")
                    logger.exception("Coordinator digest failed")
            self.last_result = result
            return result

    async def handle_update(self, update: Dict[str, Any]) -> Dict[str, Any]:
        callback = update.get("callback_query")
        if not callback:
            return {"handled": False}
        parsed = parse_callback(callback.get("data", ""))
        message = callback.get("message") or {}
        chat_id = str((message.get("chat") or {}).get("id", ""))
        if not parsed:
            await self.telegram.answer_callback(callback["id"], "Неизвестная кнопка")
            return {"handled": False}
        if chat_id != str(self.telegram.chat_id):
            await self.telegram.answer_callback(callback["id"], "Эта кнопка работает только в группе управления")
            return {"handled": False, "reason": "wrong_chat"}

        user = callback.get("from") or {}
        who = " ".join(x for x in [user.get("first_name"), user.get("last_name")] if x) or user.get("username") or str(user.get("id", ""))
        status = "Утверждено" if parsed["decision"] == "ok" else "Отклонено"
        base = await asyncio.to_thread(self._base)
        task = await asyncio.to_thread(base.set_task_status, parsed["task_id"], status, who)
        if task is None:
            await self.telegram.answer_callback(callback["id"], "Задача не найдена в базе")
            return {"handled": False, "reason": "task_not_found"}
        mark = "✅" if status == "Утверждено" else "❌"
        await self.telegram.edit(
            message["chat"]["id"], message["message_id"],
            f"{mark} <b>{status}</b> · {esc(who)} · {stamp()}\n\n<b>{esc(task.get('Задача'))}</b>\nID: {esc(task.get('ID'))}",
        )
        await self.telegram.answer_callback(callback["id"], status)
        return {"handled": True, "task_id": parsed["task_id"], "status": status, "by": who}
