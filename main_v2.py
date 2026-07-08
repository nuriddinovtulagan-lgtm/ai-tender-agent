import os
import re
import json
import threading
import traceback
from datetime import datetime

import requests
import gspread
from bs4 import BeautifulSoup
from fastapi import FastAPI
from google.oauth2.service_account import Credentials


APP_VERSION = "cargo_v28_3_stable_background"

app = FastAPI(title="AI Tender Agent", version=APP_VERSION)

# Поддерживает оба варианта названий переменных, чтобы Render не сломался
BOT_TOKEN = os.getenv("BOT_TOKEN") or os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID") or os.getenv("TELEGRAM_CHAT_ID")
GOOGLE_SHEET_ID = os.getenv("GOOGLE_SHEET_ID")
GOOGLE_CREDS_JSON = (
    os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON")
    or os.getenv("GOOGLE_CREDS_JSON")
)

WORKSHEET_NAME = os.getenv("WORKSHEET_NAME", "Тендеры")

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
]

REJECT_WORDS = [
    "ремонт", "техническое обслуживание", "техобслуживание", "запчаст",
    "поставка автомобиля", "покупка автомобиля", "шина", "масло моторное",
    "страхование", "мебель", "канцеляр", "бетон", "строительные материалы",
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
    try:
        rows = sheet.get_all_values()
    except Exception as e:
        print("GET SHEET VALUES ERROR:", e)
        return set()

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
    if title_idx is None and len(header) >= 3:
        title_idx = 2
    if url_idx is None and len(header) >= 4:
        url_idx = 3

    for row in rows[1:]:
        title = row[title_idx] if title_idx is not None and len(row) > title_idx else ""
        url = row[url_idx] if url_idx is not None and len(row) > url_idx else ""
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


def collect_links(base_url, pages_to_scan, site_name, min_title_len=6, require_tender_in_url=False, max_pages=30):
    headers = {"User-Agent": "Mozilla/5.0 (compatible; AI-Tender-Agent/1.0)"}
    tenders = []
    seen_urls = set()

    for url in pages_to_scan[:max_pages]:
        try:
            print(f"{site_name} GET:", url)
            r = requests.get(url, headers=headers, timeout=20)
            if r.status_code != 200:
                print(f"{site_name} STATUS:", r.status_code)
                continue

            soup = BeautifulSoup(r.text, "html.parser")

            for link in soup.find_all("a"):
                title = " ".join(link.get_text(" ", strip=True).split())
                href = link.get("href")

                if not title or len(title) < min_title_len or not href:
                    continue

                full_url = requests.compat.urljoin(base_url, href)
                combined_text = f"{title} {full_url}"

                if require_tender_in_url and "tender" not in full_url.lower():
                    if not is_logistics_tender(combined_text):
                        continue

                if not is_logistics_tender(combined_text):
                    continue

                if full_url in seen_urls:
                    continue

                seen_urls.add(full_url)
                tenders.append({
                    "site": site_name,
                    "title": title,
                    "url": full_url,
                    "reason": accept_reason(combined_text),
                })

        except Exception as e:
            print(f"{site_name.upper()} ERROR:", e)

    return tenders


def parse_tenderweek():
    base_url = "https://www.tenderweek.com/"
    pages_to_scan = [base_url]

    # Ограничиваем источник, чтобы не зависал
    for word in SEARCH_WORDS[:8]:
        pages_to_scan.extend([
            f"{base_url}?search={word}",
            f"{base_url}?q={word}",
        ])

    return collect_links(
        base_url,
        pages_to_scan,
        "Tenderweek",
        min_title_len=10,
        require_tender_in_url=True,
        max_pages=20,
    )


def parse_xt_xarid():
    base_url = "https://xt-xarid.uz/"
    pages_to_scan = [base_url]

    for word in SEARCH_WORDS[:8]:
        pages_to_scan.extend([
            f"{base_url}?search={word}",
            f"{base_url}?q={word}",
        ])

    return collect_links(
        base_url,
        pages_to_scan,
        "XT-Xarid",
        min_title_len=6,
        max_pages=15,
    )


def parse_uzex_api():
    items = []
    api_urls = [
        "https://etender.uzex.uz/api/common/Trade/GetTrades",
        "https://etender.uzex.uz/api/common/Trade/GetTradeList",
    ]

    keywords = [
        "yuk tashish",
        "transport",
        "logistika",
        "перевозка грузов",
        "транспортные услуги",
        "экспедиторские услуги",
    ]

    for keyword in keywords:
        for api_url in api_urls:
            try:
                print("UZEX API:", api_url, keyword)
                r = requests.get(
                    api_url,
                    params={"search": keyword, "page": 1, "size": 20},
                    timeout=25,
                )
                if r.status_code != 200:
                    print("UZEX API STATUS:", r.status_code)
                    continue

                data = r.json()
                possible_lists = []

                if isinstance(data, list):
                    possible_lists = data
                elif isinstance(data, dict):
                    for key in ["data", "items", "result", "trades", "content"]:
                        value = data.get(key)
                        if isinstance(value, list):
                            possible_lists = value
                            break
                        if isinstance(value, dict):
                            for subkey in ["data", "items", "result", "content"]:
                                if isinstance(value.get(subkey), list):
                                    possible_lists = value.get(subkey)
                                    break

                for lot in possible_lists:
                    if not isinstance(lot, dict):
                        continue

                    title = (
                        lot.get("name")
                        or lot.get("title")
                        or lot.get("lotName")
                        or lot.get("productName")
                        or lot.get("description")
                        or ""
                    )
                    lot_id = (
                        lot.get("id")
                        or lot.get("lotId")
                        or lot.get("tradeId")
                        or lot.get("number")
                        or lot.get("displayNo")
                        or ""
                    )

                    if not title:
                        continue

                    combined = f"{title} {lot_id}"
                    if not is_logistics_tender(combined):
                        continue

                    lot_url = f"https://etender.uzex.uz/lot/{lot_id}" if lot_id else "https://etender.uzex.uz"

                    items.append({
                        "site": "UZEX",
                        "title": title,
                        "url": lot_url,
                        "reason": accept_reason(combined),
                    })

            except Exception as e:
                print("UZEX API ERROR:", api_url, keyword, e)

    unique = {}
    for item in items:
        unique[tender_key(item)] = item

    return list(unique.values())


def parse_uzex_html():
    base_urls = [
        "https://etender.uzex.uz/lots/1/0",
        "https://etender.uzex.uz/",
        "https://xarid.uzex.uz/",
    ]
    pages_to_scan = []

    for base_url in base_urls:
        pages_to_scan.append(base_url)
        for word in SEARCH_WORDS[:8]:
            pages_to_scan.extend([
                f"{base_url}?search={word}",
                f"{base_url}?q={word}",
            ])

    return collect_links(
        "https://etender.uzex.uz/",
        pages_to_scan,
        "UZEX",
        min_title_len=6,
        max_pages=25,
    )


def parse_uzex():
    items = []
    try:
        items.extend(parse_uzex_api())
    except Exception as e:
        print("UZEX API MAIN ERROR:", e)

    try:
        items.extend(parse_uzex_html())
    except Exception as e:
        print("UZEX HTML MAIN ERROR:", e)

    unique = {}
    for item in items:
        unique[tender_key(item)] = item

    return list(unique.values())


def run_scan(trigger="manual"):
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

    sources = [
        ("Tenderweek", parse_tenderweek),
        ("UZEX", parse_uzex),
        ("XT-Xarid", parse_xt_xarid),
    ]

    print("SCAN STARTED", APP_VERSION, trigger)

    for source_name, parser in sources:
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
        if is_logistics_tender(f"{item.get('title', '')} {item.get('url', '')}"):
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
        f"📊 AI Tender Agent {APP_VERSION}\n"
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
        message += "\n\n" + "\n".join(result["errors"][:5])

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
        send_telegram(f"❌ AI Tender Agent error\n\nVersion: {APP_VERSION}\nError: {str(e)}")
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

    thread = threading.Thread(target=background_scan_worker, args=(trigger,), daemon=True)
    thread.start()

    return {
        "status": "accepted",
        "version": APP_VERSION,
        "running": True,
        "message": "Scan started in background. Check Telegram, Google Sheets, or /scan_status.",
        "started_at": SCAN_STATE["started_at"],
    }


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
    for parser in [parse_tenderweek, parse_uzex, parse_xt_xarid]:
        try:
            items.extend(parser())
        except Exception as e:
            items.append({"site": "ERROR", "title": str(e), "url": ""})

    unique = {}
    for item in items:
        unique[tender_key(item)] = item

    return {"version": APP_VERSION, "count": len(unique), "items": list(unique.values())}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", 10000)))
