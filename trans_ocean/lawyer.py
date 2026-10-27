"""Lawyer agent (stage 4).

- /analiz on a tender PDF from the portal (send the PDF with the caption /analiz, or reply
  /analiz to a message with the PDF): extracts the key terms — customer, value, deadline,
  evaluation method, deposit, bank guarantee, advance and payment terms, penalties,
  qualification requirements — and flags the risks for the company.
- /etiroz <лот> <причина>: draft objection (эътироз) to the procurement commission against
  the evaluation protocol; the procedure gives two working days after the protocol is posted.
- /pretenziya <лот или заказчик> <сумма> <дней>: draft claim for late payment with the
  contract penalty (0.4% per day, at most 50% of the overdue amount).
Drafts are Word files in Uzbek; a person checks and sends them. Nothing is filed by the bot.
"""

from __future__ import annotations

import html
import io
import re
from datetime import datetime, timedelta
from typing import Any, Callable, Dict, List, Optional, Tuple

from .base import Base, now_local, stamp
from .documents import REQ_SHEET, Writer, find_tender, requisites_dict

AGENT = "Юрист"
CUSTOMER_LATE_PENALTY = 0.4     # % per day, standard contract (annex 9), cap 50%
CONTRACTOR_LATE_PENALTY = 0.5   # % per day for the contractor, cap 50%

HELP = (
    "⚖️ <b>Юрист</b>\n\n"
    "📎 Отправьте PDF тендера с подписью <code>/analiz</code> (или ответьте <code>/analiz</code> на сообщение с PDF) — "
    "разбор условий и рисков\n"
    "<code>/etiroz 518494 причина</code> — черновик возражения на протокол (срок — 2 рабочих дня)\n"
    "<code>/pretenziya 518494 150000000 20</code> — претензия за просрочку оплаты: сумма долга и дней просрочки\n"
    "<code>/pravo ндс</code> — правовая база: НДС, ЭТТН, безнал, факторинг, где искать законы"
)


# ---------------------------------------------------------------- PDF text

def pdf_text(content: bytes) -> str:
    from pypdf import PdfReader
    reader = PdfReader(io.BytesIO(content))
    return "\n".join((page.extract_text() or "") for page in reader.pages)


def normalize(text: str) -> str:
    """Collapse whitespace. pypdf also splits Uzbek letters ('ра қ ами'), so labels are
    matched with flex(), which allows spaces between any two characters."""
    return re.sub(r"\s+", " ", text or "").strip()


def flex(label: str) -> str:
    out = []
    for ch in label:
        if ch == " ":
            out.append(r"\s*")
        else:
            out.append(re.escape(ch) + r"\s*")
    return "".join(out)


def _num(value: str) -> Optional[float]:
    digits = re.sub(r"[^\d.,]", "", value or "").replace(",", ".")
    try:
        return float(digits) if digits else None
    except ValueError:
        return None


def _find(pattern: str, text: str) -> str:
    m = re.search(pattern, text, flags=re.I)
    return m.group(1).strip() if m else ""


