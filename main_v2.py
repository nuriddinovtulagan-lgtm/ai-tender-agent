import os
import json
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


app = FastAPI(title="AI Tender Agent Cargo V17 + Document Analyzer V2 Debug Files")

BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")
GOOGLE_SHEET_ID = os.getenv("GOOGLE_SHEET_ID")


SERVICE_PHRASES = [
    "перевозка грузов", "перевозке грузов", "грузоперевоз", "грузовые перевозки",
    "доставка грузов", "доставка груза", "доставка товара автотранспортом",
    "транспортные услуги", "оказание транспортных услуг", "услуги транспорта",
    "услуги по перевозке", "услуги перевозки",
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
]

SOFT_BAD_WORDS = ["товар", "материал", "изделие", "деталь", "агрегат", "насос", "кабель", "труба", "краска", "масло"]

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


def save_to_sheet(site, title, url):
    try:
        if tender_exists(url):
            return False

        sheet = get_sheet()

        sheet.append_row([
            datetime.now().strftime("%d.%m.%Y %H:%M"),
            "AI Agent",
            url,
            site,
            "Новый",
            "Средний",
            "Проверить лот: найдено по Cargo V17 Balanced Search",
            title,
        ])

        return True

    except Exception as e:
        print("GOOGLE SHEETS ERROR:", e)
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


@app.get("/")
def home():
    return {"status": "AI Tender Agent Cargo V17 + Document Analyzer V2 Debug Files is running"}


@app.head("/")
def head_home():
    return {}


@app.get("/version")
def version():
    return {
        "version": "cargo_v17_doc_analyzer_v2_debug_files",
        "status": "running"
    }


@app.get("/analyze_doc_test")
def analyze_doc_test():
    return {
        "status": "ok",
        "version": "document_analyzer_v1",
        "pdf_reader": True,
        "docx_reader": True,
        "xlsx_reader": True,
        "modules": {
            "PyPDF2": True,
            "python_docx": True,
            "openpyxl": True
        }
    }


@app.get("/debug_lot_files")
def debug_lot_files(url: str):
    try:
        r = requests.get(url, headers=get_headers(), timeout=20)

        result = {
            "status": "ok",
            "version": "document_analyzer_v2_debug_files",
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
            "version": "document_analyzer_v2_debug_files",
            "lot_url": url,
            "error": str(e)
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
    }


@app.get("/debug_sources")
def debug_sources():
    result = {
        "version": "cargo_v17_doc_analyzer_v2_debug_files",
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
        "version": "cargo_v17_doc_analyzer_v2_debug_files",
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
        "version": "cargo_v17_doc_analyzer_v2_debug_files",
        "total_candidates_sample": len(all_items),
        "accepted_sample": len(accepted),
        "rejected_sample": len(rejected),
        "items": all_items[:75],
    }


@app.get("/debug_uzex")
def debug_uzex():
    url = "https://apietender.uzex.uz/api/common/TradeList"
    payload = {"TypeId": 1, "From": 1, "To": 10, "System_Id": 0}

    try:
        r = requests.post(url, headers=get_headers(json_mode=True), json=payload, timeout=12)
        return {
            "version": "cargo_v17_doc_analyzer_v2_debug_files",
            "url": url,
            "payload": payload,
            "status_code": r.status_code,
            "content_type": r.headers.get("content-type", ""),
            "size": len(r.text),
            "text_start": r.text[:1500],
        }
    except Exception as e:
        return {"version": "cargo_v17_doc_analyzer_v2_debug_files", "url": url, "error": str(e)}


@app.get("/debug_xt")
def debug_xt():
    url = "https://api.xt-xarid.uz/rpc"
    payload = {
        "id": 1,
        "jsonrpc": "2.0",
        "method": "ref",
        "params": {
            "ref": "ref_tender_public",
            "op": "read",
            "limit": 10,
            "offset": 0,
            "filters": {},
        },
    }

    try:
        r = requests.post(url, headers=get_headers(json_mode=True), json=payload, timeout=12)
        return {
            "version": "cargo_v17_doc_analyzer_v2_debug_files",
            "url": url,
            "payload": payload,
            "status_code": r.status_code,
            "content_type": r.headers.get("content-type", ""),
            "size": len(r.text),
            "text_start": r.text[:1500],
        }
    except Exception as e:
        return {"version": "cargo_v17_doc_analyzer_v2_debug_files", "url": url, "error": str(e)}


@app.get("/scan")
def scan():
    print("SCAN STARTED")

    found_total = 0
    new_total = 0
    duplicate_total = 0
    all_tenders = []
    seen_urls = set()

    message = "📊 AI Tender Agent Cargo V17 Balanced Search Scan завершён\n\n"

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
            send_telegram(
                f"🚚 Новый лот по услугам перевозки / логистике\n\n"
                f"📌 Источник: {tender['site']}\n\n"
                f"📋 {title}\n\n"
                f"🔗 {url}"
            )
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
        "version": "cargo_v17_doc_analyzer_v2_debug_files",
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
    
