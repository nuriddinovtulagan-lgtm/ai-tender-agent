import os
import re
import json
import time
import threading
import traceback
from datetime import datetime
from urllib.parse import quote_plus

import requests
import gspread
from bs4 import BeautifulSoup
from fastapi import FastAPI
from google.oauth2.service_account import Credentials


APP_VERSION = "cargo_v28_4_uzex_restore"

app = FastAPI(title="AI Tender Agent", version=APP_VERSION)

# Render Start Command должен быть:
# uvicorn main_v2:app --host 0.0.0.0 --port $PORT

BOT_TOKEN = os.getenv("BOT_TOKEN") or os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID") or os.getenv("TELEGRAM_CHAT_ID")
GOOGLE_SHEET_ID = os.getenv("GOOGLE_SHEET_ID")
GOOGLE_CREDS_JSON = (
    os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON")
    or os.getenv("GOOGLE_CREDS_JSON")
)
WORKSHEET_NAME = os.getenv("WORKSHEET_NAME", "Тендеры")

REQUEST_TIMEOUT = 20
MAX_SOURCE_SECONDS = 70
MAX_TOTAL_SECONDS = 180

SCAN_STATE = {
    "running": False,
    "started_at": None,
    "finished_at": None,
    "last_trigger": None,
    "last_result": None,
    "last_error": None,
}
SCAN_LOCK = threading.Lock()


KEYWORDS = [
    "услуга по перевозке грузов", "услуги по перевозке грузов",
    "оказание услуг по перевозке грузов", "перевозка грузов",
    "перевозка товара", "перевозка товаров", "доставка грузов",
    "доставка товара", "доставка товаров", "грузоперевоз",
    "грузовые перевозки", "грузовой транспорт", "груз",
    "логистика", "логистические услуги", "логист",
    "транспорт", "транспортные услуги", "оказание транспортных услуг",
    "автотранспорт", "автотранспортные услуги", "автомобильные перевозки",
    "международные перевозки", "внутренние перевозки", "междугородние перевозки",
    "железнодорожные перевозки", "жд перевозки", "ж/д перевозки",
    "контейнерные перевозки", "контейнер", "мультимодальные перевозки",
    "интермодальные перевозки", "экспедирование", "экспедиторские услуги",
    "транспортно-экспедиционные услуги", "транспортная экспедиция",
    "экспедитор", "доставка", "склад", "складские услуги",
    "хранение груза", "погрузка", "разгрузка",
    "погрузочно-разгрузочные работы", "погрузо-разгрузочные работы",
    "спецтехника", "аренда спецтехники", "фура", "тягач",
    "полуприцеп", "рефрижератор", "изотермический", "бортовой автомобиль",
    "самосвал", "контейнеровоз", "таможенное оформление",
    "таможенный брокер", "таможенные услуги", "транзит",
    "freight", "freight forwarding", "cargo", "cargo transportation",
    "transportation", "transport services", "logistics", "logistics services",
    "delivery", "shipping", "forwarding", "warehouse", "customs clearance",
    "yuk tashish", "yuklarni tashish", "transport xizmati",
    "transport xizmatlari", "logistika", "yetkazib berish", "ombor",
    "юк ташиш", "юкларни ташиш", "транспорт хизмати",
    "транспорт хизматлари", "логистика", "етказиб бериш", "омбор",
]

SEARCH_WORDS = [
    "перевозка", "перевозка грузов", "транспорт", "транспортные услуги",
    "доставка", "логистика", "экспедирование", "склад", "спецтехника",
    "контейнер", "таможенное оформление", "freight", "cargo", "logistics",
    "yuk tashish", "transport xizmati", "logistika", "yetkazib berish",
    "юк ташиш", "транспорт хизмати", "логистика",
]

REJECT_WORDS = [
    "ремонт", "техническое обслуживание", "техобслуживание",
    "запчаст", "запасные части", "поставка автомобиля", "покупка автомобиля",
    "шина", "масло моторное", "страхование", "мебель", "канцеляр",
    "бетон", "строительные материалы", "компьютер", "принтер",
    "картридж", "кондиционер", "медицинское оборудование",
]