def parse_terms(raw: str) -> Dict[str, Any]:
    t = normalize(raw)
    f = flex
    terms: Dict[str, Any] = {
        "title": _find(f("Харид предмети номи:") + r"(.+?)\s+" + f("Буюртмачининг"), t),
        "customer": _find(f("Буюртмачининг номи:") + r"(.+?)\s+" + f("Буюртмачининг СТИР"), t),
        "customer_stir": _find(f("СТИР рақами:") + r"(\d{9})", t),
        "value": _num(_find(f("Танловнинг қиймати") + r"([\d\s.,]+?)\s+[^\d\s]", t)),
        "start": _find(f("Савдо бошланиш санаси:") + r"([\d.]+\s+[\d:]+)", t),
        "end": _find(f("Савдо тугаш санаси:") + r"([\d.]+\s+[\d:]+)", t),
        "funding": _find(f("Молиялаштириш манбаи:") + r"(.+?)\s+" + f("Таклиф"), t),
        "method": _find(f("баҳолаш усули:") + r"(\S+\s+\S+)", t),
        "min_score": _find(f("Мин. ўтиш балли:") + r"(\d+)", t),
        "tech_weight": _find(f("техник малака қисми:") + r"(\d+)", t),
        "price_weight": _find(f("техник нарх қисми:") + r"(\d+)", t),
        "deposit": _find(f("Закалат миқдори:") + r"([\d.,]+\s*%?)", t),
        "deposit_form": _find(f("кафолат шакли:") + r"(.+?)\s+" + f("Закалат"), t),
        "bank_guarantee": _find(f("Банкдан кафолат хати қабул қилинади:") + r"(\S+(?:\s+\S+)?)", t),
        "payment_order": _find(f("Тўлов тартиби:") + r"(\S+)", t),
        "advance": _find(f("Олдиндан тўлов миқдори :") + r"(\d+)", t),
        "advance_term": _find(f("Олдиндан тўлов муддати:") + r"(\d+\s*\S+)", t),
        "payment_term": _find(f("Тўлов муддати (тўлиқ тўлов):") + r"(\d+\s*\S+\s*\S+)", t),
        "delivery_days": _find(r"(\d+)\s*дня \(дней\)", t),
        "address": _find(f("ЕТКАЗИБ БЕРИШ МАНЗИЛИ") + r".*?" + f("Манзил:") + r"(.+?)\s+" + f("Телефон"), t),
        "contractor_penalty": _find(f("кечиктирилган ҳар бир кун учун мажбурият бажарилмаган қисмининг") + r"([\d,]+)", t),
        "customer_penalty": _find(f("муддати ўтган тўлов суммасининг") + r"([\d,]+)", t),
    }
    qual = re.findall(r"\b\d{7}\s+(.+?)\s+-\s", t)
    terms["qualification"] = [q.strip() for q in qual if len(q) > 10]
    terms["forms"] = sorted(set(re.findall(r"(\d+)-\s*илова", t)), key=int)
    return terms


def assess(terms: Dict[str, Any], now: Optional[datetime] = None) -> List[str]:
    """Risks and actions for the company (plain sentences)."""
    now = now or now_local()
    risks: List[str] = []
    value = terms.get("value")
    dep = _num(terms.get("deposit", ""))
    if dep and value:
        amount = value * dep / 100 if "%" in terms.get("deposit", "") or dep < 100 else dep
        risks.append(f"Задаток {terms['deposit']} ≈ {amount:,.0f} сум — внести на лицевой счёт ҲККП до подачи.".replace(",", " "))
    guarantee = (terms.get("bank_guarantee") or "").lower().replace(" ", "")
    if "непринима" in guarantee or "қабулқилинмайди" in guarantee:
        risks.append("Банковская гарантия НЕ принимается — задаток только деньгами.")
    adv = _num(terms.get("advance", ""))
    if adv is not None and adv < 30:
        risks.append(f"Аванс всего {adv:g}%, окончательный расчёт: {terms.get('payment_term') or 'см. договор'} — "
                     "нужны оборотные деньги на наёмные машины.")
    if terms.get("contractor_penalty"):
        risks.append(f"Пеня для исполнителя {terms['contractor_penalty']}% в день за просрочку (до 50% стоимости) — "
                     "сроки подачи машин в предложении ставить реальные.")
    if terms.get("customer_penalty"):
        risks.append(f"Пеня заказчика за просрочку оплаты {terms['customer_penalty']}% в день — можно требовать претензией (/pretenziya).")
    end = None
    for fmt in ("%d.%m.%Y %H:%M", "%d.%m.%Y"):
        try:
            end = datetime.strptime(terms.get("end", ""), fmt)
            break
        except ValueError:
            continue
    if end:
        hours = (end - now.replace(tzinfo=None)).total_seconds() / 3600
        if hours < 0:
            risks.append("Срок подачи уже прошёл.")
        elif hours < 72:
            risks.append(f"До окончания подачи меньше 3 дней ({terms['end']}) — срочно.")
    if terms.get("tech_weight") and _num(terms["tech_weight"]) and _num(terms["tech_weight"]) >= 50:
        risks.append(f"Техническая часть весит {terms['tech_weight']}% — техническое предложение не менее важно, чем цена"
                     + (f"; минимальный проходной балл {terms['min_score']}." if terms.get("min_score") else "."))
    bidder_forms = [f for f in terms.get("forms", []) if f in {"6", "7"}]
    if bidder_forms:
        risks.append("Формы, которые заполняет участник: " + ", ".join(f"{f}-илова" for f in bidder_forms)
                     + " (/paket готовит черновики); договор — по форме 9-илова.")
    title = (terms.get("title") or "").lower()
    if any(k in title for k in ("tashish", "перевоз", "ташиш")):
        risks.append("Наша лицензия АТ № 2000764 — «городские и пригородные перевозки». Если перевозки междугородние, "
                     "уточнить требования к лицензии.")
    risks.append("После публикации протокола есть 2 рабочих дня на возражение (/etiroz); вопросы по документации — "
                 "через открытый чат, ответ заказчика в течение 2 рабочих дней.")
    return risks


