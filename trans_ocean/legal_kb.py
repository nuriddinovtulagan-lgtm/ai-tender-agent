"""Lawyer's knowledge base: tax and legal notes the owner asked to keep (27.10.2026).

/pravo            — list of topics
/pravo <слово>    — notes that match the word (ндс, эттн, наличные, факторинг, ...)

Each note keeps its source and the date it was checked. These are working notes, not legal
advice: the accountant or the lawyer confirms the current wording before a decision.
"""

from __future__ import annotations

import html
import re
from typing import Dict, List

CHECKED = "27.10.2026"

NOTES: List[Dict[str, object]] = [
    {
        "id": "nds-perevozchik",
        "title": "НДС 12%, если перевозчик без НДС (ИП или частник)",
        "keys": ["ндс", "qqs", "частник", "ип", "перевозчик", "физлицо", "наём", "1,12"],
        "text": (
            "По договору перевозки Trans Ocean платит 12% НДС со всей суммы заказчика, а входящего НДС от "
            "частника нет — зачесть нечего. Цены перевозчиков сравниваем без НДС: частник выгоднее, только если "
            "его цена ниже цены плательщика НДС / 1,12.\n"
            "Пример: заказчик 11 200 000 (НДС 1 200 000). Частник 8 000 000 → НДС в бюджет 1 200 000, прибыль "
            "2 000 000. Перевозчик с НДС 8 960 000 (НДС 960 000) → в бюджет 240 000, прибыль та же 2 000 000."
        ),
        "source": "Расчёт по ставке 12%; правило владельца от 27.10.2026",
    },
    {
        "id": "nds-ekspediciya",
        "title": "Договор транспортной экспедиции: НДС только с вознаграждения",
        "keys": ["ндс", "экспедиция", "экспедитор", "вознаграждение", "250", "239", "тэу"],
        "text": (
            "У экспедитора база НДС — сумма вознаграждения; оплата перевозчику, которую заказчик возмещает, в "
            "базу не входит (ст. 239 и 250 Налогового кодекса, ред. ЗРУ-599 от 30.12.2019). Пример nrm.uz: "
            "заказчик 5 600 000, перевозчику 3 136 000, вознаграждение 2 530 000, НДС 2 530 000 × 12/112 = 271 000.\n"
            "Условие: в договоре и документах вознаграждение и возмещаемые расходы указаны отдельно. Для тендеров "
            "(договор на услуги перевозки) обычно не подходит — там НДС со всей суммы. Точную редакцию ст. 250 "
            "проверить у бухгалтера."
        ),
        "source": "https://nrm.uz/contentf?doc=224581_8_okazanie_uslug_po_dogovoru_transportnoy_ekspedicii"
                  "&products=4_prakticheskoe_nalogooblojenie ; "
                  "https://buxgalter.uz/publish/doc/text185097_kak_oblagat_transportno-ekspeditorskie_uslugi_"
                  "platelshchiku_naloga_na_pribyl_i_nds",
    },
    {
        "id": "ettn",
        "title": "Электронная ТТН (ЭТТН) и ЭСФ",
        "keys": ["эттн", "ттн", "накладная", "эсф", "счёт-фактура", "soliq", "аренда", "e-ijara", "штраф"],
        "text": (
            "Обязательна с 01.04.2024 для крупных налогоплательщиков и их поставщиков, с 01.07.2024 — для всех. "
            "Оформляется в день отгрузки, одна машина — одна ЭТТН; водитель подтверждает в приложении SoliqUz "
            "(Face ID). ЭСФ — в тот же день. Перевозка без документов = сокрытие базы, штраф 20% (ст. 223 НК). "
            "Если машина арендована — договор аренды у нотариуса и регистрация в E-ijara, тогда перевозчик в "
            "ЭТТН — мы."
        ),
        "source": "https://buxgalter.uz/ru/publish/doc/text203198_elektronnye_ttn_v_uzbekistane_v_2024_godu_"
                  "rukovodstvo_po_oformleniyu_i_vajnye_nyuansy",
    },
    {
        "id": "beznal",
        "title": "Обязательный безнал с 01.04.2026",
        "keys": ["наличные", "нал", "безнал", "перечисление", "25 млн", "азс"],
        "text": (
            "Только безналом: покупки дороже 25 млн сум, топливо на АЗС, госуслуги, коммунальные, алкоголь и "
            "табак. Политика компании — расчёты только по перечислению, наличные не берём."
        ),
        "source": "https://anhor.uz/ru/ekonomika/cashless-payments-debate-uzbekistan-2026",
    },
    {
        "id": "faktoring",
        "title": "Факторинг — деньги до срока оплаты заказчика",
        "keys": ["факторинг", "отсрочка", "дебиторка", "банк", "оплата"],
        "text": (
            "Банк выкупает счёт и платит сразу после доставки. Рынок 9,5 трлн сум в 2025, +75% в I кв. 2026; "
            "94% — банки, платформы Ozplanet и Finmakon. Условия узнать в своём банке."
        ),
        "source": "https://uz.kursiv.media/2026-06-06/czb-mezhdunarodnyj-faktoring-v-uzbekistane-vyros-v-41-raz-za-god/amp/",
    },
    {
        "id": "tender-sroki",
        "title": "Тендеры: возражение и пеня",
        "keys": ["тендер", "возражение", "эътироз", "пеня", "претензия", "просрочка"],
        "text": (
            "Возражение на протокол — 2 рабочих дня после публикации (/etiroz). Пеня заказчика за просрочку "
            "оплаты по типовому договору — 0,4% в день, не больше 50% (/pretenziya)."
        ),
        "source": "Типовой договор тендера 518494 (приложение 9)",
    },
    {
        "id": "istochniki",
        "title": "Где искать законы",
        "keys": ["закон", "кодекс", "lex", "источник", "норма", "статья"],
        "text": (
            "lex.uz — официальная база (Налоговый кодекс: lex.uz/ru/docs/4674893; текст подгружается частями, "
            "агенту видно только оглавление — нужная статья присылается ссылкой или текстом). soliq.uz — "
            "разъяснения Налогового комитета, my.soliq.uz — кабинет. nrm.uz, norma.uz, buxgalter.uz — "
            "комментарии и ответы, полный текст платный."
        ),
        "source": "https://lex.uz/ru/docs/4674893",
    },
    {
        "id": "otkrytye",
        "title": "Открытые вопросы для бухгалтера",
        "keys": ["вопрос", "самозанятый", "физлицо", "гпх", "налог"],
        "text": (
            "1) Как платить водителю-физлицу и самозанятому на карту и какие налоги удерживать. "
            "2) Точная редакция ст. 250 НК по экспедиции. 3) Условия факторинга в нашем банке."
        ),
        "source": "",
    },
]