def now_str():
    return datetime.now().strftime("%d.%m.%Y %H:%M:%S")


def normalize_text(text: str) -> str:
    if not text:
        return ""
    text = str(text).lower().strip()
    text = re.sub(r"\s+", " ", text)
    return text


def is_logistics_tender(text: str) -> bool:
    t = normalize_text(text)
    if not t:
        return False

    for bad in REJECT_WORDS:
        if bad in t:
            return False

    return any(word.lower() in t for word in KEYWORDS)


def accept_reason(text: str) -> str:
    t = normalize_text(text)

    for bad in REJECT_WORDS:
        if bad in t:
            return f"rejected:{bad}"

    for good in KEYWORDS:
        if good.lower() in t:
            return f"accepted:{good}"

    return "no_transport_phrase"


def tender_key(item: dict) -> str:
    title = normalize_text(item.get("title", ""))
    url = normalize_text(item.get("url", ""))
    return f"{title}|{url}"


def safe_get(url, *, params=None, headers=None, timeout=REQUEST_TIMEOUT):
    default_headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AI-Tender-Agent/28.4",
        "Accept": "application/json,text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }
    if headers:
        default_headers.update(headers)

    return requests.get(url, params=params, headers=default_headers, timeout=timeout)


def send_telegram(text: str) -> bool:
    if not BOT_TOKEN or not CHAT_ID:
        print("Telegram variables missing")
        return False

    try:
        response = requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            json={
                "chat_id": CHAT_ID,
                "text": text[:3900],
                "disable_web_page_preview": True,
            },
            timeout=20,
        )
        print("TELEGRAM:", response.status_code, response.text[:200])
        return response.status_code == 200
    except Exception as e:
        print("TELEGRAM ERROR:", e)
        return False


def get_sheet():
    if not GOOGLE_CREDS_JSON:
        raise RuntimeError("GOOGLE_SERVICE_ACCOUNT_JSON / GOOGLE_CREDS_JSON is empty")
    if not GOOGLE_SHEET_ID:
        raise RuntimeError("GOOGLE_SHEET_ID is empty")

    info = json.loads(GOOGLE_CREDS_JSON)
    creds = Credentials.from_service_account_info(
        info,
        scopes=[
            "https://www.googleapis.com/auth/spreadsheets",
            "https://www.googleapis.com/auth/drive",
        ],
    )
    client = gspread.authorize(creds)
    book = client.open_by_key(GOOGLE_SHEET_ID)

    try:
        return book.worksheet(WORKSHEET_NAME)
    except Exception:
        return book.sheet1


def get_existing_keys(sheet):
    rows = sheet.get_all_values()
    keys = set()

    if not rows:
        return keys

    header = [normalize_text(h) for h in rows[0]]
    title_idx = None
    url_idx = None

    for i, h in enumerate(header):
        if h in ["название", "title", "тендер", "наименование"]:
            title_idx = i
        if h in ["ссылка", "url", "link"]:
            url_idx = i

    # Старый формат: Дата, Источник, Название, Ссылка, Статус
    if title_idx is None:
        title_idx = 2
    if url_idx is None:
        url_idx = 3

    for row in rows[1:]:
        title = row[title_idx] if len(row) > title_idx else ""
        url = row[url_idx] if len(row) > url_idx else ""
        if title or url:
            keys.add(f"{normalize_text(title)}|{normalize_text(url)}")

    return keys


def save_new_tenders(sheet, items):
    rows = []
    for item in items:
        rows.append([
            datetime.now().strftime("%d.%m.%Y %H:%M"),
            item.get("site", ""),
            item.get("title", ""),
            item.get("url", ""),
            "Новый",
            item.get("reason", ""),
        ])

    if rows:
        sheet.append_rows(rows, value_input_option="USER_ENTERED")
    return len(rows)


def add_unique(items: dict, item: dict):
    if not item.get("title") or not item.get("url"):
        return

    combined = f"{item.get('title', '')} {item.get('url', '')} {item.get('reason', '')}"
    if not is_logistics_tender(combined):
        return

    items[tender_key(item)] = item