def format_analysis(terms: Dict[str, Any], risks: List[str]) -> str:
    e = html.escape
    value = terms.get("value")
    rows = [
        ("Предмет", terms.get("title")), ("Заказчик", terms.get("customer")),
        ("СТИР заказчика", terms.get("customer_stir")),
        ("Стартовая сумма", f"{value:,.0f} сум".replace(",", " ") if value else ""),
        ("Подача", f"{terms.get('start')} → <b>{e(terms.get('end') or '')}</b>" if terms.get("end") else ""),
        ("Финансирование", terms.get("funding")),
        ("Оценка", " · ".join(x for x in [terms.get("method"),
                                            f"тех {terms['tech_weight']}% / цена {terms['price_weight']}%" if terms.get("tech_weight") else "",
                                            f"мин. балл {terms['min_score']}" if terms.get("min_score") else ""] if x)),
        ("Задаток", " · ".join(x for x in [terms.get("deposit"), terms.get("deposit_form"),
                                             f"гарантия банка: {terms['bank_guarantee']}" if terms.get("bank_guarantee") else ""] if x)),
        ("Оплата", " · ".join(x for x in [terms.get("payment_order"),
                                            f"аванс {terms['advance']}% за {terms.get('advance_term')}" if terms.get("advance") else "",
                                            f"расчёт {terms['payment_term']}" if terms.get("payment_term") else ""] if x)),
        ("Срок услуг", f"{terms['delivery_days']} дней" if terms.get("delivery_days") else ""),
        ("Адрес", terms.get("address")),
    ]
    lines = ["⚖️ <b>Разбор тендера</b>", ""]
    for label, value_text in rows:
        if value_text:
            text = value_text if label == "Подача" else e(str(value_text))
            lines.append(f"<b>{label}:</b> {text}")
    if terms.get("qualification"):
        lines += ["", "<b>Квалификация</b> (портал проверяет сам):"]
        lines += [f"   • {e(q)}" for q in terms["qualification"][:10]]
    lines += ["", "<b>⚠️ Риски и что сделать:</b>"] + [f"   • {e(r)}" for r in risks]
    return "\n".join(lines)


# ---------------------------------------------------------------- drafts

def add_working_days(start: datetime, days: int) -> datetime:
    d = start
    while days > 0:
        d += timedelta(days=1)
        if d.weekday() < 5:
            days -= 1
    return d


def late_penalty(amount: float, days: int, rate: float = CUSTOMER_LATE_PENALTY) -> float:
    return min(amount * rate / 100 * max(days, 0), amount * 0.5)


def _head(w: Writer, r: Dict[str, str]) -> None:
    w.p(f"{r['Название']}", bold=True, align="center", after=0)
    w.p(f"СТИР: {r['СТИР']}. {r['Адрес']}. Тел.: {r['Телефон']}. E-mail: {r['E-mail']}", align="center", size=10, after=12)
    w.p(f"Чиқиш № [___]   «[__]» {now_local().strftime('%m.%Y')} й.", after=12)


def _sign(w: Writer, r: Dict[str, str]) -> None:
    w.p("")
    w.p(f"Директор  ______________________  {r['Директор']}", bold=True)


