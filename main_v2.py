import os
import json
import io
import requests
from datetime import datetime
from urllib.parse import urljoin

from bs4 import BeautifulSoup
from fastapi import FastAPI
import gspread
from google.oauth2.service_account import Credentials

import PyPDF2
from docx import Document
import openpyxl


app = FastAPI(title="AI Tender Agent Cargo V20 Document Fix")

BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")
GOOGLE_SHEET_ID = os.getenv("GOOGLE_SHEET_ID")


SERVICE_PHRASES = [
    "перевозка грузов", "перевозке грузов", "перевозки грузов",
    "грузоперевоз", "грузовые перевозки",
    "доставка грузов", "доставка груза", "доставка товара автотранспортом",
    "транспортные услуги", "оказание транспортных услуг",
    "услуги транспорта", "услуги по перевозке", "услуги перевозки",
    "транспортно-экспедиционные услуги", "транспортно экспедиционные услуги",
    "экспедиторские услуги", "логистические услуги",
    "международные автомобильные перевозки", "международная перевозка",
    "международные перевозки", "автомобильные перевозки грузов",
    "cargo transportation", "freight transportation", "logistics service",
    "transport service", "delivery service",
    "yuk tashish", "yuklarni tashish", "yuk tashish xizmati",
    "yuklarni tashish xizmati", "yuk tashuvchi xizmatlari",
    "yuklarni tashuvchi", "transport xizmati", "transport xizmatlari",
    "logistika xizmati", "logistika xizmatlari",
    "ekspeditorlik xizmati", "ekspeditorlik xizmatlari",
    "xalqaro yuk tashish", "xalqaro tashuv", "xalqaro tashish",
    "avtotransport xizmati", "avtotransport xizmatlari",
]

SUPPORT_WORDS = [
    "перевоз", "груз", "достав", "логист", "экспед",
    "услуг", "хизмат", "xizmat", "xizmatlari", "xizmati",
    "cargo", "freight", "delivery", "logistics",
    "yuk", "yuklarni", "tashish", "tashuvchi", "tashuv",
    "xalqaro", "transport", "avtotransport",
]

HARD_BAD_WORDS = [
    "закупка", "поставка", "приобретение", "купить", "сотиб олиш",
    "машин", "машина", "автомобиль грузовой", "грузовой автомобиль",
    "шасси", "ось", "двигател", "мотор", "запчаст", "запасн",
    "инструмент", "набор инструментов", "оборудован", "техника",
    "смесительно-заряд", "зарядных машин", "спецтехника",
    "автокран", "погрузчик", "экскаватор", "трактор",
    "арматур", "бетон", "цемент", "лаборатор", "мебел", "пленк",
    "стретч", "консультац", "технадзор", "строительств", "ремонт",
    "канцеляр", "компьютер", "принтер", "медицин", "питание",
    "продукт", "одежд", "обув", "электро", "юридическ", "аудит",
    "страхован", "охрана", "дезинфек", "дезинсек", "deratiz",
    "овқат", "еда", "питания", "payvandlash",
    "метал конструкц", "metal konstruksiya",
    "yo‘li", "yo'li", "yoʻli", "avtomobil yo",
    "yer uchastkasi", "master-reja", "baholash",
    "service area", "service point", "проект", "лойиҳа",

    # V18: жёсткая отсечка IT / банковских / автоматизации / ПО
    "автоматизац", "кредит", "кредитного конвейера", "банк", "банковск",
    "финанс", "программ", "программного обеспечения", "по для",
    "система принятия решений", "принятия решений", "внедрение системы",
    "внедрению системы", "it", "информационн", "цифров",
    "software", "sap", "oracle", "crm", "erp", "сервер",
    "лиценз", "лицензи", "разработк", "поддержк программ",
    "интеграц", "модернизац системы", "сопровождение системы",
    "кибербезопас", "телеком", "интернет", "сайт", "портал",
]

SOFT_BAD_WORDS = [
    "товар", "материал", "изделие", "деталь", "агрегат", "насос",
    "кабель", "труба", "краска", "масло"
]

QUALITY_BAD_CONTEXT_WORDS = [
    "инерт материал", "инертные материалы", "щебень", "песок", "гравий",
    "бетон", "цемент", "арматура", "кирпич", "строительный материал",
    "қум", "шағал", "inert material", "qum", "shag'al",
    "утилизация", "чиқинди", "отход", "maishiy chiqindi",
    "qattiq chiqindi", "мусор", "вывоз мусора",
    "типограф", "печать", "газет", "bosmaxona", "chop etish",
    "овқат", "питание", "еда", "медицин", "лаборатор",
    "консультац", "технадзор", "аудит", "юридическ",
    "закупка", "поставка", "приобретение", "сотиб олиш",
    "запчаст", "запасн", "ось", "шасси", "двигател", "оборудован",
    "смесительно-заряд", "инструмент", "набор инструментов",
    "автоматизац", "кредит", "банк", "программ", "система принятия решений",
]

QUALITY_GOOD_CONTEXT_WORDS = [
    "перевозка грузов", "перевозке грузов", "услуга перевозка грузов",
    "транспортно-экспедитор", "экспедиторские услуги",
    "логистические услуги", "международная перевозка", "международные перевозки",
    "мультимодальная", "мультимодальные", "негабарит", "тяжеловес",
    "yuk tashish", "yuklarni tashish", "yuk tashish xizmati",
    "xalqaro yuk tashish", "xalqaro", "logistika xizmatlari",
    "ekspeditorlik", "multimodal", "gabarit bo", "og'ir vazn", "og’ir vazn",
]

QUALITY_STRONG_GOOD_WORDS = [
    "xalqaro", "международ", "multimodal", "мультимод",
    "негабарит", "тяжеловес", "gabarit bo", "og'ir vazn", "og’ir vazn",
    "экспедитор", "logistika",
]

BAD_URL_PARTS = [
    "register", "login", "logout", "signin", "signup", "cabinet", "profile",
    "account", "user", "my", "add.html", "/add", "create", "invited",
    "invitation", "english", "/en/", "news", "blog", "faq", "help",
    "contact", "about", "rules", "terms", "privacy", "advertising",
    "banner", "calendar", "archive", "feedback", "javascript:", "mailto:", "tel:",
]

BAD_TITLE_WORDS = [
    "регистрация", "зарегистрироваться", "войти", "выход",
    "стать заказчиком", "стать поставщиком", "english", "русский", "ўзбекча",
    "приглашение", "мои заявки", "моих заявок", "вопрос", "ответ",
    "помощь", "контакты", "написать нам письмо", "о сайте", "правила",
    "дата публикации", "личный кабинет", "кабинет", "профиль",
]


def clean_text(text):
    return " ".join((text or "").replace("\n", " ").replace("\t", " ").split())


def normalize_text(text):
    text = clean_text(text).lower()
    text = text.replace("’", "'").replace("‘", "'").replace("ʻ", "'").replace("`", "'")
    text = text.replace("ё", "е")
    return text


def get_headers(json_mode=False):
    return {
        "User-Agent": "Mozilla/5.0",
        "Accept": "application/json,text/plain,*/*" if json_mode else "text/html,application/xhtml+xml,*/*",
        "Accept-Language": "ru-RU,ru;q=0.9,uz;q=0.8,en;q=0.7",
        "Content-Type": "application/json" if json_mode else "text/html",
    }


def looks_like_bad_url(url):
    url = (url or "").lower()
    return any(part in url for part in BAD_URL_PARTS)


def filter_reason(title, url):
    t = normalize_text(title)

    if not title or not url:
        return "empty_title_or_url"
    if looks_like_bad_url(url):
        return "bad_url"
    if title.isdigit():
        return "only_digits"
    if len(title.split()) < 2:
        return "too_short"
    if len(t) < 10:
        return "too_short_text"

    for bad in BAD_TITLE_WORDS:
        if bad in t:
            return "bad_title_word:" + bad

    for bad in HARD_BAD_WORDS:
        if bad in t:
            return "hard_bad_word:" + bad

    for phrase in SERVICE_PHRASES:
        if phrase in t:
            return "accepted_service_phrase:" + phrase

    support_count = sum(1 for word in SUPPORT_WORDS if word in t)

    has_cargo = any(x in t for x in ["груз", "yuk", "cargo", "freight"])
    has_service = any(x in t for x in ["услуг", "xizmat", "service"])
    has_action = any(x in t for x in ["перевоз", "достав", "tashish", "tashuv", "delivery", "transportation"])

    # V18: минимум 3 опорных слова + груз + услуга + действие
    if support_count >= 3 and has_cargo and has_service and has_action:
        if any(x in t for x in SOFT_BAD_WORDS):
            return "soft_bad_but_possible"
        return "accepted_support_combo"

    return "not_service_cargo"