# ---------------- Tenderweek ----------------

def parse_tenderweek():
    started = time.time()
    unique = {}
    base_url = "https://www.tenderweek.com/"

    pages = [base_url]
    for page in range(2, 6):
        pages.append(f"{base_url}?page={page}")

    for word in SEARCH_WORDS[:8]:
        q = quote_plus(word)
        pages.append(f"{base_url}?search={q}")
        pages.append(f"{base_url}?q={q}")

    for url in pages[:22]:
        if time.time() - started > MAX_SOURCE_SECONDS:
            print("Tenderweek stopped by source timeout")
            break

        try:
            print("Tenderweek GET:", url)
            r = safe_get(url, timeout=20)
            print("Tenderweek status:", r.status_code, "size:", len(r.text))

            if r.status_code != 200:
                continue

            soup = BeautifulSoup(r.text, "html.parser")
            for link in soup.find_all("a"):
                title = " ".join(link.get_text(" ", strip=True).split())
                href = link.get("href")

                if not title or not href or len(title) < 8:
                    continue

                full_url = requests.compat.urljoin(base_url, href)
                combined = f"{title} {full_url}"

                if "tender" not in full_url.lower() and not is_logistics_tender(combined):
                    continue

                if not is_logistics_tender(combined):
                    continue

                add_unique(unique, {
                    "site": "Tenderweek",
                    "title": title,
                    "url": full_url,
                    "reason": accept_reason(combined),
                })

        except Exception as e:
            print("Tenderweek error:", e)

    return list(unique.values())


# ---------------- XT-Xarid ----------------

def parse_xt_xarid():
    started = time.time()
    unique = {}
    base_url = "https://xt-xarid.uz/"

    pages = [base_url]
    for word in SEARCH_WORDS[:6]:
        q = quote_plus(word)
        pages.append(f"{base_url}?search={q}")
        pages.append(f"{base_url}?q={q}")

    for url in pages[:14]:
        if time.time() - started > MAX_SOURCE_SECONDS:
            print("XT-Xarid stopped by source timeout")
            break

        try:
            print("XT-Xarid GET:", url)
            r = safe_get(url, timeout=15)
            print("XT-Xarid status:", r.status_code, "size:", len(r.text))

            if r.status_code != 200:
                continue

            soup = BeautifulSoup(r.text, "html.parser")
            for link in soup.find_all("a"):
                title = " ".join(link.get_text(" ", strip=True).split())
                href = link.get("href")

                if not title or not href or len(title) < 6:
                    continue

                full_url = requests.compat.urljoin(base_url, href)
                combined = f"{title} {full_url}"

                if not is_logistics_tender(combined):
                    continue

                add_unique(unique, {
                    "site": "XT-Xarid",
                    "title": title,
                    "url": full_url,
                    "reason": accept_reason(combined),
                })

        except Exception as e:
            print("XT-Xarid error:", e)

    return list(unique.values())


# ---------------- UZEX RESTORE ----------------

def extract_list_from_json(data):
    if isinstance(data, list):
        return data

    if not isinstance(data, dict):
        return []

    candidates = []

    def walk(obj, depth=0):
        if depth > 5:
            return

        if isinstance(obj, list):
            if obj and all(isinstance(x, dict) for x in obj[:5]):
                candidates.append(obj)
            return

        if isinstance(obj, dict):
            for value in obj.values():
                walk(value, depth + 1)

    walk(data)

    if not candidates:
        return []

    candidates.sort(key=len, reverse=True)
    return candidates[0]


def get_lot_title(lot: dict) -> str:
    fields = [
        "name", "title", "lotName", "productName", "description",
        "product_name", "goodsName", "subject", "ruName", "uzName",
        "Name", "Title", "ProductName", "goods_name", "lot_name",
    ]

    for f in fields:
        value = lot.get(f)
        if value:
            return str(value)

    for value in lot.values():
        if isinstance(value, dict):
            nested = get_lot_title(value)
            if nested:
                return nested

    return ""


