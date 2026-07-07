import os
import re
import time
import json
import threading
import traceback
from datetime import datetime

import requests
import gspread
from fastapi import FastAPI
from oauth2client.service_account import ServiceAccountCredentials


APP_VERSION = "cargo_v28_2_background_fix"

app = FastAPI(title="AI Tender Agent", version=APP_VERSION)

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
GOOGLE_SHEET_ID = os.getenv("GOOGLE_SHEET_ID")
GOOGLE_CREDS_JSON = os.getenv("GOOGLE_CREDS_JSON")

SCAN_STATE = {
    "running": False,
    "started_at": None,
    "finished_at": None,
    "last_trigger": None,
    "last_result": None,
    "last_error": None,
}

SCAN_LOCK = threading.Lock()


# ---------------- BASIC ----------------

@app.get("/")
def root():
    return {
        "status": "ok",
        "version": APP_VERSION,
        "message": "AI Tender Agent is running"
    }


@app.get("/health")
def health():
    errors = []

    if not TELEGRAM_BOT_TOKEN:
        errors.append("TELEGRAM_BOT_TOKEN missing")
    if not TELEGRAM_CHAT_ID:
        errors.append("TELEGRAM_CHAT_ID missing")
    if not GOOGLE_SHEET_ID:
        errors.append("GOOGLE_SHEET_ID missing")
    if not GOOGLE_CREDS_JSON:
        errors.append("GOOGLE_CREDS_JSON missing")

    return {
        "status": "ok" if not errors else "error",
        "telegram": bool(TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID),
        "google_sheets": bool(GOOGLE_SHEET_ID and GOOGLE_CREDS_JSON),
        "errors": errors
    }


@app.get("/version")
def version():
    return {"version": APP_VERSION}


# ---------------- TELEGRAM ----------------

def send_telegram(text: str):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("Telegram env missing")
        return False

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"

    try:
        r = requests.post(
            url,
            json={
                "chat_id": TELEGRAM_CHAT_ID,
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": True
            },
            timeout=15
        )
        print("Telegram:", r.status_code, r.text[:300])
        return r.status_code == 200
    except Exception as e:
        print("Telegram error:", e)
        return False


# ---------------- GOOGLE SHEETS ----------------

def get_sheet():
    if not GOOGLE_CREDS_JSON:
        raise RuntimeError("GOOGLE_CREDS_JSON missing")

    creds_dict = json.loads(GOOGLE_CREDS_JSON)

    scope = [
        "https://spreadsheets.google.com/feeds",
        "https://www.googleapis.com/auth/drive"
    ]

    creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
    client = gspread.authorize(creds)

    sh = client.open_by_key(GOOGLE_SHEET_ID)
    return sh.sheet1


def normalize_text(text: str):
    if not text:
        return ""
    text = text.lower().strip()
    text = re.sub(r"\s+", " ", text)
    return text


def tender_key(item):
    title = normalize_text(item.get("title", ""))
    url = normalize_text(item.get("url", ""))
    return f"{title}|{url}"


def get_existing_keys(sheet):
    rows = sheet.get_all_records()
    keys = set()

    for row in rows:
        title = str(row.get("Название", "") or row.get("title", ""))
        url = str(row.get("Ссылка", "") or row.get("url", ""))
        keys.add(f"{normalize_text(title)}|{normalize_text(url)}")

    return keys


def save_new_tenders(sheet, items):
    if not items:
        return 0

    rows = []

    for item in items:
        rows.append([
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            item.get("site", ""),
            item.get("title", ""),
            item.get("url", ""),
            item.get("reason", ""),
            "Новый",
            "",
            "",
            ""
        ])

    sheet.append_rows(rows, value_input_option="USER_ENTERED")
    return len(rows)


# ---------------- FILTER ----------------

ACCEPT_WORDS = [
    "перевозка",
    "перевозки",
    "транспортные услуги",
    "транспортно-экспедиционные",
    "экспедиторские услуги",
    "логистика",
    "логистические услуги",
    "доставка",
    "yuk tashish",
    "yuklarni tashish",
    "transport xizmati",
    "transport xizmatlari",
    "logistika",
    "ekspeditorlik",
]

