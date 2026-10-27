"""Shared Google Sheets base for all Trans Ocean agents.

Every agent reads and writes only through this module. The sheet "Тендеры"
is owned by the tender bot (main_v2.py) and is not touched here.
Rows are always written positionally in SCHEMA order; headers are matched by name
when reading, so a person may reorder nothing but add notes freely.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional

logger = logging.getLogger("trans_ocean.base")

TZ = timezone(timedelta(hours=5))

# Sheet name -> header row. Order matters: rows are written in this order.
SCHEMA: Dict[str, List[str]] = {
    "Компании": [
        "ID", "Название", "Тип", "Страна", "Направления", "Что делают",
        "Телефон", "E-mail", "Сайт", "Приоритет", "Источник", "Статус",
        "Ответственный", "Добавлено", "Добавил", "Заметки",
    ],
    "Контакты": [
        "ID", "Компания", "Имя", "Должность", "Телефон", "E-mail", "Telegram",
        "Язык", "Источник", "Добавлено", "Заметки",
    ],
    "Сделки": [
        "ID", "Компания", "Контакт", "Этап", "Услуга / маршрут", "Сумма (UZS)",
        "Дата последнего касания", "Следующий шаг", "Дата следующего шага",
        "Ответственный", "Заметки",
    ],
    "Ставки рынка": [
        "Дата", "Откуда", "Куда", "Тип машины", "Тоннаж (т)", "Ставка",
        "Валюта", "Источник", "Ссылка", "Добавил",
    ],
    "Расчёты цены": [
        "ID", "Дата", "Для (тендер / сделка)", "Маршрут", "Тип машины",
        "Своя / наёмная", "Топливо (UZS)", "Водитель (UZS)", "Прочие расходы (UZS)",
        "Себестоимость (UZS)", "Маржа (%)", "Минимальная цена (UZS)",
        "Итоговая цена (UZS)", "Статус", "Утвердил",
    ],
    "Перевозчики": [
        "ID", "Владелец / компания", "Телефон", "Telegram", "Тип машины",
        "Тоннаж (т)", "Госномер", "Маршруты", "Документы проверены",
        "Рейтинг (1-5)", "Рейсов с нами", "Последний рейс", "Заметки",
    ],
    "Документы": [
        "Документ", "Тип", "Номер", "Дата выдачи", "Действует до",
        "Ссылка на файл", "Ответственный", "Заметки",
    ],
    "Задачи": [
        "ID", "Создано", "Задача", "Агент", "Ответственный", "Срок",
        "Нужно утверждение", "Статус", "Ссылка", "Решил", "Дата решения",
        "Сообщение TG", "Заметки",
    ],
    "Журнал": ["Время", "Агент", "Действие", "Детали", "Связано с", "Утвердил"],
    # Stage 2 (Economist): filled by the accountant, read by the agent.
    "Параметры машин": [
        "Тип машины", "Топливо (л/100 км)", "Цена топлива (сум/л)", "Водитель (сум/км)",
        "Командировочные (сум/день)", "Постоянные расходы (сум/мес)", "Рабочих дней в месяц",
        "Км в день", "Прочие расходы на рейс (сум)", "Обратный порожний пробег (%)", "Заметки",
    ],
    "Параметры цены": ["Параметр", "Значение", "Описание"],
    "Расстояния": ["Откуда", "Куда", "Км", "Заметки"],
    # Stage 3 (Document specialist): company requisites used in every tender package.
    "Реквизиты компании": ["Параметр", "Значение", "Описание"],
    # Stage 5 (Marketing): projects found in the news.
    "Лиды": ["ID", "Дата", "Заголовок", "Ссылка", "Источник", "Баллы", "Категории", "Регион",
             "Компании", "Статус", "Сообщение TG", "Заметки"],
    # Stage 6 (Dispatcher + Price agent): ads read from freight Telegram groups.
    "Объявления": ["Дата", "Тип", "Откуда", "Куда", "Тип машины", "Тоннаж (т)", "Цена", "Валюта",
                   "Телефон", "Контакт TG", "Группа", "Ссылка", "Текст", "Ключ"],
}

DEAL_STAGES = ["Новый", "Ищем контакт", "Письмо отправлено", "Ответили",
               "КП отправлено", "Переговоры", "Договор", "Отказ"]
TASK_STATUSES = ["Новая", "В работе", "Ждёт утверждения", "Утверждено",
                 "Отклонено", "Готово"]
OPEN_TASK_STATUSES = {"Новая", "В работе", "Ждёт утверждения"}

# Start sheets from Trans_Ocean_baza_klientov.xlsx -> company type in "Компании".
START_SHEETS = {
    "Проекты": "Проект",
    "Экспедиторы": "Экспедитор",
    "Международные перевозчики": "Международный перевозчик",
    "Перевозчики-партнёры": "Партнёр",
}

# Column of "Компании" -> header names to look for in the start sheets, first match wins.
START_SHEET_COLUMNS = {
    "Название": ["Компания", "Проект"],
    "Страна": ["Страна базы", "Регион"],
    "Направления": ["Направление в Узбекистан", "Направления / что делают", "Отрасль"],
    "Что делают": ["Что возят", "Какие грузы вероятны", "Что известно", "Чем полезна вам"],
    "Телефон": ["Телефон", "Телефон / e-mail"],
    "E-mail": ["E-mail", "E-mail для письма"],
    "Сайт": ["Сайт"],
    "Приоритет": ["Приоритет"],
    "Источник": ["Источник"],
    "Заметки": ["Чем полезны вам", "Чем полезна вам", "Генподрядчик", "Инвестор / заказчик"],
}


def now_local() -> datetime:
    return datetime.now(TZ)


def stamp() -> str:
    return now_local().strftime("%Y-%m-%d %H:%M")


def new_id(prefix: str) -> str:
    return f"{prefix}-{now_local().strftime('%y%m%d')}-{uuid.uuid4().hex[:4]}"


def parse_date(value: Any) -> Optional[datetime]:
    text = str(value or "").strip()
    if not text:
        return None
    for candidate in (text, text[:16], text[:10]):
        for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d", "%d.%m.%Y %H:%M", "%d.%m.%Y", "%d/%m/%Y"):
            try:
                return datetime.strptime(candidate, fmt).replace(tzinfo=TZ)
            except ValueError:
                continue
    return None


class Base:
    """Thin wrapper over a gspread Spreadsheet (or a test double with the same methods)."""

    def __init__(self, spreadsheet: Any):
        self.spreadsheet = spreadsheet
        self._ws_cache: Dict[str, Any] = {}

    # ---------- sheets ----------
    def worksheet(self, name: str) -> Any:
        if name not in self._ws_cache:
            self._ws_cache[name] = self.spreadsheet.worksheet(name)
        return self._ws_cache[name]

    def existing_titles(self) -> List[str]:
        return [ws.title for ws in self.spreadsheet.worksheets()]

    def ensure_sheets(self) -> Dict[str, str]:
        """Create missing sheets with headers. Existing sheets keep their data;
        an empty header row is filled, a different one is left alone and reported."""
        report: Dict[str, str] = {}
        titles = set(self.existing_titles())
        for name, headers in SCHEMA.items():
            if name not in titles:
                ws = self.spreadsheet.add_worksheet(title=name, rows=1000, cols=max(len(headers), 10))
                ws.append_row(headers, value_input_option="USER_ENTERED")
                try:
                    ws.freeze(rows=1)
                except Exception:
                    pass
                self._ws_cache[name] = ws
                report[name] = "создан"
                continue
            ws = self.worksheet(name)
            first = ws.row_values(1)
            if not any(str(v).strip() for v in first):
                ws.update("A1", [headers])
                report[name] = "добавлены заголовки"
            elif [str(v).strip() for v in first[:len(headers)]] != headers:
                report[name] = "заголовки отличаются, не трогали"
            else:
                report[name] = "ок"
        return report

    # ---------- rows ----------
    def records(self, name: str) -> List[Dict[str, str]]:
        """All rows as dicts keyed by header name, with the sheet row number in '_row'."""
        values = self.worksheet(name).get_all_values()
        if not values:
            return []
        headers = [str(h).strip() for h in values[0]]
        out = []
        for index, row in enumerate(values[1:], start=2):
            if not any(str(c).strip() for c in row):
                continue
            record = {h: (row[i].strip() if i < len(row) else "") for i, h in enumerate(headers) if h}
            record["_row"] = str(index)
            out.append(record)
        return out

    def append(self, name: str, data: Dict[str, Any]) -> None:
        headers = SCHEMA[name]
        row = ["" if data.get(h) is None else data.get(h) for h in headers]
        self.worksheet(name).append_row(row, value_input_option="USER_ENTERED")

    def append_many(self, name: str, rows: Iterable[Dict[str, Any]]) -> int:
        headers = SCHEMA[name]
        values = [["" if r.get(h) is None else r.get(h) for h in headers] for r in rows]
        if values:
            self.worksheet(name).append_rows(values, value_input_option="USER_ENTERED")
        return len(values)

    def update_fields(self, name: str, row_number: int, data: Dict[str, Any]) -> None:
        headers = SCHEMA[name]
        ws = self.worksheet(name)
        for key, value in data.items():
            if key in headers:
                ws.update_cell(row_number, headers.index(key) + 1, value)

    def find(self, name: str, column: str, value: str) -> Optional[Dict[str, str]]:
        for record in self.records(name):
            if record.get(column, "") == value:
                return record
        return None

    # ---------- journal ----------
    def log(self, agent: str, action: str, details: str = "", related: str = "",
            approved_by: str = "") -> None:
        try:
            self.append("Журнал", {
                "Время": stamp(), "Агент": agent, "Действие": action,
                "Детали": details[:1000], "Связано с": related, "Утвердил": approved_by,
            })
        except Exception:
            logger.exception("Journal write failed")

    # ---------- tasks ----------
    def create_task(self, title: str, agent: str = "Координатор", assignee: str = "",
                    due: str = "", needs_approval: bool = False, link: str = "",
                    notes: str = "") -> str:
        task_id = new_id("T")
        self.append("Задачи", {
            "ID": task_id, "Создано": stamp(), "Задача": title, "Агент": agent,
            "Ответственный": assignee, "Срок": due,
            "Нужно утверждение": "да" if needs_approval else "нет",
            "Статус": "Новая", "Ссылка": link, "Заметки": notes,
        })
        self.log(agent, "Создана задача", title, task_id)
        return task_id

    def set_task_status(self, task_id: str, status: str, by: str = "") -> Optional[Dict[str, str]]:
        task = self.find("Задачи", "ID", task_id)
        if not task:
            return None
        fields: Dict[str, Any] = {"Статус": status}
        if status in {"Утверждено", "Отклонено", "Готово"}:
            fields.update({"Решил": by, "Дата решения": stamp()})
        self.update_fields("Задачи", int(task["_row"]), fields)
        self.log("Координатор", f"Задача: {status}", task.get("Задача", ""), task_id, by)
        task.update({k: str(v) for k, v in fields.items()})
        return task

    # ---------- start data ----------
    def migrate_start_companies(self) -> Dict[str, int]:
        """Copy companies from the start sheets into "Компании". Safe to run again:
        names already present are skipped."""
        titles = set(self.existing_titles())
        known = {r.get("Название", "").strip().lower() for r in self.records("Компании")}
        added: Dict[str, int] = {}
        for sheet, kind in START_SHEETS.items():
            if sheet not in titles:
                continue
            rows = []
            for record in self.records(sheet):
                row = map_start_record(record, kind)
                key = row["Название"].strip().lower()
                if not key or key in known:
                    continue
                known.add(key)
                rows.append(row)
            added[sheet] = self.append_many("Компании", rows)
        if any(added.values()):
            self.log("Маркетинг", "Перенос стартовой базы компаний",
                     ", ".join(f"{k}: {v}" for k, v in added.items()))
        return added


PLACEHOLDER_RE = ("xx", "уточнить", "найти", "форма на сайте")


def is_placeholder(value: str) -> bool:
    v = (value or "").strip().lower()
    return not v or any(p in v for p in PLACEHOLDER_RE)


def import_companies(base: "Base", items: List[Dict[str, Any]], who: str = "импорт") -> Dict[str, Any]:
    """Add new companies and fill EMPTY fields of existing ones (matched by name, case-insensitive).
    Filled values are never overwritten; placeholders like "+998 78 140-XX-XX (уточнить)" count as empty.
    Notes are appended, not replaced."""
    headers = SCHEMA["Компании"]
    existing = {r.get("Название", "").strip().lower(): r for r in base.records("Компании")}
    added, updated, unchanged = [], [], []
    new_rows = []
    for item in items:
        name = str(item.get("Название", "")).strip()
        if not name:
            continue
        row = existing.get(name.lower())
        if row is None:
            data = {h: "" for h in headers}
            data.update({k: v for k, v in item.items() if k in headers})
            data.update({"ID": new_id("C"), "Статус": item.get("Статус") or "Новый",
                         "Добавлено": stamp(), "Добавил": who})
            new_rows.append(data)
            existing[name.lower()] = data
            added.append(name)
            continue
        changes: Dict[str, Any] = {}
        for key, value in item.items():
            if key not in headers or key in ("ID", "Название") or not str(value).strip():
                continue
            if key == "Заметки":
                if str(value) not in row.get("Заметки", ""):
                    changes[key] = (row.get("Заметки", "") + "; " if row.get("Заметки") else "") + str(value)
            elif is_placeholder(row.get(key, "")):
                changes[key] = value
        if changes:
            base.update_fields("Компании", int(row["_row"]), changes)
            updated.append(name)
        else:
            unchanged.append(name)
    if new_rows:
        base.append_many("Компании", new_rows)
    base.log("Маркетинг", "Импорт компаний", f"{who}: добавлено {len(added)}, обновлено {len(updated)}")
    return {"added": added, "updated": updated, "unchanged": unchanged}


def map_start_record(record: Dict[str, str], kind: str) -> Dict[str, Any]:
    row: Dict[str, Any] = {h: "" for h in SCHEMA["Компании"]}
    for target, candidates in START_SHEET_COLUMNS.items():
        for candidate in candidates:
            value = record.get(candidate, "").strip()
            if value:
                row[target] = value
                break
    row.update({
        "ID": new_id("C"), "Тип": kind,
        "Статус": record.get("Статус", "").strip() or "Новый",
        "Добавлено": stamp(), "Добавил": "стартовая база 21.10.2026",
    })
    return row