def get_lot_id(lot: dict) -> str:
    fields = [
        "id", "lotId", "tradeId", "number", "displayNo",
        "lot_id", "LotId", "ID", "Id", "trade_id", "lotNumber",
    ]

    for f in fields:
        value = lot.get(f)
        if value:
            return str(value)

    for value in lot.values():
        if isinstance(value, dict):
            nested = get_lot_id(value)
            if nested:
                return nested

    return ""


def parse_uzex_api():
    started = time.time()
    unique = {}

    api_urls = [
        "https://etender.uzex.uz/api/common/Trade/GetTrades",
        "https://etender.uzex.uz/api/common/Trade/GetTradeList",
        "https://etender.uzex.uz/api/common/Trade/GetList",
        "https://etender.uzex.uz/api/common/Lot/GetLots",
    ]

    keywords = [
        "transport",
        "yuk tashish",
        "logistika",
        "transport xizmati",
        "перевозка",
        "перевозка грузов",
        "транспорт",
        "транспортные услуги",
        "экспедиторские услуги",
    ]

    param_variants = []
    for keyword in keywords:
        param_variants.extend([
            {"search": keyword, "page": 1, "size": 50},
            {"keyword": keyword, "page": 1, "size": 50},
            {"Search": keyword, "Page": 1, "Size": 50},
            {"filter": keyword, "page": 1, "limit": 50},
        ])

    for api_url in api_urls:
        for params in param_variants:
            if time.time() - started > MAX_SOURCE_SECONDS:
                print("UZEX API stopped by source timeout")
                return list(unique.values())

            try:
                print("UZEX API GET:", api_url, params)
                r = safe_get(api_url, params=params, timeout=25)
                print("UZEX API status:", r.status_code, "size:", len(r.text))

                if r.status_code != 200:
                    continue

                try:
                    data = r.json()
                except Exception:
                    print("UZEX API not json")
                    continue

                lots = extract_list_from_json(data)
                print("UZEX API lots raw:", len(lots))

                for lot in lots:
                    if not isinstance(lot, dict):
                        continue

                    title = get_lot_title(lot)
                    lot_id = get_lot_id(lot)

                    lot_preview = json.dumps(lot, ensure_ascii=False)[:1200]
                    combined = f"{title} {lot_preview}"

                    if not title:
                        continue

                    if not is_logistics_tender(combined):
                        continue

                    lot_url = "https://etender.uzex.uz"
                    if lot_id:
                        lot_url = f"https://etender.uzex.uz/lot/{lot_id}"

                    add_unique(unique, {
                        "site": "UZEX",
                        "title": title,
                        "url": lot_url,
                        "reason": accept_reason(combined),
                    })

            except Exception as e:
                print("UZEX API error:", api_url, params, e)

    return list(unique.values())


def parse_uzex_html():
    started = time.time()
    unique = {}

    base_urls = [
        "https://etender.uzex.uz/lots/1/0",
        "https://etender.uzex.uz/",
        "https://xarid.uzex.uz/",
    ]

    pages = []
    for base in base_urls:
        pages.append(base)
        for word in SEARCH_WORDS[:8]:
            q = quote_plus(word)
            pages.append(f"{base}?search={q}")
            pages.append(f"{base}?q={q}")
            pages.append(f"{base}?keyword={q}")

    for url in pages[:35]:
        if time.time() - started > MAX_SOURCE_SECONDS:
            print("UZEX HTML stopped by source timeout")
            break

        try:
            print("UZEX HTML GET:", url)
            r = safe_get(url, timeout=20)
            print("UZEX HTML status:", r.status_code, "size:", len(r.text))

            if r.status_code != 200:
                continue

            soup = BeautifulSoup(r.text, "html.parser")

            for link in soup.find_all("a"):
                title = " ".join(link.get_text(" ", strip=True).split())
                href = link.get("href")

                if not title or not href or len(title) < 6:
                    continue

                full_url = requests.compat.urljoin("https://etender.uzex.uz/", href)
                combined = f"{title} {full_url}"

                if not is_logistics_tender(combined):
                    continue

                add_unique(unique, {
                    "site": "UZEX",
                    "title": title,
                    "url": full_url,
                    "reason": accept_reason(combined),
                })

        except Exception as e:
            print("UZEX HTML error:", e)

    return list(unique.values())


