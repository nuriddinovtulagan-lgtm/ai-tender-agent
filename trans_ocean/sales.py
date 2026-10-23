"""Sales agent (stage 1).

Flow — nothing leaves without the owner's "yes":
1. prepare(): takes companies from "Компании" (status "Новый", a real e-mail, type
   forwarder / international carrier), fills the matching letter template, opens a deal in
   "Сделки" (stage "Новый") and creates an approval task in "Задачи". The Coordinator posts
   the task to Telegram with ✅ / ❌ buttons. The letter text sits in the task's "Заметки"
   ("Кому: …", "Тема: …", blank line, body) and can be edited in the sheet before approval.
2. send_approved(): approved letters are sent from the company mailbox (SMTP), the deal moves
   to "Письмо отправлено" with a follow-up date, the company status is updated, all journaled.
   Rejected letters close the deal as "Отказ" so the company is not offered again.
3. prepare_followups(): deals with no reply by the follow-up date get one short follow-up
   letter, again through approval.
"""

from __future__ import annotations

import asyncio
import logging
import re
import smtplib
import ssl
from dataclasses import dataclass
from datetime import timedelta
from email.message import EmailMessage
from email.utils import formataddr, make_msgid
from typing import Any, Callable, Dict, List, Optional, Tuple

from .base import Base, now_local, parse_date, stamp, new_id
from .sales_templates import EN_GREETING, RU_GREETING, TEMPLATES

logger = logging.getLogger("trans_ocean.sales")

AGENT = "Продажи"
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
DEAL_RE = re.compile(r"\bD-\d{6}-[0-9a-f]{4}\b")
SALES_TYPES = {"экспедитор", "международный перевозчик"}
# Countries whose companies get the Russian letter; everything else gets English.
RU_COUNTRY_HINTS = ("росс", "казах", "узбек", "кыргыз", "таджик", "туркмен", "беларус", "снг")
PRIORITY_ORDER = {"высокий": 0, "средний": 1, "низкий": 2}
LETTER_PREFIX = "Письмо: "
FOLLOWUP_PREFIX = "Повторное письмо: "


@dataclass
class SalesSettings:
    auto_prepare: bool = False      # prepare a batch every day by itself
    daily_limit: int = 5            # letters prepared per day
    prepare_hour: int = 10          # local hour for the daily batch
    follow_up_days: int = 5
    tick_minutes: int = 5


class Mailer:
    """Plain SMTP over SSL (mail.ru: smtp.mail.ru:465 with an app password)."""

    def __init__(self, host: str, port: int, user: str, password: str,
                 sender_name: str = "Trans Ocean Logistics", bcc_self: bool = True, timeout: float = 30):
        self.host, self.port, self.user, self.password = host, port, user, password
        self.sender_name, self.bcc_self, self.timeout = sender_name, bcc_self, timeout

    @property
    def configured(self) -> bool:
        return bool(self.host and self.user and self.password)

    def build(self, to: str, subject: str, body: str) -> EmailMessage:
        msg = EmailMessage()
        msg["From"] = formataddr((self.sender_name, self.user))
        msg["To"] = to
        msg["Subject"] = subject
        msg["Reply-To"] = self.user
        msg["Message-ID"] = make_msgid(domain=self.user.split("@")[-1] or None)
        msg.set_content(body)
        return msg

    def send(self, to: str, subject: str, body: str) -> str:
        msg = self.build(to, subject, body)
        recipients = [to] + ([self.user] if self.bcc_self else [])
        context = ssl.create_default_context()
        with smtplib.SMTP_SSL(self.host, self.port, context=context, timeout=self.timeout) as smtp:
            smtp.login(self.user, self.password)
            smtp.send_message(msg, from_addr=self.user, to_addrs=recipients)
        return msg["Message-ID"]


# ---------------------------------------------------------------- pure helpers

def first_email(text: str) -> str:
    match = EMAIL_RE.search(text or "")
    return match.group(0).lower() if match else ""


def is_russian_country(country: str) -> bool:
    low = (country or "").lower()
    return any(hint in low for hint in RU_COUNTRY_HINTS)


def choose_template(company: Dict[str, str]) -> Optional[str]:
    kind = (company.get("Тип") or "").strip().lower()
    if kind not in SALES_TYPES:
        return None
    if kind == "экспедитор":
        # Forwarders with a Tashkent office: local staff, Russian letter.
        return "forwarder_ru"
    return "carrier_ru" if is_russian_country(company.get("Страна", "")) else "carrier_en"