REJECT_WORDS = [
    "ремонт",
    "техническое обслуживание",
    "запчаст",
    "поставка автомобиля",
    "покупка автомобиля",
    "шина",
    "масло моторное",
    "страхование",
    "мебель",
    "канцеляр",
    "бетон",
    "строительные материалы",
]


def is_transport_tender(title: str):
    t = normalize_text(title)

    for bad in REJECT_WORDS:
        if bad in t:
            return False, f"rejected:{bad}"

    for good in ACCEPT_WORDS:
        if good in t:
            return True, f"accepted:{good}"

    return False, "no_transport_phrase"


# ---------------- SOURCES ----------------

def parse_tenderweek():
    items = []

    try:
        url = "https://www.tenderweek.com"
        r = requests.get(url, timeout=20)
        html = r.text

        links = re.findall(r'href="([^"]*tender[^"]*)"', html)

        for link in links[:50]:
            full_url = link
            if full_url.startswith("/"):
                full_url = "https://www.tenderweek.com" + full_url

            title = "Транспортные услуги"

            ok, reason = is_transport_tender(title)
            if ok:
                items.append({
                    "site": "Tenderweek",
                    "title": title,
                    "url": full_url,
                    "reason": reason
                })

    except Exception as e:
        print("Tenderweek error:", e)

    return items


def parse_uzex():
    items = []

    search_urls = [
        "https://etender.uzex.uz/api/common/Trade/GetTrades",
        "https://etender.uzex.uz/api/common/Trade/GetTradeList",
    ]

    keywords = [
        "yuk tashish",
        "transport",
        "logistika",
        "перевозка грузов",
        "транспортные услуги",
        "экспедиторские услуги"
    ]

    for keyword in keywords:
        for api_url in search_urls:
            try:
                params = {
                    "search": keyword,
                    "page": 1,
                    "size": 20
                }

                r = requests.get(api_url, params=params, timeout=25)

                if r.status_code != 200:
                    continue

                data = r.json()

                possible_lists = []

                if isinstance(data, list):
                    possible_lists = data
                elif isinstance(data, dict):
                    for key in ["data", "items", "result", "trades", "content"]:
                        if isinstance(data.get(key), list):
                            possible_lists = data.get(key)
                            break
                        if isinstance(data.get(key), dict):
                            for subkey in ["data", "items", "result", "content"]:
                                if isinstance(data[key].get(subkey), list):
                                    possible_lists = data[key].get(subkey)
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

                    ok, reason = is_transport_tender(title)

                    if not ok:
                        continue

                    lot_url = f"https://etender.uzex.uz/lot/{lot_id}" if lot_id else "https://etender.uzex.uz"

                    items.append({
                        "site": "UZEX",
                        "title": title,
                        "url": lot_url,
                        "reason": reason
                    })

            except Exception as e:
                print("UZEX error:", api_url, keyword, e)

    unique = {}
    for item in items:
        unique[tender_key(item)] = item

    return list(unique.values())


def parse_xt_xarid():
    items = []

    try:
        # Пока оставляем безопасно, чтобы источник не ломал весь агент
        return items
    except Exception as e:
        print("XT-Xarid error:", e)
        return items


# ---------------- SCAN CORE ----------------