def is_real_cargo_tender(title, url):
    return filter_reason(title, url).startswith("accepted_")


def get_sheet():
    raw_json = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON")
    if not raw_json:
        raise Exception("GOOGLE_SERVICE_ACCOUNT_JSON is empty")

    info = json.loads(raw_json)
    creds = Credentials.from_service_account_info(
        info,
        scopes=[
            "https://www.googleapis.com/auth/spreadsheets",
            "https://www.googleapis.com/auth/drive",
        ],
    )
    client = gspread.authorize(creds)
    return client.open_by_key(GOOGLE_SHEET_ID).worksheet("Тендеры")


def send_telegram(text):
    if not BOT_TOKEN or not CHAT_ID:
        return False

    try:
        response = requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            json={"chat_id": CHAT_ID, "text": text[:3900]},
            timeout=8,
        )
        return response.status_code == 200
    except Exception as e:
        print("TELEGRAM ERROR:", e)
        return False


def make_key(site, title, url):
    return f"{site}:{normalize_text(title)[:120]}:{url}".lower()


def add_tender(tenders, seen, site, title, url):
    title = clean_text(title)
    url = clean_text(url)

    if not title or not url:
        return

    key = make_key(site, title, url)
    if key in seen:
        return

    if not is_real_cargo_tender(title, url):
        return

    seen.add(key)
    tenders.append({
        "site": site,
        "title": title[:300],
        "url": url,
        "reason": filter_reason(title, url),
    })


def add_raw_candidate(candidates, seen, site, title, url):
    title = clean_text(title)
    url = clean_text(url)

    if not title or not url:
        return

    key = make_key(site, title, url)
    if key in seen:
        return

    seen.add(key)
    candidates.append({
        "site": site,
        "title": title[:300],
        "url": url,
        "accepted": is_real_cargo_tender(title, url),
        "reason": filter_reason(title, url),
    })


def collect_links(base_url, pages, site, limit=10, raw=False):
    tenders = []
    candidates = []
    seen = set()

    for page_url in pages[:limit]:
        try:
            r = requests.get(page_url, headers=get_headers(), timeout=12)
            print(f"{site} PAGE:", page_url)
            print(f"{site} STATUS:", r.status_code)
            print(f"{site} SIZE:", len(r.text))

            if r.status_code != 200:
                continue

            soup = BeautifulSoup(r.text, "html.parser")

            for a in soup.find_all("a"):
                title = clean_text(a.get_text(" ", strip=True))
                href = a.get("href")

                if not title or not href:
                    continue

                url = urljoin(base_url, href)

                if raw:
                    add_raw_candidate(candidates, seen, site, title, url)
                else:
                    add_tender(tenders, seen, site, title, url)

            for tag in soup.find_all(["div", "tr", "li", "article", "section"]):
                text = clean_text(tag.get_text(" ", strip=True))
                if 20 <= len(text) <= 300:
                    if raw:
                        add_raw_candidate(candidates, seen, site, text, page_url)
                    else:
                        add_tender(tenders, seen, site, text, page_url)

        except Exception as e:
            print(f"{site} HTML ERROR:", e)

    return candidates if raw else tenders


def flatten_items(data):
    items = []

    if isinstance(data, list):
        for x in data:
            if isinstance(x, dict):
                items.append(x)
                items.extend(flatten_items(x))
            elif isinstance(x, list):
                items.extend(flatten_items(x))

    elif isinstance(data, dict):
        for v in data.values():
            if isinstance(v, dict):
                items.append(v)
                items.extend(flatten_items(v))
            elif isinstance(v, list):
                items.extend(flatten_items(v))

    return items


def item_url(item, base_url, site):
    for key in ["url", "link", "href"]:
        if item.get(key):
            return urljoin(base_url, str(item.get(key)))

    lot_id = (
        item.get("id")
        or item.get("lotId")
        or item.get("number")
        or item.get("lotNumber")
        or item.get("procedureId")
        or item.get("display_no")
    )

    if site == "UZEX" and lot_id:
        return f"https://etender.uzex.uz/lot/{lot_id}"

    if site == "XT-Xarid" and lot_id:
        return f"https://xt-xarid.uz/tender/{lot_id}"

    return base_url


def item_title(item):
    keys = [
        "title", "name", "lotName", "productName", "subject",
        "description", "descriptionRu", "nameRu", "nameUz",
        "goodsName", "serviceName", "procedureName",
        "category_name", "company_name",
    ]

    parts = []

    for key in keys:
        value = item.get(key)
        if value and str(value).lower() not in ["tender", "none", "null"]:
            parts.append(str(value))

    meta = item.get("meta")
    if isinstance(meta, dict):
        for gm in meta.get("good_maps", []):
            if isinstance(gm, dict):
                if gm.get("name"):
                    parts.append(str(gm.get("name")))

                category = gm.get("category")
                if isinstance(category, dict) and category.get("title"):
                    parts.append(str(category.get("title")))

        if meta.get("company_name"):
            parts.append(str(meta.get("company_name")))

    texts = [str(v) for v in item.values() if isinstance(v, str) and len(v) > 8]
    parts.extend(texts[:3])

    return clean_text(" | ".join(parts))


def parse_tenderweek(raw=False):
    base_url = "https://www.tenderweek.com/"
    pages = [base_url]

    search_queries = [
        "перевозка грузов",
        "транспортные услуги",
        "логистические услуги",
        "транспортно-экспедиционные услуги",
        "доставка грузов",
    ]

    for page in range(2, 10):
        pages.append(f"{base_url}?page={page}")

    for q in search_queries:
        pages.append(f"{base_url}?search={requests.utils.quote(q)}")
        pages.append(f"{base_url}?q={requests.utils.quote(q)}")

    return collect_links(base_url, pages, "Tenderweek", limit=20, raw=raw)


def parse_uzex_api(raw=False):
    url = "https://apietender.uzex.uz/api/common/TradeList"
    base_url = "https://etender.uzex.uz/"
    tenders = []
    candidates = []
    seen = set()

    payloads = [
        {"TypeId": 1, "From": 1, "To": 200, "System_Id": 0},
        {"TypeId": 2, "From": 1, "To": 200, "System_Id": 0},
        {"TypeId": 3, "From": 1, "To": 200, "System_Id": 0},
    ]

    for payload in payloads:
        try:
            r = requests.post(url, headers=get_headers(json_mode=True), json=payload, timeout=15)

            print("UZEX API STATUS:", r.status_code)
            print("UZEX API SIZE:", len(r.text))
            print("UZEX API TYPE:", r.headers.get("content-type", ""))

            if r.status_code != 200:
                continue

            data = r.json()
            items = flatten_items(data)
            print("UZEX API ITEMS:", len(items))

            for item in items:
                if not isinstance(item, dict):
                    continue

                title = item_title(item)
                tender_url = item_url(item, base_url, "UZEX")

                if raw:
                    add_raw_candidate(candidates, seen, "UZEX", title, tender_url)
                else:
                    add_tender(tenders, seen, "UZEX", title, tender_url)

        except Exception as e:
            print("UZEX API ERROR:", e)

    return candidates if raw else tenders