def parse_uzex():
    unique = {}

    try:
        api_items = parse_uzex_api()
        print("UZEX API accepted:", len(api_items))
        for item in api_items:
            unique[tender_key(item)] = item
    except Exception as e:
        print("UZEX API main error:", e)
        print(traceback.format_exc())

    try:
        html_items = parse_uzex_html()
        print("UZEX HTML accepted:", len(html_items))
        for item in html_items:
            unique[tender_key(item)] = item
    except Exception as e:
        print("UZEX HTML main error:", e)
        print(traceback.format_exc())

    return list(unique.values())


# ---------------- SCAN CORE ----------------

def run_scan(trigger="manual"):
    scan_started = time.time()

    result = {
        "status": "success",
        "version": APP_VERSION,
        "sources": {"Tenderweek": 0, "UZEX": 0, "XT-Xarid": 0},
        "found_total": 0,
        "new_total": 0,
        "duplicates": 0,
        "errors": [],
    }

    all_items = []

    print("SCAN STARTED", APP_VERSION, trigger)

    sources = [
        ("Tenderweek", parse_tenderweek),
        ("UZEX", parse_uzex),
        ("XT-Xarid", parse_xt_xarid),
    ]

    for source_name, parser in sources:
        if time.time() - scan_started > MAX_TOTAL_SECONDS:
            result["errors"].append("Total scan timeout reached")
            break

        try:
            items = parser()
            result["sources"][source_name] = len(items)
            all_items.extend(items)
            print(f"{source_name} DONE:", len(items))
        except Exception as e:
            error_text = f"{source_name}: {str(e)}"
            result["errors"].append(error_text)
            print(error_text)
            print(traceback.format_exc())

    unique = {}
    for item in all_items:
        combined = f"{item.get('title', '')} {item.get('url', '')} {item.get('reason', '')}"
        if is_logistics_tender(combined):
            unique[tender_key(item)] = item

    all_items = list(unique.values())
    result["found_total"] = len(all_items)

    try:
        sheet = get_sheet()
        existing_keys = get_existing_keys(sheet)

        new_items = []
        duplicates = 0

        for item in all_items:
            if tender_key(item) in existing_keys:
                duplicates += 1
            else:
                new_items.append(item)

        result["new_total"] = save_new_tenders(sheet, new_items)
        result["duplicates"] = duplicates

        for item in new_items[:10]:
            send_telegram(
                f"🆕 Новый логистический тендер\n\n"
                f"📌 {item.get('site')}\n\n"
                f"{item.get('title')}\n\n"
                f"{item.get('url')}"
            )

    except Exception as e:
        result["status"] = "warning"
        result["errors"].append(f"Google Sheets: {str(e)}")
        print("GOOGLE SHEETS ERROR:", e)
        print(traceback.format_exc())

    message = (
        f"📊 AI Tender Agent\n"
        f"{APP_VERSION}\n"
        f"Scan завершён\n\n"
        f"Tenderweek: {result['sources']['Tenderweek']}\n"
        f"UZEX: {result['sources']['UZEX']}\n"
        f"XT-Xarid: {result['sources']['XT-Xarid']}\n\n"
        f"Всего найдено: {result['found_total']}\n"
        f"Новых сохранено: {result['new_total']}\n"
        f"Дубликатов пропущено: {result['duplicates']}\n"
        f"Ошибок: {len(result['errors'])}"
    )

    if result["errors"]:
        message += "\n\nОшибки:\n" + "\n".join(result["errors"][:5])

    send_telegram(message)

    print("SCAN FINISHED", result)
    return result


def background_scan_worker(trigger):
    try:
        result = run_scan(trigger)
        with SCAN_LOCK:
            SCAN_STATE["last_result"] = result
            SCAN_STATE["last_error"] = None

    except Exception as e:
        err = traceback.format_exc()
        print("BACKGROUND WORKER ERROR:", err)
        with SCAN_LOCK:
            SCAN_STATE["last_error"] = str(e)

        send_telegram(
            f"❌ AI Tender Agent error\n\n"
            f"Version: {APP_VERSION}\n"
            f"Trigger: {trigger}\n"
            f"Error: {str(e)}"
        )

    finally:
        with SCAN_LOCK:
            SCAN_STATE["running"] = False
            SCAN_STATE["finished_at"] = now_str()
        print("BACKGROUND WORKER FINALLY: running=False")