def objection_docx(tender: Dict[str, str], reason: str, r: Dict[str, str]) -> bytes:
    w = Writer(); _head(w, r)
    w.p(f"{tender.get('customer') or '[буюртмачи]'}", bold=True, align="right", after=0)
    w.p("Харид комиссиясига", align="right", after=12)
    w.p("Танлов (тендер) натижалари бўйича", bold=True, align="center", after=0)
    w.p("ЭЪТИРОЗ", bold=True, align="center", size=14)
    w.p(f"{r['Название']} «{tender.get('title') or '[харид предмети]'}» (лот № {tender.get('lot')}) савдоларида "
        "иштирок этди. Таклифларни кўриб чиқиш ва баҳолаш баённомаси муҳокама учун давлат харидларининг электрон "
        "тизимига жойлаштирилди.", indent=True)
    w.p("Иштирокчи баённома натижаларига қуйидаги асосларда эътироз билдиради:", indent=True)
    w.p(reason or "[рад этиш ёки баҳолашнинг нотўғрилиги сабаблари: қайси талабга мувофиқ эмас деб топилган, "
        "нима учун бу хулоса нотўғри, тасдиқловчи ҳужжатлар]", indent=True)
    w.p("Иштирокчининг таклифи харид ҳужжатлари ва «Давлат харидлари тўғрисида»ги Ўзбекистон Республикаси Қонуни "
        "талабларига тўлиқ мувофиқ эканлигини тасдиқловчи ҳужжатлар илова қилинади.", indent=True)
    w.p("Юқоридагиларга асосан СЎРАЙМИЗ:", bold=True)
    w.p("1. Эътирозни харид ҳужжатларида белгиланган муддатда кўриб чиқиш.", indent=True)
    w.p("2. Иштирокчининг таклифини рад этиш (баҳолаш) тўғрисидаги қарорни қайта кўриб чиқиш ва таклифни харид "
        "ҳужжатларида белгиланган мезонлар асосида қайта баҳолаш.", indent=True)
    w.p("3. Кўриб чиқиш натижалари ҳақида асосланган ёзма жавоб юбориш.", indent=True)
    w.p("Эътироз қаноатлантирилмаган тақдирда Иштирокчи давлат харидлари соҳасидаги шикоятларни кўриб чиқувчи "
        "ваколатли органга ва судга мурожаат қилиш ҳуқуқини сақлаб қолади.", indent=True)
    w.p("Илова: [тасдиқловчи ҳужжатлар рўйхати]", after=12)
    _sign(w, r)
    return w.bytes()


def claim_docx(tender: Dict[str, str], debt: float, days: int, r: Dict[str, str]) -> bytes:
    penalty = late_penalty(debt, days)
    w = Writer(); _head(w, r)
    w.p(f"{tender.get('customer') or '[буюртмачи]'}", bold=True, align="right", after=0)
    w.p("Раҳбарига", align="right", after=12)
    w.p("ТАЛАБНОМА (ПРЕТЕНЗИЯ)", bold=True, align="center", size=14)
    w.p(f"{r['Название']} (Ижрочи) ва {tender.get('customer') or '[буюртмачи]'} (Буюртмачи) ўртасида "
        f"«{tender.get('title') or '[шартнома предмети]'}» (лот № {tender.get('lot') or '[__]'}) бўйича "
        "[шартнома № ___, «__»______ 20__ й.] тузилган.", indent=True)
    w.p("Ижрочи хизматларни шартнома шартларига мувофиқ тўлиқ кўрсатган, бу [бажарилган ишлар далолатномалари / "
        "ҳисобварақ-фактуралар № ___] билан тасдиқланади.", indent=True)
    w.p(f"Буюртмачи шартномада белгиланган муддатда тўловни амалга оширмаган. Муддати ўтган қарздорлик "
        f"{debt:,.0f} сўмни ташкил этади, тўлов {days} кунга кечиктирилган.".replace(",", " "), indent=True)
    w.p(f"Шартноманинг «Тарафларнинг жавобгарлиги» бўлимига мувофиқ Буюртмачи кечиктирилган ҳар бир кун учун муддати "
        f"ўтган тўлов суммасининг {CUSTOMER_LATE_PENALTY:g} фоизи миқдорида, бироқ 50 фоиздан ошмаган миқдорда пеня "
        f"тўлайди. Пеня суммаси: {debt:,.0f} × {CUSTOMER_LATE_PENALTY:g}% × {days} кун = {penalty:,.0f} сўм.".replace(",", " "),
        indent=True)
    w.p("Юқоридагиларга асосан ТАЛАБ ҚИЛАМИЗ:", bold=True)
    w.p(f"1. Ушбу талабнома олинган кундан бошлаб [10] кун ичида {debt:,.0f} сўм асосий қарзни тўлаш.".replace(",", " "), indent=True)
    w.p(f"2. {penalty:,.0f} сўм пеняни тўлаш.".replace(",", " "), indent=True)
    w.p("Талаб қондирилмаган тақдирда Ижрочи қарз, пеня ва суд харажатларини ундириш учун иқтисодий судга мурожаат "
        "қилади.", indent=True)
    w.p(f"Тўлов реквизитлари: {r['Банк']}, МФО {r['МФО']}, ҳ/р {r['Расчётный счёт']}, СТИР {r['СТИР']}.", indent=True)
    _sign(w, r)
    return w.bytes()


# ---------------------------------------------------------------- agent