def language_of(template_key: str) -> str:
    return "en" if template_key.endswith("_en") else "ru"


def greeting_for(template_key: str, company_name: str) -> str:
    if language_of(template_key) == "en":
        return EN_GREETING.format(company=company_name.strip() or "Logistics")
    return RU_GREETING


def render(template_key: str, company_name: str, subject: str = "") -> Tuple[str, str]:
    subj, body = TEMPLATES[template_key]
    return (subj.format(subject=subject),
            body.format(greeting=greeting_for(template_key, company_name)))


def letter_notes(to: str, subject: str, body: str) -> str:
    return f"Кому: {to}\nТема: {subject}\n\n{body}"


def parse_letter(notes: str) -> Optional[Tuple[str, str, str]]:
    """'Кому: …\\nТема: …\\n\\nbody' -> (to, subject, body); None if the shape is broken."""
    text = (notes or "").replace("\r\n", "\n")
    head, sep, body = text.partition("\n\n")
    if not sep:
        return None
    fields = {}
    for line in head.split("\n"):
        key, _, value = line.partition(":")
        fields[key.strip().lower()] = value.strip()
    to = first_email(fields.get("кому", ""))
    subject = fields.get("тема", "")
    if not to or not subject or not body.strip():
        return None
    return to, subject, body.strip()


def pick_companies(companies: List[Dict[str, str]], deals: List[Dict[str, str]],
                   limit: int) -> List[Tuple[Dict[str, str], str, str]]:
    """Companies to write to now -> [(company, email, template_key)], best priority first."""
    in_deals = {(d.get("Компания") or "").strip().lower() for d in deals}
    seen_emails = {first_email(d.get("Контакт", "")) for d in deals} - {""}
    picked = []
    for company in companies:
        name = (company.get("Название") or "").strip()
        status = (company.get("Статус") or "Новый").strip()
        if not name or status not in {"", "Новый"} or name.lower() in in_deals:
            continue
        template = choose_template(company)
        email = first_email(company.get("E-mail", ""))
        if not template or not email or email in seen_emails:
            continue
        seen_emails.add(email)
        picked.append((company, email, template))
    picked.sort(key=lambda x: PRIORITY_ORDER.get((x[0].get("Приоритет") or "").strip().lower(), 3))
    return picked[:max(0, limit)]


# ---------------------------------------------------------------- agent

