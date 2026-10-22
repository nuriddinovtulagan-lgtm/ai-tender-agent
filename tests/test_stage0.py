"""Stage 0 tests on an in-memory spreadsheet (no Google, no Telegram)."""

import asyncio
from datetime import datetime, timedelta

import pytest

from trans_ocean.base import SCHEMA, TZ, Base
from trans_ocean.coordinator import Coordinator, Settings, build_digest
from trans_ocean.telegram import parse_callback


class FakeWorksheet:
    def __init__(self, title, rows=None):
        self.title = title
        self.values = [list(r) for r in (rows or [])]

    def row_values(self, n):
        return list(self.values[n - 1]) if len(self.values) >= n else []

    def append_row(self, row, value_input_option=None):
        self.values.append([str(v) for v in row])

    def append_rows(self, rows, value_input_option=None):
        for r in rows:
            self.append_row(r)

    def get_all_values(self):
        return [list(r) for r in self.values]

    def update(self, rng, values):
        assert rng == "A1"
        if not self.values:
            self.values.append([])
        self.values[0] = [str(v) for v in values[0]]

    def update_cell(self, row, col, value):
        while len(self.values) < row:
            self.values.append([])
        r = self.values[row - 1]
        while len(r) < col:
            r.append("")
        r[col - 1] = str(value)

    def freeze(self, rows=0):
        pass


class FakeSpreadsheet:
    def __init__(self, sheets):
        self.sheets = {s.title: s for s in sheets}

    def worksheets(self):
        return list(self.sheets.values())

    def worksheet(self, name):
        return self.sheets[name]

    def add_worksheet(self, title, rows, cols):
        ws = FakeWorksheet(title)
        self.sheets[title] = ws
        return ws


def start_spreadsheet():
    expeditors = FakeWorksheet("Экспедиторы", [
        ["Компания", "Тип", "Направления / что делают", "Телефон", "Сайт", "E-mail для письма", "Приоритет", "Источник", "Статус"],
        ["Gebrüder Weiss Uzbekistan", "Международный (Австрия)", "Авто FTL", "+998 90 989 51 08", "https://www.gw-world.com", "Найти на сайте", "Высокий", "src", "Новый"],
        ["CEVA Logistics Tashkent", "Международный", "Авто, ж/д", "", "", "", "Высокий", "src", "Новый"],
    ])
    intl = FakeWorksheet("Международные перевозчики", [
        ["Компания", "Страна базы", "Направление в Узбекистан", "Что возят", "E-mail", "Чем полезны вам", "Статус"],
        ["MARTIN Spedition", "Германия", "Европа → Узбекистан", "FTL, негабарит", "info@martin-spedition.com", "Доставка по регионам", "Новый"],
        ["Gebrüder Weiss", "Австрия", "Европа", "FTL", "", "", "Новый"],
        ["gebrüder weiss uzbekistan", "Австрия", "дубль", "", "", "", "Новый"],
    ])
    tenders = FakeWorksheet("Тендеры", [["ID лота", "Название"], ["1", "x"]])
    return FakeSpreadsheet([expeditors, intl, tenders])


def test_ensure_sheets_creates_missing_and_keeps_tenders():
    ss = start_spreadsheet()
    base = Base(ss)
    report = base.ensure_sheets()
    for name in SCHEMA:
        assert report[name] == "создан"
        assert ss.worksheet(name).row_values(1) == SCHEMA[name]
    assert ss.worksheet("Тендеры").get_all_values()[1] == ["1", "x"]
    assert set(Base(ss).ensure_sheets().values()) == {"ок"}


def test_migrate_start_companies_maps_and_dedupes():
    ss = start_spreadsheet()
    base = Base(ss)
    base.ensure_sheets()
    added = base.migrate_start_companies()
    assert added == {"Экспедиторы": 2, "Международные перевозчики": 2}
    companies = {c["Название"]: c for c in base.records("Компании")}
    martin = companies["MARTIN Spedition"]
    assert martin["Тип"] == "Международный перевозчик"
    assert martin["Страна"] == "Германия"
    assert martin["E-mail"] == "info@martin-spedition.com"
    assert martin["Что делают"] == "FTL, негабарит"
    assert companies["Gebrüder Weiss Uzbekistan"]["Телефон"] == "+998 90 989 51 08"
    assert base.migrate_start_companies() == {"Экспедиторы": 0, "Международные перевозчики": 0}
    assert any(r["Действие"] == "Перенос стартовой базы компаний" for r in base.records("Журнал"))


def test_task_lifecycle_and_journal():
    ss = start_spreadsheet()
    base = Base(ss)
    base.ensure_sheets()
    tid = base.create_task("Утвердить письмо MARTIN", agent="Продажи", needs_approval=True)
    task = base.find("Задачи", "ID", tid)
    assert task["Статус"] == "Новая" and task["Нужно утверждение"] == "да"
    base.set_task_status(tid, "Утверждено", "Тулаган")
    task = base.find("Задачи", "ID", tid)
    assert task["Статус"] == "Утверждено" and task["Решил"] == "Тулаган" and task["Дата решения"]
    actions = [r["Действие"] for r in base.records("Журнал")]
    assert "Создана задача" in actions and "Задача: Утверждено" in actions
    assert base.set_task_status("T-нет", "Утверждено") is None