def parse_xt_xarid_api(raw=False):
    url = "https://api.xt-xarid.uz/rpc"
    base_url = "https://xt-xarid.uz/"
    tenders = []
    candidates = []
    seen = set()

    offsets = [0, 100, 200, 300]

    for offset in offsets:
        payload = {
            "id": 1,
            "jsonrpc": "2.0",
            "method": "ref",
            "params": {
                "ref": "ref_tender_public",
                "op": "read",
                "limit": 100,
                "offset": offset,
                "filters": {},
            },
        }

        try:
            r = requests.post(url, headers=get_headers(json_mode=True), json=payload, timeout=15)

            print("XT-Xarid API STATUS:", r.status_code)
            print("XT-Xarid API SIZE:", len(r.text))
            print("XT-Xarid API TYPE:", r.headers.get("content-type", ""))

            if r.status_code != 200:
                continue

            data = r.json()
            items = flatten_items(data)
            print("XT-Xarid API ITEMS:", len(items))

            for item in items:
                if not isinstance(item, dict):
                    continue

                title = item_title(item)
                tender_url = item_url(item, base_url, "XT-Xarid")

                if raw:
                    add_raw_candidate(candidates, seen, "XT-Xarid", title, tender_url)
                else:
                    add_tender(tenders, seen, "XT-Xarid", title, tender_url)

        except Exception as e:
            print("XT-Xarid API ERROR:", e)

    return candidates if raw else tenders


def parse_xt_xarid(raw=False):
    return parse_xt_xarid_api(raw=raw)


def parse_uzex(raw=False):
    return parse_uzex_api(raw=raw)


def tender_exists(url):
    try:
        sheet = get_sheet()
        urls_col_c = sheet.col_values(3)
        urls_col_d = sheet.col_values(4)
        return url in urls_col_c or url in urls_col_d
    except Exception as e:
        print("CHECK ERROR:", e)
        return False


def extract_lot_id_from_url(url):
    if not url:
        return None

    url = str(url).strip().rstrip("/")
    parts = url.split("/")

    for part in reversed(parts):
        if part.isdigit():
            return part

    return None


def safe_json_loads(raw, default=None):
    if default is None:
        default = []

    if not raw:
        return default

    try:
        return json.loads(raw)
    except Exception:
        return default


def get_uzex_lot_data(lot_id):
    api_url = f"https://apietender.uzex.uz/api/common/GetTrade/{lot_id}/0"
    r = requests.get(api_url, headers=get_headers(json_mode=True), timeout=20)
    r.raise_for_status()
    return r.json()


def short_requirements_from_trade(trade):
    parts = []

    budget_products = safe_json_loads(trade.get("budget_products"), [])
    if budget_products:
        p = budget_products[0]
        desc = clean_text(p.get("Description", ""))
        delivery = p.get("Delivery_Term")
        category = clean_text(p.get("Category_Name", ""))
        if desc:
            parts.append(desc[:350])
        if category:
            parts.append("Категория: " + category)
        if delivery:
            parts.append(f"Срок оказания: {delivery} дней")

    q_fields = trade.get("js_qualification_fields") or []
    if isinstance(q_fields, list) and q_fields:
        q_short = []
        for q in q_fields[:5]:
            name = clean_text(q.get("name", ""))
            if name:
                q_short.append(name[:120])
        if q_short:
            parts.append("Квалификация: " + "; ".join(q_short))

    return clean_text(" | ".join(parts))[:900]


def quality_decision_from_trade(trade, title=""):
    budget_products = safe_json_loads(trade.get("budget_products"), [])
    description = ""
    product_name = ""
    category_name = ""

    if budget_products:
        product_name = clean_text(budget_products[0].get("Product_Name", ""))
        description = clean_text(budget_products[0].get("Description", ""))
        category_name = clean_text(budget_products[0].get("Category_Name", ""))

    text = normalize_text(" ".join([
        title or "",
        product_name,
        description,
        category_name,
        trade.get("customer_name") or "",
        trade.get("technical_description") or "",
    ]))

    bad_hits = [w for w in QUALITY_BAD_CONTEXT_WORDS if w in text]
    good_hits = [w for w in QUALITY_GOOD_CONTEXT_WORDS if w in text]
    strong_hits = [w for w in QUALITY_STRONG_GOOD_WORDS if w in text]

    if strong_hits and any(w in text for w in ["yuk tashish", "перевоз", "transport", "логист", "экспедитор"]):
        return {
            "logistics": "Да",
            "priority": "Очень высокий",
            "risk": "Средний",
            "win_chance": "Высокий",
            "reason": "Сильный профиль логистики: " + ", ".join(strong_hits[:4]),
            "decision": "Участвовать: высокий приоритет, международная/сложная логистика, нужны партнёры и расчёт себестоимости",
        }

    if bad_hits and not strong_hits:
        return {
            "logistics": "Нет",
            "priority": "Низкий",
            "risk": "Высокий",
            "win_chance": "Низкий",
            "reason": "Непрофильный контекст: " + ", ".join(bad_hits[:5]),
            "decision": "Отказаться: непрофильная закупка или не транспортная услуга для Trans Ocean Logistics",
        }

    if good_hits:
        return {
            "logistics": "Да",
            "priority": "Высокий",
            "risk": "Средний",
            "win_chance": "Средний",
            "reason": "Профильная логистическая услуга: " + ", ".join(good_hits[:5]),
            "decision": "Рассмотреть участие: профильная логистическая услуга",
        }

    if any(w in text for w in ["transport", "транспорт", "avtotransport", "yuk"]):
        return {
            "logistics": "Сомнительно",
            "priority": "Средний",
            "risk": "Средний",
            "win_chance": "Средний",
            "reason": "Есть транспортные слова, но требуется ручная проверка",
            "decision": "Изучить: требуется ручная проверка условий",
        }

    return {
        "logistics": "Нет",
        "priority": "Низкий",
        "risk": "Высокий",
        "win_chance": "Низкий",
        "reason": "Нет достаточных признаков логистической услуги",
        "decision": "Отказаться: недостаточно признаков профильной логистики",
    }


def analyze_uzex_for_sheet(site, title, url):
    empty = {
        "Логистика": "",
        "Приоритет": "",
        "Приоритет ": "",
        "Риск": "",
        "Риск ": "",
        "Шанс победы": "",
        "Заказчик": "",
        "Сумма": "",
        "Валюта": "",
        "Оплата": "",
        "Срок оплаты": "",
        "Срок оказания услуг": "",
        "Требования": "",
        "Рекомендация AI": "",
    }

    if site != "UZEX":
        return empty

    lot_id = extract_lot_id_from_url(url)
    if not lot_id:
        empty["Рекомендация AI"] = "Не удалось определить ID лота"
        return empty

    try:
        trade = get_uzex_lot_data(lot_id)
        budget_products = safe_json_loads(trade.get("budget_products"), [])

        delivery_term = ""
        if budget_products:
            delivery_term = budget_products[0].get("Delivery_Term", "") or ""

        quality = quality_decision_from_trade(trade, title)

        return {
            "Логистика": quality.get("logistics", ""),
            "Приоритет": quality.get("priority", ""),
            "Приоритет ": quality.get("priority", ""),
            "Риск": quality.get("risk", ""),
            "Риск ": quality.get("risk", ""),
            "Шанс победы": quality.get("win_chance", ""),
            "Заказчик": trade.get("customer_name", "") or "",
            "Сумма": trade.get("start_cost", "") or "",
            "Валюта": trade.get("currency_codeabc", "") or trade.get("currency_name", "") or "",
            "Оплата": trade.get("payment_type_name", "") or "",
            "Срок оплаты": trade.get("term_payment_days", "") or "",
            "Срок оказания услуг": delivery_term,
            "Требования": short_requirements_from_trade(trade),
            "Рекомендация AI": quality.get("decision", ""),
        }

    except Exception as e:
        empty["Рекомендация AI"] = "Ошибка анализа UZEX API: " + str(e)[:180]
        return empty


EXTRA_SHEET_COLUMNS = [
    "Заказчик",
    "Сумма",
    "Валюта",
    "Оплата",
    "Срок оплаты",
    "Срок оказания услуг",
    "Требования",
    "Рекомендация AI",
]

TENDER_MANAGER_COLUMNS = [
    "Подавать",
    "Ответственный",
    "Дата решения",
    "Статус участия",
    "Комментарий директора",
]


