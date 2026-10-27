"""Stage 4 tests: Lawyer agent."""

from datetime import datetime
from pathlib import Path

from trans_ocean.base import Base
from trans_ocean.documents import DocumentAgent
from trans_ocean.lawyer import LawyerAgent, add_working_days, assess, late_penalty, parse_terms, pdf_text
from tests.test_stage0 import FakeSpreadsheet, FakeWorksheet
from tests.test_stage3_documents import docx_text

PDF = Path(__file__).parent / "fixtures" / "tender_selection_info.pdf"


def make():
    tenders = FakeWorksheet("Тендеры", [
        ["ID", "Номер лота", "Название", "Заказчик", "Срок окончания", "Ссылка"],
        ["518494", "518494", "TURLI XIL YUKLARNI TASHISH", '"NEFTGAZMONTAJ" MCHJ', "29.10.2026 12:01",
         "https://etender.uzex.uz/lot/518494"],
    ])
    base = Base(FakeSpreadsheet([tenders]))
    base.ensure_sheets()
    docs = DocumentAgent(lambda: base)
    docs.setup(base)
    return base, LawyerAgent(lambda: base, docs._tender_tables)


def test_parse_real_tender_pdf():
    terms = parse_terms(pdf_text(PDF.read_bytes()))
    assert terms["customer"] == '"NEFTGAZMONTAJ" MCHJ' and terms["customer_stir"] == "204993861"
    assert terms["value"] == 1792040000 and terms["end"] == "29.10.2026 12:01"
    assert (terms["tech_weight"], terms["price_weight"], terms["min_score"]) == ("50", "50", "20")
    assert terms["deposit"] == "3%" and terms["bank_guarantee"].startswith("Не принима")
    assert (terms["advance"], terms["payment_term"]) == ("15", "30 банк. куни")
    assert (terms["contractor_penalty"], terms["customer_penalty"]) == ("0,5", "0,4")
    assert len(terms["qualification"]) == 9
    risks = " ".join(assess(terms, datetime(2026, 10, 28, 9)))
    assert "53 761 200" in risks and "НЕ принимается" in risks and "меньше 3 дней" in risks


def test_analyze_pdf_message():
    base, lawyer = make()
    reply = lawyer.analyze_pdf(PDF.read_bytes(), "file_5.pdf")
    assert "Разбор тендера" in reply and "NEFTGAZMONTAJ" in reply
    assert any(r["Агент"] == "Юрист" for r in base.records("Журнал"))


def test_penalty_and_working_days():
    assert late_penalty(100_000_000, 10) == 4_000_000
    assert late_penalty(100_000_000, 500) == 50_000_000           # capped at 50%
    friday = datetime(2026, 10, 30)
    assert add_working_days(friday, 2).date() == datetime(2026, 11, 3).date()


def test_objection_and_claim_drafts():
    base, lawyer = make()
    msg, files = lawyer.objection("/etiroz 518494 Таклиф асоссиз рад этилган: малака талаблари бажарилган")
    assert "2 рабочих дня" in msg and len(files) == 1
    text = docx_text(files[0][1])
    assert "ЭЪТИРОЗ" in text and "NEFTGAZMONTAJ" in text and "асоссиз рад этилган" in text
    assert any("возражение" in t["Задача"] for t in base.records("Задачи"))
    msg, files = lawyer.claim("/pretenziya 518494 150000000 20")
    assert "12 000 000" in msg                                     # 150 mln × 0.4% × 20
    claim = docx_text(files[0][1])
    assert "ТАЛАБНОМА" in claim and "303372643" in claim
    assert "Юрист" in lawyer.claim("/pretenziya")[0]