def start_background_scan(trigger="manual_http"):
    with SCAN_LOCK:
        if SCAN_STATE["running"]:
            return {
                "status": "already_running",
                "version": APP_VERSION,
                "running": True,
                "started_at": SCAN_STATE["started_at"],
                "message": "Scan already running",
            }

        SCAN_STATE["running"] = True
        SCAN_STATE["started_at"] = now_str()
        SCAN_STATE["finished_at"] = None
        SCAN_STATE["last_trigger"] = trigger
        SCAN_STATE["last_error"] = None
        SCAN_STATE["last_result"] = None

    thread = threading.Thread(
        target=background_scan_worker,
        args=(trigger,),
        daemon=True,
    )
    thread.start()

    return {
        "status": "accepted",
        "version": APP_VERSION,
        "running": True,
        "message": "Scan started in background. Check Telegram, Google Sheets, or /scan_status.",
        "started_at": SCAN_STATE["started_at"],
    }


# ---------------- ENDPOINTS ----------------

@app.get("/")
def home():
    return {"status": "AI Tender Agent is running", "version": APP_VERSION}


@app.head("/")
def head_home():
    return {}


@app.get("/version")
def version():
    return {"version": APP_VERSION}


@app.get("/health")
def health():
    result = {"status": "ok", "telegram": False, "google_sheets": False, "errors": []}

    try:
        if not BOT_TOKEN or not CHAT_ID:
            raise RuntimeError("BOT_TOKEN/CHAT_ID missing")
        tg_response = requests.get(f"https://api.telegram.org/bot{BOT_TOKEN}/getMe", timeout=10)
        result["telegram"] = tg_response.status_code == 200
    except Exception as e:
        result["errors"].append("Telegram error: " + str(e))

    try:
        sh = get_sheet()
        sh.row_values(1)
        result["google_sheets"] = True
    except Exception as e:
        result["errors"].append("Google Sheets error: " + str(e))

    if result["errors"]:
        result["status"] = "warning"

    return result


@app.get("/scan")
def scan():
    return start_background_scan("cron_http")


@app.get("/scan_start")
def scan_start():
    return start_background_scan("manual_http")


@app.get("/scan_status")
def scan_status():
    return {
        "status": "ok",
        "version": APP_VERSION,
        "running": SCAN_STATE["running"],
        "started_at": SCAN_STATE["started_at"],
        "finished_at": SCAN_STATE["finished_at"],
        "last_trigger": SCAN_STATE["last_trigger"],
        "last_result": SCAN_STATE["last_result"],
        "last_error": SCAN_STATE["last_error"],
    }


@app.get("/test_filter")
def test_filter():
    tests = [
        "Услуга по перевозке грузов",
        "Оказание транспортных услуг",
        "Закупка бетона для АЭС",
        "Поставка мебели",
        "Транспортно-экспедиционные услуги",
        "Yuk tashish xizmatlari",
        "Cargo transportation services",
        "Таможенное оформление и экспедирование",
    ]
    return {t: is_logistics_tender(t) for t in tests}


@app.get("/debug_items")
def debug_items():
    items = []
    errors = []

    for name, parser in [
        ("Tenderweek", parse_tenderweek),
        ("UZEX", parse_uzex),
        ("XT-Xarid", parse_xt_xarid),
    ]:
        try:
            parsed = parser()
            items.extend(parsed)
        except Exception as e:
            errors.append(f"{name}: {str(e)}")

    unique = {}
    for item in items:
        unique[tender_key(item)] = item

    return {
        "version": APP_VERSION,
        "count": len(unique),
        "items": list(unique.values())[:100],
        "errors": errors,
    }


@app.get("/debug_uzex")
def debug_uzex():
    items = parse_uzex()
    return {
        "version": APP_VERSION,
        "count": len(items),
        "items": items[:100],
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", 10000)))