def ensure_sheet_columns():
    sheet = get_sheet()
    headers = sheet.row_values(1)

    if not headers:
        headers = [
            "Дата",
            "Отправил",
            "Ссылка",
            "Источник",
            "Статус",
            "Приоритет",
            "AI анализ",
            "Комментарий",
        ]
        sheet.update("A1:H1", [headers])

    current_headers = sheet.row_values(1)
    added = []

    for col_name in EXTRA_SHEET_COLUMNS:
        if col_name not in current_headers:
            current_headers.append(col_name)
            added.append(col_name)

    if added:
        end_col = len(current_headers)
        end_a1 = gspread.utils.rowcol_to_a1(1, end_col)
        end_col_letter = ''.join([c for c in end_a1 if c.isalpha()])
        sheet.update(f"A1:{end_col_letter}1", [current_headers])

    return {
        "headers_total": len(current_headers),
        "added": added,
        "headers": current_headers,
    }


def ensure_tender_manager_columns():
    sheet = get_sheet()
    headers = sheet.row_values(1)

    if not headers:
        ensure_sheet_columns()

    current_headers = sheet.row_values(1)
    added = []

    for col_name in TENDER_MANAGER_COLUMNS:
        if col_name not in current_headers:
            current_headers.append(col_name)
            added.append(col_name)

    if added:
        end_col = len(current_headers)
        end_a1 = gspread.utils.rowcol_to_a1(1, end_col)
        end_col_letter = ''.join([c for c in end_a1 if c.isalpha()])
        sheet.update(f"A1:{end_col_letter}1", [current_headers])

    return {
        "headers_total": len(current_headers),
        "added": added,
        "headers": current_headers,
    }


def get_header_index_map(headers):
    result = {}
    for idx, h in enumerate(headers, start=1):
        if h:
            result[h.strip()] = idx
    return result


def update_row_analytics(sheet, row_number, headers_map, analytics):
    cells = []

    for col_name, value in analytics.items():
        col_idx = headers_map.get(col_name)
        if col_idx:
            cells.append(gspread.Cell(row_number, col_idx, value))

    if cells:
        sheet.update_cells(cells, value_input_option="USER_ENTERED")

    return len(cells)


def make_row_by_headers(headers, base_values, analytics):
    base_map = {
        "Дата": base_values.get("Дата", ""),
        "Отправил": base_values.get("Отправил", ""),
        "Ссылка": base_values.get("Ссылка", ""),
        "Источник": base_values.get("Источник", ""),
        "Статус": base_values.get("Статус", ""),
        "Приоритет": base_values.get("Приоритет", ""),
        "Приоритет ": base_values.get("Приоритет", ""),
        "AI анализ": base_values.get("AI анализ", ""),
        "Комментарий": base_values.get("Комментарий", ""),
    }

    row = []
    for h in headers:
        clean_h = h.strip()
        if h in base_map:
            row.append(base_map[h])
        elif clean_h in analytics:
            row.append(analytics[clean_h])
        else:
            row.append("")

    return row


def save_to_sheet(site, title, url):
    try:
        if tender_exists(url):
            return False

        ensure_sheet_columns()
        sheet = get_sheet()
        headers = sheet.row_values(1)

        analytics = analyze_uzex_for_sheet(site, title, url)

        base_values = {
            "Дата": datetime.now().strftime("%d.%m.%Y %H:%M"),
            "Отправил": "AI Agent",
            "Ссылка": url,
            "Источник": site,
            "Статус": "Новый",
            "Приоритет": "Средний",
            "AI анализ": "Проверить лот: найдено по Cargo V20 Document Fix",
            "Комментарий": title,
        }

        row = make_row_by_headers(headers, base_values, analytics)
        sheet.append_row(row)

        return True

    except Exception as e:
        print("GOOGLE SHEETS ERROR:", e)
        return False


def tender_exists(url):
    try:
        sheet = get_sheet()
        urls_col_c = sheet.col_values(3)
        urls_col_d = sheet.col_values(4)
        return url in urls_col_c or url in urls_col_d
    except Exception as e:
        print("CHECK ERROR:", e)
        return False


def extract_file_links_from_html(page_url, html):
    soup = BeautifulSoup(html, "html.parser")
    found = []
    seen = set()

    keywords = [
        "pdf", "doc", "docx", "xls", "xlsx", "zip", "download", "file",
        "тех", "техничес", "документац", "задание", "контракт",
        "протокол", "скачать", "fayl", "hujjat", "tex", "shartnoma"
    ]

    for a in soup.find_all("a"):
        text = clean_text(a.get_text(" ", strip=True))
        href = a.get("href") or ""

        if not href:
            continue

        full_url = urljoin(page_url, href)
        check = (text + " " + href + " " + full_url).lower()

        is_file = any(ext in check for ext in [".pdf", ".doc", ".docx", ".xls", ".xlsx", ".zip"])
        is_keyword = any(k in check for k in keywords)

        if is_file or is_keyword:
            if full_url not in seen:
                seen.add(full_url)
                found.append({
                    "text": text[:200],
                    "url": full_url,
                    "is_file": is_file,
                    "looks_relevant": is_keyword,
                })

    return found


def build_file_url_candidates(file_path):
    """
    UZEX sometimes stores file paths in API as /files/... or tender/user-files/...
    The public Angular page can return 200 text/html for those paths.
    V20 tries several host/path variants and later validates real file bytes.
    """
    if not file_path:
        return []

    file_path = str(file_path).strip()
    candidates = []

    if file_path.startswith("http://") or file_path.startswith("https://"):
        candidates.append(file_path)
    else:
        clean = file_path.lstrip("/")

        host_variants = [
            "https://etender.uzex.uz/",
            "https://apietender.uzex.uz/",
        ]

        path_variants = [clean]

        # Some API paths come as files/... while real storage can be tender/user-files/...
        if clean.startswith("files/"):
            path_variants.append("tender/user-files/" + clean[len("files/"):])
            path_variants.append("user-files/" + clean[len("files/"):])

        if clean.startswith("tender/user-files/"):
            tail = clean[len("tender/user-files/"):]
            path_variants.append("files/" + tail)
            path_variants.append("user-files/" + tail)

        if clean.startswith("user-files/"):
            tail = clean[len("user-files/"):]
            path_variants.append("files/" + tail)
            path_variants.append("tender/user-files/" + tail)

        for host in host_variants:
            for path in path_variants:
                candidates.append(host + path)

    result = []
    seen = set()
    for u in candidates:
        if u not in seen:
            seen.add(u)
            result.append(u)

    return result


def is_html_response(content, content_type=""):
    head = (content or b"")[:500].lower()
    ctype = (content_type or "").lower()
    return (
        "text/html" in ctype
        or b"<!doctype html" in head
        or b"<html" in head
        or b"<app-root" in head
    )


def detect_file_kind(content, content_type="", url=""):
    """Returns pdf/docx/xlsx/zip/html/unknown based on headers and magic bytes."""
    content = content or b""
    ctype = (content_type or "").lower()
    url_low = (url or "").lower()

    if is_html_response(content, ctype):
        return "html"
    if content.startswith(b"%PDF") or "application/pdf" in ctype or url_low.endswith(".pdf"):
        return "pdf" if content.startswith(b"%PDF") else "maybe_pdf"
    if content.startswith(b"PK"):
        if url_low.endswith(".docx") or "wordprocessingml" in ctype:
            return "docx"
        if url_low.endswith(".xlsx") or "spreadsheetml" in ctype:
            return "xlsx"
        return "zip"
    return "unknown"


def download_file_with_fallback(file_path):
    """
    Downloads only a real binary document.
    V19 bug: any HTTP 200 response was accepted, including Angular HTML shell.
    V20: reject HTML, validate magic bytes, then continue to the next candidate URL.
    """
    attempts = []

    for file_url in build_file_url_candidates(file_path):
        try:
            r = requests.get(
                file_url,
                headers={
                    **get_headers(),
                    "Accept": "application/pdf,application/vnd.openxmlformats-officedocument.wordprocessingml.document,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet,application/octet-stream,*/*",
                    "Referer": "https://etender.uzex.uz/",
                },
                timeout=30,
                allow_redirects=True,
            )
            content_type = r.headers.get("content-type", "")
            kind = detect_file_kind(r.content, content_type, file_url)

            info = {
                "url": file_url,
                "status_code": r.status_code,
                "content_type": content_type,
                "size": len(r.content or b""),
                "detected_kind": kind,
            }

            if kind == "html":
                info["rejected"] = "html_page_not_document"
                info["html_start"] = (r.text or "")[:300]

            attempts.append(info)

            if r.status_code == 200 and r.content and kind in ["pdf", "docx", "xlsx", "zip"]:
                return r.content, file_url, attempts

            # Do not accept fake PDF/DOCX URL if body is not a real file.
            if r.status_code == 200 and kind in ["maybe_pdf", "unknown"]:
                attempts[-1]["rejected"] = "not_real_document_bytes"

        except Exception as e:
            attempts.append({
                "url": file_url,
                "error": str(e),
            })

    raise Exception("No real document downloaded. Attempts: " + json.dumps(attempts, ensure_ascii=False)[:1800])