class SalesAgent:
    def __init__(self, base_factory: Callable[[], Optional[Base]], mailer: Mailer,
                 notify: Optional[Callable[[str], Any]] = None,
                 registry_load: Optional[Callable[[], Dict[str, str]]] = None,
                 registry_save: Optional[Callable[[str, str, str], None]] = None,
                 settings: Optional[SalesSettings] = None):
        self.base_factory = base_factory
        self.mailer = mailer
        self.notify = notify
        self.registry_load = registry_load or (lambda: {})
        self.registry_save = registry_save or (lambda k, v, d: None)
        self.settings = settings or SalesSettings()
        self._lock = asyncio.Lock()
        self._last_tick_mono: Optional[float] = None
        self._mail_warned = False
        self.last_result: Dict[str, Any] = {}

    def _base(self) -> Base:
        base = self.base_factory()
        if base is None:
            raise RuntimeError("Google Sheets не настроен")
        return base

    # ---------- 1. prepare first letters ----------
    def prepare(self, limit: Optional[int] = None) -> Dict[str, Any]:
        limit = self.settings.daily_limit if limit is None else limit
        base = self._base()
        companies = base.records("Компании")
        deals = base.records("Сделки")
        prepared = []
        for company, email, template in pick_companies(companies, deals, limit):
            name = company["Название"].strip()
            subject, body = render(template, name)
            deal_id = new_id("D")
            base.append("Сделки", {
                "ID": deal_id, "Компания": name, "Контакт": email, "Этап": "Новый",
                "Услуга / маршрут": "Перевозки по Узбекистану",
                "Дата последнего касания": "", "Следующий шаг": "Утвердить письмо",
                "Дата следующего шага": now_local().strftime("%Y-%m-%d"),
                "Ответственный": "Менеджер по продажам",
                "Заметки": f"шаблон: {template}; тема: {subject}",
            })
            task_id = base.create_task(
                f"{LETTER_PREFIX}{name} ({email})", agent=AGENT, assignee="Директор",
                due=now_local().strftime("%Y-%m-%d"), needs_approval=True,
                link=f"{email} · {deal_id}", notes=letter_notes(email, subject, body),
            )
            base.update_fields("Компании", int(company["_row"]), {"Статус": "Письмо на утверждении"})
            base.log(AGENT, "Письмо подготовлено", f"{name} · {email} · {template}", deal_id)
            prepared.append({"company": name, "email": email, "template": template,
                             "deal": deal_id, "task": task_id})
        return {"prepared": len(prepared), "items": prepared}

    # ---------- 3. follow-ups ----------
    def prepare_followups(self, limit: Optional[int] = None) -> Dict[str, Any]:
        limit = self.settings.daily_limit if limit is None else limit
        base = self._base()
        today = now_local().date()
        made = []
        for deal in base.records("Сделки"):
            if len(made) >= limit:
                break
            if deal.get("Этап") != "Письмо отправлено" or deal.get("Следующий шаг") != "Повторное письмо":
                continue
            due = parse_date(deal.get("Дата следующего шага"))
            email = first_email(deal.get("Контакт", ""))
            if not due or due.date() > today or not email:
                continue
            notes = deal.get("Заметки", "")
            template_match = re.search(r"шаблон:\s*(\w+)", notes)
            subject_match = re.search(r"тема:\s*([^;\n]+)", notes)
            lang = language_of(template_match.group(1)) if template_match else "ru"
            key = f"followup_{lang}"
            subject, body = render(key, deal.get("Компания", ""), subject_match.group(1).strip() if subject_match else "")
            base.create_task(
                f"{FOLLOWUP_PREFIX}{deal.get('Компания')} ({email})", agent=AGENT, assignee="Директор",
                due=today.strftime("%Y-%m-%d"), needs_approval=True,
                link=f"{email} · {deal.get('ID')}", notes=letter_notes(email, subject, body),
            )
            base.update_fields("Сделки", int(deal["_row"]), {"Следующий шаг": "Повторное письмо на утверждении"})
            base.log(AGENT, "Повторное письмо подготовлено", deal.get("Компания", ""), deal.get("ID", ""))
            made.append(deal.get("Компания"))
        return {"followups": len(made), "companies": made}

    # ---------- 2. send approved / close rejected ----------
    def _deal_for(self, base: Base, task: Dict[str, str]) -> Optional[Dict[str, str]]:
        match = DEAL_RE.search(task.get("Ссылка", ""))
        return base.find("Сделки", "ID", match.group(0)) if match else None

    def _company_row(self, base: Base, name: str) -> Optional[Dict[str, str]]:
        name = (name or "").strip().lower()
        for company in base.records("Компании"):
            if (company.get("Название") or "").strip().lower() == name:
                return company
        return None

    def send_approved(self) -> Dict[str, Any]:
        base = self._base()
        tasks = [t for t in base.records("Задачи") if t.get("Агент") == AGENT]
        sent, failed, closed, waiting_mail = [], [], [], 0
        for task in tasks:
            status = task.get("Статус", "")
            if status == "Отклонено":
                deal = self._deal_for(base, task)
                if deal and deal.get("Этап") == "Новый":
                    base.update_fields("Сделки", int(deal["_row"]), {
                        "Этап": "Отказ", "Следующий шаг": "", "Заметки": deal.get("Заметки", "") + "; письмо отклонено владельцем"})
                    company = self._company_row(base, deal.get("Компания", ""))
                    if company:
                        base.update_fields("Компании", int(company["_row"]), {"Статус": "Отложено"})
                    base.log(AGENT, "Письмо отклонено", deal.get("Компания", ""), deal.get("ID", ""), task.get("Решил", ""))
                    closed.append(deal.get("Компания"))
                elif deal and deal.get("Следующий шаг") == "Повторное письмо на утверждении":
                    base.update_fields("Сделки", int(deal["_row"]), {"Следующий шаг": "Ждём ответа"})
                    closed.append(deal.get("Компания"))
                continue
            if status != "Утверждено":
                continue
            if not self.mailer.configured:
                waiting_mail += 1
                continue
            letter = parse_letter(task.get("Заметки", ""))
            if not letter:
                base.update_fields("Задачи", int(task["_row"]), {"Статус": "В работе"})
                base.log(AGENT, "Ошибка: письмо в задаче повреждено", task.get("Задача", ""), task.get("ID", ""))
                failed.append({"task": task.get("ID"), "error": "bad letter format"})
                continue
            to, subject, body = letter
            try:
                message_id = self.mailer.send(to, subject, body)
            except Exception as exc:
                logger.exception("Sending failed for %s", to)
                base.log(AGENT, "Ошибка отправки письма", f"{to}: {exc}", task.get("ID", ""))
                failed.append({"task": task.get("ID"), "to": to, "error": str(exc)})
                continue
            today = now_local()
            base.update_fields("Задачи", int(task["_row"]), {"Статус": "Готово"})
            deal = self._deal_for(base, task)
            is_followup = task.get("Задача", "").startswith(FOLLOWUP_PREFIX)
            if deal:
                base.update_fields("Сделки", int(deal["_row"]), {
                    "Этап": "Письмо отправлено",
                    "Дата последнего касания": today.strftime("%Y-%m-%d"),
                    "Следующий шаг": "Звонок / ждём ответа" if is_followup else "Повторное письмо",
                    "Дата следующего шага": (today + timedelta(days=self.settings.follow_up_days)).strftime("%Y-%m-%d"),
                })
                company = self._company_row(base, deal.get("Компания", ""))
                if company:
                    base.update_fields("Компании", int(company["_row"]), {"Статус": "Письмо отправлено"})
            base.log(AGENT, "Повторное письмо отправлено" if is_followup else "Письмо отправлено",
                     f"{to} · {subject} · {message_id}", deal.get("ID", "") if deal else task.get("ID", ""),
                     task.get("Решил", ""))
            sent.append({"to": to, "company": deal.get("Компания") if deal else "", "followup": is_followup})
        return {"sent": sent, "failed": failed, "closed": closed, "waiting_mail_settings": waiting_mail}

    # ---------- scheduler ----------
    async def _notify(self, text: str) -> None:
        if not self.notify:
            return
        try:
            result = self.notify(text)
            if asyncio.iscoroutine(result):
                await result
        except Exception:
            logger.exception("Sales notify failed")

    async def run_send(self) -> Dict[str, Any]:
        result = await asyncio.to_thread(self.send_approved)
        for item in result["sent"]:
            kind = "Повторное письмо" if item["followup"] else "Письмо"
            await self._notify(f"📧 {kind} отправлено: <b>{item['company'] or item['to']}</b> ({item['to']})")
        for item in result["failed"]:
            await self._notify(f"⚠️ Письмо не отправлено ({item.get('to', item.get('task'))}): {item['error'][:300]}")
        if result["waiting_mail_settings"] and not self._mail_warned:
            self._mail_warned = True
            await self._notify("⚠️ Есть утверждённые письма, но почта для отправки не настроена "
                               "(MAIL_USER и MAIL_APP_PASSWORD в Render). Письма ждут.")
        return result

    async def tick(self, force: bool = False) -> Dict[str, Any]:
        import time
        now_mono = time.monotonic()
        if not force and self._last_tick_mono is not None and now_mono - self._last_tick_mono < self.settings.tick_minutes * 60:
            return {"skipped": "not_due"}
        if self._lock.locked():
            return {"skipped": "busy"}
        async with self._lock:
            self._last_tick_mono = now_mono
            result: Dict[str, Any] = {"time": stamp(), "errors": []}
            try:
                result["send"] = await self.run_send()
            except Exception as exc:
                result["errors"].append(f"send: {exc}")
                logger.exception("Sales send failed")
            now = now_local()
            key = f"sales:prepared:{now.strftime('%Y-%m-%d')}"
            if self.settings.auto_prepare and now.hour >= self.settings.prepare_hour and now.weekday() < 5:
                try:
                    registry = await asyncio.to_thread(self.registry_load)
                    if key not in registry:
                        prepared = await asyncio.to_thread(self.prepare)
                        followups = await asyncio.to_thread(self.prepare_followups)
                        await asyncio.to_thread(self.registry_save, key, "done", "Агент Продаж: дневная подготовка писем")
                        result["prepared"], result["followups"] = prepared, followups
                except Exception as exc:
                    result["errors"].append(f"prepare: {exc}")
                    logger.exception("Sales prepare failed")
            self.last_result = result
            return result