HELP = (
    "📚 <b>Правовая база Юриста</b>\n"
    "<code>/pravo ндс</code>, <code>/pravo эттн</code>, <code>/pravo наличные</code> — заметки по слову"
)


def search(query: str) -> List[Dict[str, object]]:
    words = [w for w in re.split(r"\s+", (query or "").lower().replace("ё", "е")) if w]
    if not words:
        return []
    found = []
    for note in NOTES:
        hay = " ".join([str(note["title"]), str(note["text"]), " ".join(note["keys"])]).lower().replace("ё", "е")
        if all(w in hay for w in words):
            found.append(note)
    return found


def _format(note: Dict[str, object]) -> str:
    out = f"<b>{html.escape(str(note['title']))}</b>\n{html.escape(str(note['text']))}"
    if note.get("source"):
        out += f"\n<i>Источник: {html.escape(str(note['source']))}</i>"
    return out


def answer(text: str) -> str:
    query = re.sub(r"^/pravo(@\w+)?", "", (text or "").strip(), flags=re.I).strip()
    if not query:
        topics = "\n".join(f"• {html.escape(str(n['title']))}" for n in NOTES)
        return f"{HELP}\n\nТемы (проверено {CHECKED}):\n{topics}"
    notes = search(query)
    if not notes:
        return f"Ничего не нашёл по «{html.escape(query)}».\n\n{HELP}"
    return "\n\n".join(_format(n) for n in notes[:4]) + f"\n\n<i>Проверено {CHECKED}. Перед решением — сверить с бухгалтером.</i>"