def read_pdf_from_bytes(file_bytes, max_pages=12):
    reader = PyPDF2.PdfReader(io.BytesIO(file_bytes))
    text_parts = []

    for i, page in enumerate(reader.pages[:max_pages]):
        try:
            page_text = page.extract_text() or ""
            if page_text:
                text_parts.append(page_text)
        except Exception as e:
            text_parts.append(f"[PDF PAGE {i + 1} READ ERROR: {e}]")

    return clean_text("\n".join(text_parts))


def read_docx_from_bytes(file_bytes):
    doc = Document(io.BytesIO(file_bytes))
    text_parts = []

    for p in doc.paragraphs:
        txt = clean_text(p.text)
        if txt:
            text_parts.append(txt)

    for table in doc.tables:
        for row in table.rows:
            row_text = " | ".join(clean_text(cell.text) for cell in row.cells)
            if row_text.strip():
                text_parts.append(row_text)

    return clean_text("\n".join(text_parts))


def get_uzex_trade(lot_id):
    url = f"https://apietender.uzex.uz/api/common/GetTrade/{lot_id}/0"
    r = requests.get(url, headers=get_headers(json_mode=True), timeout=30)
    r.raise_for_status()
    return r.json()


def extract_budget_products(trade):
    raw = trade.get("budget_products")
    if not raw:
        return []

    try:
        return json.loads(raw)
    except Exception:
        return []


def analyze_text_for_logistics(title, text):
    t = normalize_text(title + " " + text)

    high_priority_words = [
        "негабарит", "тяжеловес", "og'ir vazn", "og’ir vazn", "gabarit bo'lmagan",
        "мультимодаль", "multimodal", "международн", "xalqaro",
        "китай", "xitoy", "тяньцзинь", "tyantszin", "хоргос", "xorgos",
        "казахстан", "qozog", "qozog'iston", "qozog’iston",
        "электропоезд", "высокоскоростн", "poezd",
    ]

    medium_priority_words = [
        "перевозка грузов", "yuk tashish", "транспортно-экспедитор",
        "экспедитор", "transport", "logistika", "доставка",
    ]

    risk_words = [
        "goldhofer", "gantry", "500", "745", "8x4", "низкорам", "модуль",
        "специальные дорожные разрешения", "обследование дорог",
        "страхование", "перестрах", "10 лет", "фото", "видео",
    ]

    priority = "Средний"
    chance = "Средний"
    risk = "Средний"
    logistics = "Да"

    if any(w in t for w in high_priority_words):
        priority = "Очень высокий"
        chance = "Высокий"
    elif any(w in t for w in medium_priority_words):
        priority = "Высокий"
        chance = "Средний"

    if any(w in t for w in risk_words):
        risk = "Высокий" if "goldhofer" in t or "gantry" in t else "Средний"

    if any(w in t for w in ["типограф", "лаборатор", "консультац", "оборудован", "ремонт"]):
        logistics = "Нет"
        priority = "Низкий"
        chance = "Низкий"
        risk = "Высокий"

    if priority == "Очень высокий":
        decision = "Изучить ТЗ и готовить участие с партнёрами"
    elif priority == "Высокий":
        decision = "Рассмотреть участие"
    elif logistics == "Нет":
        decision = "Отказаться"
    else:
        decision = "Изучить"

    return {
        "priority": priority,
        "win_chance": chance,
        "risk": risk,
        "logistics": logistics,
        "decision": decision,
    }


def pick_text_snippet(text, keywords, limit=700):
    t = text or ""
    low = t.lower()

    for kw in keywords:
        pos = low.find(kw.lower())
        if pos >= 0:
            start = max(0, pos - 250)
            end = min(len(t), pos + limit)
            return clean_text(t[start:end])

    return clean_text(t[:limit])


@app.get("/")
def home():
    return {"status": "AI Tender Agent Cargo V20 Document Fix is running"}


@app.head("/")
def head_home():
    return {}


@app.get("/version")
def version():
    return {
        "version": "cargo_v20_document_fix",
        "status": "running"
    }


@app.get("/health")
def health():
    result = {"status": "ok", "telegram": False, "google_sheets": False, "errors": []}

    try:
        tg = requests.get(f"https://api.telegram.org/bot{BOT_TOKEN}/getMe", timeout=8)
        result["telegram"] = tg.status_code == 200
    except Exception as e:
        result["errors"].append("Telegram error: " + str(e))

    try:
        sheet = get_sheet()
        sheet.row_values(1)
        result["google_sheets"] = True
    except Exception as e:
        result["errors"].append("Google Sheets error: " + str(e))

    if result["errors"]:
        result["status"] = "warning"

    return result


@app.get("/test_filter")
def test_filter():
    return {
        "Услуга по перевозке грузов": is_real_cargo_tender("Услуга по перевозке грузов", "https://etender.uzex.uz/lot/123"),
        "Оказание транспортных услуг": is_real_cargo_tender("Оказание транспортных услуг", "https://xarid.uzex.uz/tender/456"),
        "Транспортно-экспедиционные услуги": is_real_cargo_tender("Транспортно-экспедиционные услуги", "https://etender.uzex.uz/lot/999"),
        "Лабораторное оборудование": is_real_cargo_tender("Лабораторное оборудование", "https://www.tenderweek.com/tender-35921"),
        "Закупка арматуры для АЭС": is_real_cargo_tender("Закупка арматуры для АЭС", "https://www.tenderweek.com/tender-35911"),
        "Доставка товара автотранспортом": is_real_cargo_tender("Доставка товара автотранспортом", "https://www.tenderweek.com/tender-99999"),
        "O’zbekneftgaz yuklarni tashuvchi avtotransporti xizmatlari": is_real_cargo_tender(
            "O’zbekneftgaz yuklarni tashuvchi avtotransporti xizmatlari",
            "https://etender.uzex.uz/lot/488787",
        ),
        "xalqaro avtomobil yo‘lining master-reja ishlab chiqish": is_real_cargo_tender(
            "xalqaro avtomobil yo‘lining master-reja ishlab chiqish",
            "https://etender.uzex.uz/lot/494429",
        ),
        "Набор инструментов": is_real_cargo_tender(
            "Набор инструментов",
            "https://xt-xarid.uz/tender/7477544",
        ),
        "Закупка смесительно-зарядных машин грузоподъёмностью 20 тонн": is_real_cargo_tender(
            "Закупка смесительно-зарядных машин грузоподъёмностью 20 тонн",
            "https://xt-xarid.uz/tender/7389067",
        ),
        "Ось грузовых автотранспортных средств": is_real_cargo_tender(
            "Ось грузовых автотранспортных средств",
            "https://xt-xarid.uz/tender/29.32.30.219_00012",
        ),
        "xalqaro yuk tashish xizmati": is_real_cargo_tender(
            "xalqaro yuk tashish xizmati",
            "https://xt-xarid.uz/tender/999",
        ),
        "Автоматизация кредитного конвейера": is_real_cargo_tender(
            "Предоставление услуг по автоматизации кредитного конвейера",
            "https://xt-xarid.uz/tender/111",
        ),
        "Внедрение системы принятия решений": is_real_cargo_tender(
            "Предоставление услуг по внедрению Системы принятия решений",
            "https://xt-xarid.uz/tender/222",
        ),
    }


@app.get("/analyze_doc_test")
def analyze_doc_test():
    return {
        "status": "ok",
        "version": "document_analyzer_v20",
        "pdf_reader": True,
        "docx_reader": True,
        "xlsx_reader": True,
        "modules": {
            "PyPDF2": True,
            "python_docx": True,
            "openpyxl": True
        }
    }


