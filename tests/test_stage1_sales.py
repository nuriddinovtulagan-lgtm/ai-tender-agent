"""Stage 1 tests: Sales agent on the in-memory spreadsheet (no Google, no SMTP)."""

from datetime import timedelta

from trans_ocean.base import Base, now_local
from trans_ocean.sales import (SalesAgent, SalesSettings, choose_template, first_email,
                               letter_notes, parse_letter, pick_companies)
from tests.test_stage0 import FakeSpreadsheet, FakeWorksheet


class FakeMailer:
    def __init__(self, configured=True, fail_for=()):
        self._configured = configured
        self.fail_for = set(fail_for)
        self.sent = []

    @property
    def configured(self):
        return self._configured

    def send(self, to, subject, body):
        if to in self.fail_for:
            raise RuntimeError("smtp down")
        self.sent.append((to, subject, body))
        return f"<id{len(self.sent)}@mail.ru>"


def make_base():
    carriers = FakeWorksheet("Международные перевозчики", [
        ["Компания", "Страна базы", "E-mail", "Приоритет", "Статус"],
        ["MARTIN Spedition", "Германия", "info@martin-spedition.com", "Высокий", "Новый"],
        ["SAMCOM", "Россия / Казахстан", "contact@samcom.ru", "Средний", "Новый"],
        ["Soncotra", "Бельгия", "Форма на сайте", "Средний", "Новый"],
        ["FXNAK Lojistik", "Турция", "info@fxnak.com.tr", "Низкий", "Новый"],
        ["Old Partner", "Германия", "old@partner.de", "Высокий", "Письмо отправлено"],
    ])
    expeditors = FakeWorksheet("Экспедиторы", [
        ["Компания", "E-mail для письма", "Приоритет", "Статус"],
        ["Gebrüder Weiss Uzbekistan", "Найти на сайте", "Высокий", "Новый"],
        ["Starways Logistics", "sales@starways.uz", "Средний", "Новый"],
    ])
    projects = FakeWorksheet("Проекты", [
        ["Проект", "Регион", "Приоритет", "Статус"],
        ["Ветровая станция", "Каракалпакстан", "Высокий", "Новый"],
    ])
    base = Base(FakeSpreadsheet([carriers, expeditors, projects]))
    base.ensure_sheets()
    base.migrate_start_companies()
    return base


def agent_for(base, mailer=None, **settings):
    return SalesAgent(lambda: base, mailer or FakeMailer(), settings=SalesSettings(**settings))


def test_helpers():
    assert first_email("Info: Info@Martin-Spedition.com, tel") == "info@martin-spedition.com"
    assert first_email("Найти на сайте") == ""
    assert choose_template({"Тип": "Международный перевозчик", "Страна": "Германия"}) == "carrier_en"
    assert choose_template({"Тип": "Международный перевозчик", "Страна": "Россия / Казахстан"}) == "carrier_ru"
    assert choose_template({"Тип": "Экспедитор", "Страна": ""}) == "forwarder_ru"
    assert choose_template({"Тип": "Проект"}) is None
    notes = letter_notes("a@b.uz", "Тема письма", "Текст\n\nвторой абзац")
    assert parse_letter(notes) == ("a@b.uz", "Тема письма", "Текст\n\nвторой абзац")
    assert parse_letter("просто текст") is None


def test_pick_companies_filters_and_sorts():
    base = make_base()
    picked = pick_companies(base.records("Компании"), [], 10)
    names = [c["Название"] for c, _, _ in picked]
    assert names[0] == "MARTIN Spedition"           # high priority first
    assert set(names) == {"MARTIN Spedition", "SAMCOM", "FXNAK Lojistik", "Starways Logistics"}
    templates = {c["Название"]: t for c, _, t in picked}
    assert templates["SAMCOM"] == "carrier_ru" and templates["MARTIN Spedition"] == "carrier_en"
    assert templates["Starways Logistics"] == "forwarder_ru"