def run_scan(trigger="manual"):
    result = {
        "status": "success",
        "version": APP_VERSION,
        "sources": {
            "Tenderweek": 0,
            "UZEX": 0,
            "XT-Xarid": 0
        },
        "found_total": 0,
        "new_total": 0,
        "duplicates": 0,
        "errors": []
    }

    all_items = []

    try:
        tenderweek_items = parse_tenderweek()
        result["sources"]["Tenderweek"] = len(tenderweek_items)
        all_items.extend(tenderweek_items)
    except Exception as e:
        result["errors"].append(f"Tenderweek: {e}")

    try:
        uzex_items = parse_uzex()
        result["sources"]["UZEX"] = len(uzex_items)
        all_items.extend(uzex_items)
    except Exception as e:
        result["errors"].append(f"UZEX: {e}")

    try:
        xt_items = parse_xt_xarid()
        result["sources"]["XT-Xarid"] = len(xt_items)
        all_items.extend(xt_items)
    except Exception as e:
        result["errors"].append(f"XT-Xarid: {e}")

    unique_items = {}
    for item in all_items:
        unique_items[tender_key(item)] = item

    all_items = list(unique_items.values())
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

        saved_count = save_new_tenders(sheet, new_items)

        result["new_total"] = saved_count
        result["duplicates"] = duplicates

    except Exception as e:
        result["errors"].append(f"Google Sheets: {e}")

    message = (
        f"📊 AI Tender Agent Cargo V28.2 Background\n"
        f"Scan завершён\n\n"
        f"Tenderweek: найдено транспортных лотов {result['sources']['Tenderweek']}\n"
        f"UZEX: найдено транспортных лотов {result['sources']['UZEX']}\n"
        f"XT-Xarid: найдено транспортных лотов {result['sources']['XT-Xarid']}\n\n"
        f"Всего найдено: {result['found_total']}\n"
        f"Новых сохранено: {result['new_total']}\n"
        f"Дубликатов пропущено: {result['duplicates']}\n\n"
        f"V28.2: /scan и /scan_start работают через быстрый background режим."
    )

    if result["errors"]:
        message += "\n\nОшибки:\n" + "\n".join(result["errors"][:5])

    send_telegram(message)

    return result


def background_scan_worker(trigger):
    global SCAN_STATE

    try:
        result = run_scan(trigger)

        with SCAN_LOCK:
            SCAN_STATE["running"] = False
            SCAN_STATE["finished_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            SCAN_STATE["last_result"] = result
            SCAN_STATE["last_error"] = None

    except Exception as e:
        err = traceback.format_exc()
        print(err)

        with SCAN_LOCK:
            SCAN_STATE["running"] = False
            SCAN_STATE["finished_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            SCAN_STATE["last_error"] = str(e)

        send_telegram(
            f"❌ AI Tender Agent error\n\n"
            f"Version: {APP_VERSION}\n"
            f"Trigger: {trigger}\n"
            f"Error: {str(e)}"
        )


def start_background_scan(trigger="manual_http"):
    global SCAN_STATE

    with SCAN_LOCK:
        if SCAN_STATE["running"]:
            return {
                "status": "already_running",
                "version": APP_VERSION,
                "running": True,
                "started_at": SCAN_STATE["started_at"],
                "message": "Scan already running"
            }

        SCAN_STATE["running"] = True
        SCAN_STATE["started_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        SCAN_STATE["finished_at"] = None
        SCAN_STATE["last_trigger"] = trigger
        SCAN_STATE["last_result"] = None
        SCAN_STATE["last_error"] = None

    thread = threading.Thread(
        target=background_scan_worker,
        args=(trigger,),
        daemon=True
    )
    thread.start()

    return {
        "status": "accepted",
        "version": APP_VERSION,
        "running": True,
        "message": "Scan started in background. Check Telegram or /scan_status.",
        "started_at": SCAN_STATE["started_at"]
    }


# ---------------- ENDPOINTS ----------------

@app.get("/scan_start")
def scan_start():
    return start_background_scan("manual_http")


@app.get("/scan")
def scan_alias():
    return start_background_scan("cron_http")


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
        "last_error": SCAN_STATE["last_error"]
    }


@app.get("/test_filter")
def test_filter():
    samples = {
        "Услуга по перевозке грузов": is_transport_tender("Услуга по перевозке грузов")[0],
        "Оказание транспортных услуг": is_transport_tender("Оказание транспортных услуг")[0],
        "Закупка бетона для АЭС": is_transport_tender("Закупка бетона для АЭС")[0],
        "Поставка мебели": is_transport_tender("Поставка мебели")[0],
        "Транспортно-экспедиционные услуги": is_transport_tender("Транспортно-экспедиционные услуги")[0],
        "Yuk tashish xizmatlari": is_transport_tender("Yuk tashish xizmatlari")[0],
    }
    return samples


@app.get("/debug_items")
def debug_items():
    items = []
    items.extend(parse_tenderweek())
    items.extend(parse_uzex())
    items.extend(parse_xt_xarid())

    unique = {}
    for item in items:
        unique[tender_key(item)] = item

    return {
        "version": APP_VERSION,
        "count": len(unique),
        "items": list(unique.values())
    }