@app.get("/debug_sources")
def debug_sources():
    result = {
        "version": "cargo_v20_document_fix",
        "Tenderweek": 0,
        "UZEX": 0,
        "XT-Xarid": 0,
        "total": 0,
        "errors": [],
    }

    try:
        tw = parse_tenderweek()
        result["Tenderweek"] = len(tw)
    except Exception as e:
        result["errors"].append("Tenderweek error: " + str(e))

    try:
        uzex = parse_uzex()
        result["UZEX"] = len(uzex)
    except Exception as e:
        result["errors"].append("UZEX error: " + str(e))

    try:
        xt = parse_xt_xarid()
        result["XT-Xarid"] = len(xt)
    except Exception as e:
        result["errors"].append("XT-Xarid error: " + str(e))

    result["total"] = result["Tenderweek"] + result["UZEX"] + result["XT-Xarid"]
    return result


@app.get("/debug_items")
def debug_items():
    all_items = []

    for source_name, parser in [
        ("Tenderweek", parse_tenderweek),
        ("UZEX", parse_uzex),
        ("XT-Xarid", parse_xt_xarid),
    ]:
        try:
            result = parser()
            for item in result[:10]:
                all_items.append({
                    "site": source_name,
                    "title": item.get("title"),
                    "url": item.get("url"),
                    "reason": item.get("reason"),
                })
        except Exception as e:
            all_items.append({
                "site": source_name,
                "error": str(e),
            })

    return {
        "version": "cargo_v20_document_fix",
        "count": len(all_items),
        "items": all_items[:30],
    }


@app.get("/debug_raw_candidates")
def debug_raw_candidates():
    all_items = []

    for source_name, parser in [
        ("Tenderweek", parse_tenderweek),
        ("UZEX", parse_uzex),
        ("XT-Xarid", parse_xt_xarid),
    ]:
        try:
            result = parser(raw=True)
            for item in result[:25]:
                all_items.append(item)
        except Exception as e:
            all_items.append({
                "site": source_name,
                "error": str(e),
            })

    accepted = [x for x in all_items if x.get("accepted") is True]
    rejected = [x for x in all_items if x.get("accepted") is False]

    return {
        "version": "cargo_v20_document_fix",
        "total_candidates_sample": len(all_items),
        "accepted_sample": len(accepted),
        "rejected_sample": len(rejected),
        "items": all_items[:75],
    }


@app.get("/setup_sheet_columns")
def setup_sheet_columns():
    try:
        result = ensure_sheet_columns()
        return {
            "status": "ok",
            "version": "sheet_setup_v20",
            "message": "Google Sheets columns checked and updated",
            **result,
        }
    except Exception as e:
        return {
            "status": "error",
            "version": "sheet_setup_v20",
            "error": str(e),
        }


@app.get("/setup_tender_manager_columns")
def setup_tender_manager_columns():
    try:
        ensure_sheet_columns()
        result = ensure_tender_manager_columns()

        return {
            "status": "ok",
            "version": "tender_manager_v20",
            "message": "Tender manager columns checked and updated",
            **result,
        }

    except Exception as e:
        return {
            "status": "error",
            "version": "tender_manager_v20",
            "error": str(e),
        }


@app.get("/tender_manager_status")
def tender_manager_status():
    try:
        sheet = get_sheet()
        headers = sheet.row_values(1)

        missing = []
        for col in EXTRA_SHEET_COLUMNS + TENDER_MANAGER_COLUMNS:
            if col not in headers:
                missing.append(col)

        return {
            "status": "ok" if not missing else "warning",
            "version": "tender_manager_v20",
            "headers_total": len(headers),
            "missing_columns": missing,
            "manager_columns": TENDER_MANAGER_COLUMNS,
            "analytics_columns": EXTRA_SHEET_COLUMNS,
        }

    except Exception as e:
        return {
            "status": "error",
            "version": "tender_manager_v20",
            "error": str(e),
        }


@app.get("/backfill_preview")
def backfill_preview(limit: int = 20):
    try:
        sheet = get_sheet()
        all_values = sheet.get_all_values()

        if not all_values:
            return {
                "status": "warning",
                "version": "backfill_v20",
                "message": "Sheet is empty",
            }

        headers = all_values[0]
        headers_map = get_header_index_map(headers)

        if "Ссылка" not in headers_map or "Заказчик" not in headers_map:
            return {
                "status": "error",
                "version": "backfill_v20",
                "error": "Required columns not found",
                "headers": headers,
            }

        link_col = headers_map["Ссылка"]
        customer_col = headers_map["Заказчик"]

        candidates = []

        for row_idx, row in enumerate(all_values[1:], start=2):
            if len(candidates) >= limit:
                break

            link = row[link_col - 1] if len(row) >= link_col else ""
            customer_value = row[customer_col - 1] if len(row) >= customer_col else ""

            if link and "etender.uzex.uz/lot/" in link and not customer_value:
                candidates.append({
                    "row": row_idx,
                    "link": link,
                    "lot_id": extract_lot_id_from_url(link),
                })

        return {
            "status": "ok",
            "version": "backfill_v20",
            "candidates_count": len(candidates),
            "candidates": candidates,
        }

    except Exception as e:
        return {
            "status": "error",
            "version": "backfill_v20",
            "error": str(e),
        }


@app.get("/backfill_existing_tenders")
def backfill_existing_tenders(limit: int = 50):
    try:
        ensure_sheet_columns()
        ensure_tender_manager_columns()

        sheet = get_sheet()
        all_values = sheet.get_all_values()

        if not all_values:
            return {
                "status": "warning",
                "version": "backfill_v20",
                "message": "Sheet is empty",
            }

        headers = all_values[0]
        headers_map = get_header_index_map(headers)

        required = [
            "Ссылка",
            "Заказчик",
            "Сумма",
            "Валюта",
            "Оплата",
            "Срок оплаты",
            "Срок оказания услуг",
            "Требования",
            "Рекомендация AI",
        ]

        missing = [h for h in required if h not in headers_map]
        if missing:
            return {
                "status": "error",
                "version": "backfill_v20",
                "error": "Missing columns: " + ", ".join(missing),
                "headers": headers,
            }

        processed = 0
        updated = 0
        skipped = 0
        errors = []

        link_col = headers_map["Ссылка"]
        customer_col = headers_map["Заказчик"]

        for row_idx, row in enumerate(all_values[1:], start=2):
            if processed >= limit:
                break

            link = row[link_col - 1] if len(row) >= link_col else ""
            customer_value = row[customer_col - 1] if len(row) >= customer_col else ""

            if not link or "etender.uzex.uz/lot/" not in link:
                skipped += 1
                continue

            if customer_value:
                skipped += 1
                continue

            processed += 1

            try:
                analytics = analyze_uzex_for_sheet("UZEX", "Backfill UZEX lot", link)
                update_row_analytics(sheet, row_idx, headers_map, analytics)
                updated += 1

            except Exception as e:
                errors.append({
                    "row": row_idx,
                    "link": link,
                    "error": str(e)[:250],
                })

        return {
            "status": "ok",
            "version": "backfill_v20",
            "processed": processed,
            "updated": updated,
            "skipped": skipped,
            "errors_count": len(errors),
            "errors": errors[:10],
            "message": "Existing UZEX tenders backfill completed",
        }

    except Exception as e:
        return {
            "status": "error",
            "version": "backfill_v20",
            "error": str(e),
        }