class LawyerAgent:
    def __init__(self, base_factory: Callable[[], Optional[Base]], tender_tables: Callable[[Base], List]):
        self.base_factory = base_factory
        self.tender_tables = tender_tables

    def _base(self) -> Base:
        base = self.base_factory()
        if base is None:
            raise RuntimeError("Google Sheets не настроен")
        return base

    def analyze_pdf(self, content: bytes, filename: str = "") -> str:
        text = pdf_text(content)
        if len(normalize(text)) < 200:
            return ("⚠️ В этом PDF почти нет текста (скорее всего, скан). Пришлите PDF «Selection Information» с портала "
                    "или текстовую версию. Для сканов нужен ключ Claude API.")
        terms = parse_terms(text)
        if not (terms.get("customer") or terms.get("value")):
            return ("⚠️ Не нашёл в файле стандартных полей объявления (заказчик, сумма, сроки). Это точно документ "
                    "тендера с портала?")
        risks = assess(terms)
        try:
            base = self._base()
            base.log(AGENT, "Разбор тендера", f"{filename}: {terms.get('title', '')[:120]}, {terms.get('customer', '')}")
        except Exception:
            pass
        return format_analysis(terms, risks)

    def _tender(self, base: Base, query: str) -> Dict[str, str]:
        found = None
        if re.fullmatch(r"\d{4,}", query or ""):
            found = find_tender(self.tender_tables(base), query)
        return found or {"lot": query if re.fullmatch(r"\d{4,}", query or "") else "",
                         "title": "", "customer": "" if re.fullmatch(r"\d{4,}", query or "") else query}

    def objection(self, text: str) -> Tuple[str, List[Tuple[str, bytes]]]:
        body = re.sub(r"^/etiroz(@\w+)?", "", (text or "").strip(), flags=re.I).strip()
        if not body:
            return HELP, []
        query, _, reason = body.partition(" ")
        base = self._base()
        tender = self._tender(base, query)
        r = requisites_dict(base.records(REQ_SHEET))
        deadline = add_working_days(now_local(), 2)
        base.create_task(f"Подать возражение на протокол: лот {tender.get('lot') or query}", agent=AGENT,
                         assignee="Директор", due=deadline.strftime("%Y-%m-%d"),
                         notes="2 рабочих дня с момента публикации протокола. Проверить дату публикации на портале.")
        base.log(AGENT, "Черновик возражения", f"лот {tender.get('lot') or query}: {reason[:200]}")
        msg = (f"⚖️ <b>Черновик возражения</b> · лот {html.escape(tender.get('lot') or query)}\n\n"
               f"⏰ Срок: 2 рабочих дня после публикации протокола (ориентир: до {deadline.strftime('%d.%m.%Y')}).\n"
               "Подаётся через личный кабинет на портале. В файле допишите конкретные пункты отказа и приложите "
               "документы, которые их опровергают. Перед подачей лучше показать юристу.")
        return msg, [(f"Эътироз_лот_{tender.get('lot') or 'X'}.docx", objection_docx(tender, reason, r))]

    def claim(self, text: str) -> Tuple[str, List[Tuple[str, bytes]]]:
        body = re.sub(r"^/pretenziya(@\w+)?", "", (text or "").strip(), flags=re.I).strip()
        tokens = body.split()
        if len(tokens) < 3:
            return HELP, []
        try:
            days = int(tokens[-1])
            debt = float(re.sub(r"\D", "", tokens[-2]))
        except ValueError:
            return HELP, []
        query = " ".join(tokens[:-2])
        base = self._base()
        tender = self._tender(base, query)
        r = requisites_dict(base.records(REQ_SHEET))
        penalty = late_penalty(debt, days)
        base.log(AGENT, "Черновик претензии", f"{query}: долг {debt:.0f}, {days} дн., пеня {penalty:.0f}")
        msg = (f"⚖️ <b>Претензия за просрочку оплаты</b> · {html.escape(query)}\n\n"
               f"Долг: {debt:,.0f} сум, просрочка {days} дн.\n"
               f"Пеня {CUSTOMER_LATE_PENALTY:g}% в день (не более 50%): <b>{penalty:,.0f} сум</b>\n\n"
               "Проверьте ставку пени в вашем договоре — в стандартном договоре госзакупок 0,4% в день. "
               "Впишите номер договора и акты, подпишите, отправьте заказчику с отметкой о получении.").replace(",", " ")
        return msg, [(f"Претензия_{re.sub(r'[^0-9A-Za-zА-Яа-я]+', '_', query)[:30]}.docx", claim_docx(tender, debt, days, r))]