def test_prepare_creates_deal_task_and_marks_company():
    base = make_base()
    result = agent_for(base).prepare(2)
    assert result["prepared"] == 2
    tasks = [t for t in base.records("Задачи") if t["Агент"] == "Продажи"]
    assert len(tasks) == 2 and all(t["Нужно утверждение"] == "да" for t in tasks)
    martin_task = next(t for t in tasks if "MARTIN" in t["Задача"])
    to, subject, body = parse_letter(martin_task["Заметки"])
    assert to == "info@martin-spedition.com"
    assert body.startswith("Dear MARTIN Spedition team,") and "T. A. Nuriddinov" in body
    deals = base.records("Сделки")
    assert {d["Этап"] for d in deals} == {"Новый"}
    companies = {c["Название"]: c["Статус"] for c in base.records("Компании")}
    assert companies["MARTIN Spedition"] == "Письмо на утверждении"
    # running again does not offer the same companies twice
    again = agent_for(base).prepare(10)
    assert {i["company"] for i in again["items"]}.isdisjoint({i["company"] for i in result["items"]})


def test_approved_letter_is_sent_and_rejected_is_closed():
    base = make_base()
    agent = agent_for(base, follow_up_days=5)
    agent.prepare(2)
    tasks = [t for t in base.records("Задачи") if t["Агент"] == "Продажи"]
    base.set_task_status(tasks[0]["ID"], "Утверждено", "Директор")
    base.set_task_status(tasks[1]["ID"], "Отклонено", "Директор")
    result = agent.send_approved()
    assert len(result["sent"]) == 1 and len(result["closed"]) == 1
    assert len(agent.mailer.sent) == 1
    deals = {d["Компания"]: d for d in base.records("Сделки")}
    sent_company = result["sent"][0]["company"]
    assert deals[sent_company]["Этап"] == "Письмо отправлено"
    assert deals[sent_company]["Следующий шаг"] == "Повторное письмо"
    closed_company = result["closed"][0]
    assert deals[closed_company]["Этап"] == "Отказ"
    statuses = {c["Название"]: c["Статус"] for c in base.records("Компании")}
    assert statuses[sent_company] == "Письмо отправлено" and statuses[closed_company] == "Отложено"
    # nothing is sent twice
    assert agent.send_approved()["sent"] == [] and len(agent.mailer.sent) == 1


def test_no_mail_settings_keeps_letters_waiting():
    base = make_base()
    agent = agent_for(base, mailer=FakeMailer(configured=False))
    agent.prepare(1)
    task = next(t for t in base.records("Задачи") if t["Агент"] == "Продажи")
    base.set_task_status(task["ID"], "Утверждено", "Директор")
    result = agent.send_approved()
    assert result["waiting_mail_settings"] == 1 and result["sent"] == []
    assert base.find("Задачи", "ID", task["ID"])["Статус"] == "Утверждено"


def test_failed_send_is_retried_later():
    base = make_base()
    mailer = FakeMailer(fail_for={"info@martin-spedition.com"})
    agent = agent_for(base, mailer=mailer)
    agent.prepare(1)
    task = next(t for t in base.records("Задачи") if t["Агент"] == "Продажи")
    base.set_task_status(task["ID"], "Утверждено", "Директор")
    assert len(agent.send_approved()["failed"]) == 1
    mailer.fail_for.clear()
    assert len(agent.send_approved()["sent"]) == 1


def test_followup_after_due_date():
    base = make_base()
    agent = agent_for(base, follow_up_days=5)
    agent.prepare(1)
    task = next(t for t in base.records("Задачи") if t["Агент"] == "Продажи")
    base.set_task_status(task["ID"], "Утверждено", "Директор")
    agent.send_approved()
    assert agent.prepare_followups()["followups"] == 0           # not due yet
    deal = base.records("Сделки")[0]
    past = (now_local() - timedelta(days=1)).strftime("%Y-%m-%d")
    base.update_fields("Сделки", int(deal["_row"]), {"Дата следующего шага": past})
    assert agent.prepare_followups()["followups"] == 1
    follow = [t for t in base.records("Задачи") if t["Задача"].startswith("Повторное письмо")]
    assert len(follow) == 1
    to, subject, body = parse_letter(follow[0]["Заметки"])
    assert subject.startswith("Re: Your partner in Uzbekistan") and body.startswith("Dear MARTIN")
    base.set_task_status(follow[0]["ID"], "Утверждено", "Директор")
    assert agent.send_approved()["sent"][0]["followup"] is True
    assert base.records("Сделки")[0]["Следующий шаг"] == "Звонок / ждём ответа"
    assert agent.prepare_followups()["followups"] == 0