@app.get("/quality_backfill_existing_tenders")
def quality_backfill_existing_tenders(limit: int = 100):
    try:
        ensure_sheet_columns()
        ensure_tender_manager_columns()

        sheet = get_sheet()
        all_values = sheet.get_all_values()

        if not all_values:
            return {
                "status": "warning",
                "version": "quality_filter_v20",
                "message": "Sheet is empty",
            }

        headers = all_values[0]
        headers_map = get_header_index_map(headers)

        if "Ссылка" not in headers_map:
            return {
                "status": "error",
                "version": "quality_filter_v20",
                "error": "Missing column: Ссылка",
            }

        processed = 0
        updated = 0
        skipped = 0
        errors = []

        link_col = headers_map["Ссылка"]

        for row_idx, row in enumerate(all_values[1:], start=2):
            if processed >= limit:
                break

            link = row[link_col - 1] if len(row) >= link_col else ""

            if not link or "etender.uzex.uz/lot/" not in link:
                skipped += 1
                continue

            processed += 1

            try:
                analytics = analyze_uzex_for_sheet("UZEX", "Quality backfill UZEX lot", link)
                update_row_analytics(sheet, row_idx, headers_map, analytics)
                updated += 1

            except Exception as e:
                errors.append({
                    "row": row_idx,
                    "link": link,
                    "error": str(e)[:250],
                })

        return {
            "status": "ok",
            "version": "quality_filter_v20",
            "processed": processed,
            "updated": updated,
            "skipped": skipped,
            "errors_count": len(errors),
            "errors": errors[:10],
            "message": "Quality filter backfill completed",
        }

    except Exception as e:
        return {
            "status": "error",
            "version": "quality_filter_v20",
            "error": str(e),
        }


@app.get("/test_quality_filter")
def test_quality_filter():
    samples = {
        "Услуга по перевозке грузов": "Услуга по перевозке грузов",
        "Международная мультимодальная перевозка негабаритных грузов": "Xitoy-Qozog’iston-O’zbekiston yo’nalishi bo’yicha gabarit bo’lmagan va og’ir vaznli yuklarni tashish",
        "Инертные материалы": "Инертные материалы с доставкой",
        "Вывоз мусора": "qattiq va maishiy chiqindilarni olib chiqib ketish",
        "Печать газет": "gazetalarni chop etish uchun bosmaxona xizmatlari",
        "Закупка осей": "Ось грузовых автотранспортных средств",
        "Автоматизация кредитного конвейера": "Предоставление услуг по автоматизации кредитного конвейера",
    }

    result = {}
    for name, text in samples.items():
        fake_trade = {
            "budget_products": json.dumps([{
                "Product_Name": text,
                "Description": text,
                "Category_Name": "Услуги сухопутного и трубопроводного транспорта",
                "Delivery_Term": 10,
            }], ensure_ascii=False),
            "customer_name": "TEST",
            "technical_description": text,
        }
        result[name] = quality_decision_from_trade(fake_trade, text)

    return {
        "status": "ok",
        "version": "quality_filter_v20",
        "samples": result,
    }


@app.get("/analyze_uzex_lot")
def analyze_uzex_lot(lot_id: str):
    try:
        trade = get_uzex_trade(lot_id)

        budget_products = extract_budget_products(trade)
        product_name = ""
        product_description = ""

        if budget_products:
            product_name = budget_products[0].get("Product_Name", "") or ""
            product_description = budget_products[0].get("Description", "") or ""

        documents = []
        combined_text = ""

        file_fields = [
            ("tech_file", trade.get("tech_file_name"), trade.get("tech_file_path"), trade.get("tech_file_ext")),
            ("tech_doc_file", trade.get("tech_doc_file_name"), trade.get("tech_doc_file_path"), trade.get("tech_doc_file_ext")),
            ("contract_proform_file", trade.get("contract_proform_file_name"), trade.get("contract_proform_file_path"), trade.get("contract_proform_file_ext")),
            ("prolong_file", trade.get("prolong_file_name"), trade.get("prolong_file_path"), trade.get("prolong_file_ext")),
        ]

        for doc_type, name, path, ext in file_fields:
            if not path:
                continue

            doc_info = {
                "type": doc_type,
                "name": name,
                "ext": ext,
                "path": path,
                "url_candidates": build_file_url_candidates(path),
                "working_url": None,
                "download_attempts": [],
                "read_status": "not_read",
                "text_preview": "",
            }

            try:
                ext_norm = (ext or name or path or "").lower()

                file_bytes, working_url, attempts = download_file_with_fallback(path)
                doc_info["working_url"] = working_url
                doc_info["download_attempts"] = attempts

                if "pdf" in ext_norm:
                    text = read_pdf_from_bytes(file_bytes)
                    doc_info["read_status"] = "ok"
                    doc_info["text_preview"] = text[:1200]
                    combined_text += "\n" + text

                elif "docx" in ext_norm:
                    text = read_docx_from_bytes(file_bytes)
                    doc_info["read_status"] = "ok"
                    doc_info["text_preview"] = text[:1200]
                    combined_text += "\n" + text

                else:
                    doc_info["read_status"] = "unsupported_ext"

            except Exception as e:
                doc_info["read_status"] = "error"
                doc_info["error"] = str(e)

            documents.append(doc_info)

        base_title = " | ".join([
            str(trade.get("customer_name") or ""),
            str(product_name or ""),
            str(product_description or ""),
            str(trade.get("technical_description") or ""),
        ])

        scoring = analyze_text_for_logistics(base_title, combined_text)

        route_snippet = pick_text_snippet(
            combined_text,
            ["маршрут", "тяньцзинь", "хоргос", "яллама", "ташкент", "tyantszin", "xorgos", "yallama"]
        )

        requirements_snippet = pick_text_snippet(
            combined_text,
            ["требования к участнику", "тягач", "goldhofer", "gantry", "требования к персоналу"]
        )

        payment_snippet = pick_text_snippet(
            combined_text,
            ["условия оплаты", "оплата", "payment", "30 календарных дней", "15 дней"]
        )

        return {
            "status": "ok",
            "version": "document_analyzer_v20",
            "lot_id": lot_id,
            "api_url": f"https://apietender.uzex.uz/api/common/GetTrade/{lot_id}/0",
            "lot": {
                "id": trade.get("id"),
                "display_no": trade.get("display_no"),
                "customer_name": trade.get("customer_name"),
                "customer_tin": trade.get("customer_tin"),
                "start_date": trade.get("start_date"),
                "end_date": trade.get("end_date"),
                "start_cost": trade.get("start_cost"),
                "currency": trade.get("currency_codeabc") or trade.get("currency_name"),
                "valuation_name": trade.get("valuation_name"),
                "payment_type_name": trade.get("payment_type_name"),
                "term_payment_days": trade.get("term_payment_days"),
                "delivery_term_days": budget_products[0].get("Delivery_Term") if budget_products else None,
                "product_name": product_name,
                "product_description": product_description,
                "category_name": budget_products[0].get("Category_Name") if budget_products else None,
            },
            "documents_count": len(documents),
            "documents": documents,
            "analysis": {
                "priority": scoring["priority"],
                "win_chance": scoring["win_chance"],
                "risk": scoring["risk"],
                "logistics": scoring["logistics"],
                "decision": scoring["decision"],
                "route_snippet": route_snippet,
                "requirements_snippet": requirements_snippet,
                "payment_snippet": payment_snippet,
                "summary": (
                    f"Заказчик: {trade.get('customer_name')}. "
                    f"Предмет: {product_name}. "
                    f"Стартовая цена: {trade.get('start_cost')} {trade.get('currency_codeabc') or trade.get('currency_name')}. "
                    f"Рекомендация: {scoring['decision']}."
                ),
            },
        }

    except Exception as e:
        return {
            "status": "error",
            "version": "document_analyzer_v20",
            "lot_id": lot_id,
            "error": str(e),
        }


@app.get("/debug_uzex_lot_api")
def debug_uzex_lot_api(lot_id: str):
    candidates = []

    get_urls = [
        f"https://apietender.uzex.uz/api/common/GetTrade/{lot_id}/0",
        f"https://apietender.uzex.uz/api/common/GetTrade/{lot_id}",
        f"https://apietender.uzex.uz/api/common/Trade/{lot_id}",
        f"https://apietender.uzex.uz/api/common/Lot/{lot_id}",
    ]

    for u in get_urls:
        try:
            r = requests.get(u, headers=get_headers(json_mode=True), timeout=15)
            candidates.append({
                "url": u,
                "status_code": r.status_code,
                "content_type": r.headers.get("content-type", ""),
                "size": len(r.text),
                "text_start": r.text[:500],
            })
        except Exception as e:
            candidates.append({"url": u, "error": str(e)})

    return {
        "status": "ok",
        "version": "debug_uzex_lot_v20",
        "lot_id": lot_id,
        "results": candidates,
    }