def test_parse_callback():
    assert parse_callback("t:T-261021-ab12:ok") == {"task_id": "T-261021-ab12", "decision": "ok"}
    assert parse_callback("t:T-1:no")["decision"] == "no"
    assert parse_callback("t::ok") is None
    assert parse_callback("x:T-1:ok") is None
    assert parse_callback("") is None


def test_digest_content():
    now = datetime(2026, 10, 22, 9, 10, tzinfo=TZ)
    data = {
        "Задачи": [
            {"Задача": "Собрать пакет", "Статус": "В работе", "Срок": "2026-10-20", "Ответственный": "Аня"},
            {"Задача": "Письмо KLG", "Статус": "Ждёт утверждения", "Срок": ""},
            {"Задача": "Старое", "Статус": "Готово", "Срок": "2026-10-01"},
        ],
        "Сделки": [
            {"Компания": "MARTIN", "Этап": "Письмо отправлено", "Дата последнего касания": "2026-10-15"},
            {"Компания": "KLG", "Этап": "Письмо отправлено", "Дата последнего касания": "2026-10-21"},
            {"Компания": "EGT", "Этап": "Переговоры", "Дата следующего шага": "22.10.2026", "Следующий шаг": "звонок"},
        ],
        "Документы": [
            {"Документ": "Лицензия", "Действует до": "2026-11-01"},
            {"Документ": "Устав", "Действует до": "2030-01-01"},
        ],
        "Компании": [{"Название": "A", "Статус": "Новый"}, {"Название": "B", "Статус": "Письмо отправлено"}],
        "Перевозчики": [],
    }
    tenders = [{"title": "Перевозка труб", "deadline": now + timedelta(days=3)}]
    text = build_digest(data, tenders, Settings(), now=now)
    assert "открыто 2, просрочено 1, ждут утверждения 1" in text
    assert "Собрать пакет" in text and "Письмо KLG" in text and "Старое" not in text
    assert "активных 1" in text and "Перевозка труб · осталось 3 дн." in text
    assert "MARTIN · без ответа 7 дн." in text
    assert "KLG ·" not in text
    assert "EGT · шаг на 22.10: звонок" in text
    assert "Лицензия · осталось 10 дн." in text and "Устав" not in text
    assert "компаний 2, ещё не в работе 1" in text
    assert len(text) < 4096


class FakeTelegram:
    def __init__(self):
        self.chat_id = "-100"
        self.sent, self.edits, self.answers = [], [], []

    configured = True

    async def send(self, text, buttons=None, chat_id=None):
        self.sent.append((text, buttons))
        return {"message_id": len(self.sent)}

    async def edit(self, chat_id, message_id, text):
        self.edits.append((chat_id, message_id, text))

    async def answer_callback(self, cid, text=""):
        self.answers.append(text)


def make_coordinator(ss):
    registry = {}
    tg = FakeTelegram()
    coord = Coordinator(
        base_factory=lambda: Base(ss), telegram=tg,
        registry_load=lambda: dict(registry),
        registry_save=lambda k, v, d: registry.__setitem__(k, v),
        tenders_loader=lambda: [],
    )
    return coord, tg, registry


def test_approval_flow_end_to_end():
    ss = start_spreadsheet()
    base = Base(ss)
    base.ensure_sheets()
    tid = base.create_task("Отправить письмо MARTIN", agent="Продажи", needs_approval=True, notes="текст письма")
    base.create_task("Без утверждения")
    coord, tg, _ = make_coordinator(ss)

    posted = asyncio.run(coord.post_pending_approvals())
    assert posted == 1
    text, buttons = tg.sent[0]
    assert "Отправить письмо MARTIN" in text and "текст письма" in text
    assert buttons[0][0]["callback_data"] == f"t:{tid}:ok"
    assert Base(ss).find("Задачи", "ID", tid)["Статус"] == "Ждёт утверждения"
    assert asyncio.run(coord.post_pending_approvals()) == 0  # not posted twice

    update = {"callback_query": {"id": "cb1", "data": f"t:{tid}:ok",
                                 "from": {"first_name": "Тулаган"},
                                 "message": {"message_id": 1, "chat": {"id": -100}}}}
    result = asyncio.run(coord.handle_update(update))
    assert result == {"handled": True, "task_id": tid, "status": "Утверждено", "by": "Тулаган"}
    assert Base(ss).find("Задачи", "ID", tid)["Статус"] == "Утверждено"
    assert "Утверждено" in tg.edits[0][2]

    wrong = {"callback_query": {"id": "cb2", "data": f"t:{tid}:no", "from": {},
                                "message": {"message_id": 1, "chat": {"id": -999}}}}
    assert asyncio.run(coord.handle_update(wrong))["reason"] == "wrong_chat"
    assert Base(ss).find("Задачи", "ID", tid)["Статус"] == "Утверждено"


def test_digest_once_per_day():
    ss = start_spreadsheet()
    Base(ss).ensure_sheets()
    coord, tg, registry = make_coordinator(ss)
    assert asyncio.run(coord.send_digest())["sent"] is True
    assert asyncio.run(coord.send_digest()) == {"sent": False, "reason": "already_sent_today"}
    assert asyncio.run(coord.send_digest(force=True))["sent"] is True
    assert len(tg.sent) == 2
    assert any(r["Действие"] == "Утренняя сводка отправлена" for r in Base(ss).records("Журнал"))