@app.get("/debug_lot_files")
def debug_lot_files(url: str):
    try:
        r = requests.get(url, headers=get_headers(), timeout=20)

        result = {
            "status": "ok",
            "version": "document_analyzer_v20_debug_files",
            "lot_url": url,
            "http_status": r.status_code,
            "content_type": r.headers.get("content-type", ""),
            "html_size": len(r.text),
            "files_found": [],
            "files_count": 0,
            "page_title": "",
            "html_start": r.text[:500],
        }

        if r.status_code != 200:
            result["status"] = "http_error"
            return result

        soup = BeautifulSoup(r.text, "html.parser")
        title_tag = soup.find("title")
        if title_tag:
            result["page_title"] = clean_text(title_tag.get_text())

        files = extract_file_links_from_html(url, r.text)
        result["files_found"] = files[:50]
        result["files_count"] = len(files)

        return result

    except Exception as e:
        return {
            "status": "error",
            "version": "document_analyzer_v20_debug_files",
            "lot_url": url,
            "error": str(e)
        }


@app.get("/debug_file_download")
def debug_file_download(path: str):
    """Debug one UZEX file path from GetTrade API and show why it works/fails."""
    try:
        file_bytes, working_url, attempts = download_file_with_fallback(path)
        return {
            "status": "ok",
            "version": "document_downloader_v20",
            "path": path,
            "working_url": working_url,
            "size": len(file_bytes or b""),
            "detected_kind": detect_file_kind(file_bytes, "", working_url),
            "attempts": attempts,
        }
    except Exception as e:
        return {
            "status": "error",
            "version": "document_downloader_v20",
            "path": path,
            "error": str(e),
        }


@app.get("/debug_uzex_lot_files_api")
def debug_uzex_lot_files_api(lot_id: str):
    """Shows UZEX document fields and candidate URLs without trying to parse the files."""
    try:
        trade = get_uzex_trade(lot_id)
        file_fields = [
            ("tech_file", trade.get("tech_file_name"), trade.get("tech_file_path"), trade.get("tech_file_ext")),
            ("tech_doc_file", trade.get("tech_doc_file_name"), trade.get("tech_doc_file_path"), trade.get("tech_doc_file_ext")),
            ("contract_proform_file", trade.get("contract_proform_file_name"), trade.get("contract_proform_file_path"), trade.get("contract_proform_file_ext")),
            ("prolong_file", trade.get("prolong_file_name"), trade.get("prolong_file_path"), trade.get("prolong_file_ext")),
        ]
        docs = []
        for doc_type, name, path, ext in file_fields:
            if not path:
                continue
            docs.append({
                "type": doc_type,
                "name": name,
                "ext": ext,
                "path": path,
                "url_candidates": build_file_url_candidates(path),
            })
        return {
            "status": "ok",
            "version": "document_downloader_v20",
            "lot_id": lot_id,
            "documents_count": len(docs),
            "documents": docs,
        }
    except Exception as e:
        return {
            "status": "error",
            "version": "document_downloader_v20",
            "lot_id": lot_id,
            "error": str(e),
        }


def format_money(value):
    """Красиво форматирует сумму для Telegram."""
    if value is None or value == "":
        return "-"

    try:
        number = float(str(value).replace(" ", "").replace(",", "."))
        if number.is_integer():
            return f"{int(number):,}".replace(",", " ")
        return f"{number:,.2f}".replace(",", " ")
    except Exception:
        return str(value)


def extract_priority_label(analytics, title=""):
    """Дополнительная логика приоритета для Telegram, чтобы отличать перевозки от складских/погрузочных услуг."""
    t = normalize_text(title)

    high_words = [
        "международ", "xalqaro", "транспортно-экспедитор", "экспедитор",
        "перевозка грузов", "перевозке грузов", "yuk tashish",
        "мультимод", "multimodal", "негабарит", "тяжеловес",
        "og'ir vazn", "og’ir vazn", "gabarit bo",
    ]

    medium_words = [
        "погруз", "разгруз", "склад", "складирован", "ombor",
        "yuklash", "tushirish", "вспомогательные транспортные услуги",
    ]

    if any(w in t for w in high_words):
        return "Высокий"

    if any(w in t for w in medium_words):
        return "Средний"

    return analytics.get("Приоритет") or analytics.get("Приоритет ") or "Средний"


def format_tender_message(tender):
    """Формирует расширенное Telegram-сообщение с AI-анализом."""
    site = tender.get("site", "")
    title = tender.get("title", "")
    url = tender.get("url", "")

    analytics = {}

    if site == "UZEX":
        analytics = analyze_uzex_for_sheet(site, title, url)
    else:
        analytics = {
            "Заказчик": "-",
            "Сумма": "-",
            "Валюта": "-",
            "Срок оказания услуг": "-",
            "Приоритет": "Средний",
            "Риск": "Средний",
            "Шанс победы": "Требуется проверка",
            "Рекомендация AI": "Проверить вручную: для этого источника нет глубокого API-анализа",
        }

    priority = extract_priority_label(analytics, title)
    amount = format_money(analytics.get("Сумма", ""))
    currency = analytics.get("Валюта") or "-"
    customer = analytics.get("Заказчик") or "-"
    delivery_term = analytics.get("Срок оказания услуг") or "-"
    risk = analytics.get("Риск") or analytics.get("Риск ") or "-"
    win_chance = analytics.get("Шанс победы") or "-"
    recommendation = analytics.get("Рекомендация AI") or "Проверить вручную"
    requirements = analytics.get("Требования") or ""

    if delivery_term not in ["-", ""]:
        delivery_term = str(delivery_term)
        if delivery_term.isdigit():
            delivery_term = delivery_term + " дней"

    message = (
        f"🚚 Новый логистический тендер\n\n"
        f"📌 Источник: {site}\n"
        f"🎯 Приоритет: {priority}\n\n"
        f"📋 {title}\n\n"
        f"🏢 Заказчик: {customer}\n"
        f"💰 Сумма: {amount}\n"
        f"💵 Валюта: {currency}\n"
        f"📅 Срок оказания: {delivery_term}\n\n"
        f"📈 Шанс победы: {win_chance}\n"
        f"⚠️ Риск: {risk}\n\n"
        f"🤖 AI рекомендация:\n{recommendation}\n"
    )

    if requirements:
        message += f"\n📄 Требования кратко:\n{requirements[:700]}\n"

    message += f"\n🔗 {url}"

    return message[:3900]


@app.get("/scan")
def scan():
    print("SCAN STARTED")

    found_total = 0
    new_total = 0
    duplicate_total = 0
    all_tenders = []
    seen_urls = set()

    message = "📊 AI Tender Agent Cargo V20 Document Fix Scan завершён\n\n"

    sources = [
        ("Tenderweek", parse_tenderweek),
        ("UZEX", parse_uzex),
        ("XT-Xarid", parse_xt_xarid),
    ]

    source_counts = {}

    for source_name, parser in sources:
        print("PARSING:", source_name)

        try:
            result = parser()
            source_counts[source_name] = len(result)
            all_tenders.extend(result)
            message += f"{source_name}: найдено услуг по перевозке {len(result)}\n"
        except Exception as e:
            source_counts[source_name] = 0
            message += f"{source_name}: ERROR\n"
            print(f"{source_name} ERROR:", e)

    message += "\n"

    for tender in all_tenders:
        url = tender.get("url")
        title = tender.get("title")

        if not url or url in seen_urls:
            continue

        seen_urls.add(url)

        if not is_real_cargo_tender(title, url):
            continue

        found_total += 1

        saved = save_to_sheet(tender["site"], title, url)

        if saved:
            new_total += 1
            send_telegram(format_tender_message(tender))
        else:
            duplicate_total += 1

    message += (
        f"Всего найдено: {found_total}\n"
        f"Новых сохранено: {new_total}\n"
        f"Дубликатов пропущено: {duplicate_total}"
    )

    send_telegram(message)

    return {
        "status": "success",
        "version": "cargo_v20_document_fix",
        "sources": source_counts,
        "found_total": found_total,
        "new_total": new_total,
        "duplicates": duplicate_total,
    }


@app.head("/scan")
def scan_head():
    return {}


@app.post("/webhook")
def webhook():
    return scan()


@app.get("/webhook")
def webhook_get():
    return scan()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        app,
        host="0.0.0.0",
        port=int(os.getenv("PORT", 10000)),
    )
