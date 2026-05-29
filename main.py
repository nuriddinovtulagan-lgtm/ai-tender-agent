import os
import json
import requests
from datetime import datetime
from bs4 import BeautifulSoup
from fastapi import FastAPI
import gspread
from google.oauth2.service_account import Credentials

app = FastAPI(title="AI Tender Agent")

BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")
GOOGLE_SHEET_ID = os.getenv("GOOGLE_SHEET_ID")
KEYWORDS = [
    "услуга по перевозке грузов",
    "услуги по перевозке грузов",
    "оказание услуг по перевозке грузов",
    "перевозка грузов",
    "доставка грузов",
    "грузоперевоз",
    "груз",
    "логистика",
    "логист",
    "транспорт",
    "транспортные услуги",
    "оказание транспортных услуг",
    "автотранспорт",
    "автомобильные перевозки",
    "международные перевозки",
    "экспедирование",
    "экспедиторские услуги",
    "транспортно-экспедиционные услуги",
    "доставка",
    "контейнер",
    "склад",
    "спецтехника",
    "фура",
    "погрузка",
    "разгрузка"
]
def is_logistics_tender(title):
    title = title.lower()

    return any(word in title for word in KEYWORDS)
def get_sheet():
    raw_json = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON")

    if not raw_json:
        raise Exception("GOOGLE_SERVICE_ACCOUNT_JSON is empty")

    info = json.loads(raw_json)

    creds = Credentials.from_service_account_info(
        info,
        scopes=[
            "https://www.googleapis.com/auth/spreadsheets",
            "https://www.googleapis.com/auth/drive"
        ]
    )

    client = gspread.authorize(creds)
    sheet = client.open_by_key(GOOGLE_SHEET_ID).worksheet("Тендеры")
    return sheet


def send_telegram(text):
    if not BOT_TOKEN or not CHAT_ID:
        print("Telegram variables missing")
        return

    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"

    requests.post(
        url,
        json={
            "chat_id": CHAT_ID,
            "text": text
        },
        timeout=20
    )


def parse_tenderweek():
    base_url = "https://www.tenderweek.com/"
    headers = {"User-Agent": "Mozilla/5.0"}

    search_words = [
        "перевозка",
        "перевозка грузов",
        "транспорт",
        "транспортные услуги",
        "доставка",
        "логистика",
        "экспедирование",
        "склад",
        "спецтехника"
    ]

    tenders = []
    seen_urls = set()

    pages_to_scan = [base_url]

    for word in search_words:
        pages_to_scan.append(f"{base_url}?search={word}")
        pages_to_scan.append(f"{base_url}?q={word}")

    for url in pages_to_scan:
        try:
            r = requests.get(url, headers=headers, timeout=30)
            soup = BeautifulSoup(r.text, "html.parser")

            for link in soup.find_all("a"):
                title = link.get_text(strip=True)
                href = link.get("href")

                if not title or len(title) < 10:
                    continue

                if not href:
                    continue

                full_url = requests.compat.urljoin(base_url, href)

                if "tender" not in full_url.lower():
                    continue

                if full_url in seen_urls:
                    continue

                combined_text = f"{title} {full_url}"

                if not is_logistics_tender(combined_text):
                    continue

                seen_urls.add(full_url)

                tenders.append({
                    "site": "Tenderweek",
                    "title": title,
                    "url": full_url
                })

        except Exception as e:
            print("TENDERWEEK SEARCH ERROR:", e)

    return tenders[:30]

def parse_uzex():
    url = "https://etender.uzex.uz/lots/1/0"
    headers = {"User-Agent": "Mozilla/5.0"}

    r = requests.get(url, headers=headers, timeout=30)
    soup = BeautifulSoup(r.text, "html.parser")

    tenders = []

    for card in soup.find_all("div"):
        text = card.get_text(strip=True)

        if len(text) > 40 and ("UZS" in text or "лот" in text.lower()):
            tenders.append({
                "site": "UZEX",
                "title": text[:300],
                "url": url
            })

    return tenders[:10]


def tender_exists(url):
    try:
        sheet = get_sheet()
        urls = sheet.col_values(4)
        return url in urls
    except Exception as e:
        print("CHECK ERROR:", e)
        return False


def save_to_sheet(site, title, url):
    try:
        if tender_exists(url):
            return False

        sheet = get_sheet()

        row = [
            datetime.now().strftime("%d.%m.%Y %H:%M"),
            site,
            title,
            url,
            "Новый"
        ]

        sheet.append_row(row)
        return True

    except Exception as e:
        print("GOOGLE SHEETS ERROR:", e)
        return False


@app.get("/")
def home():
    return {
        "status": "AI Tender Agent is running"
    }


@app.head("/")
def head_home():
    return {}


@app.get("/health")
def health():
    result = {
        "status": "ok",
        "telegram": False,
        "google_sheets": False,
        "errors": []
    }

    try:
        tg_url = f"https://api.telegram.org/bot{BOT_TOKEN}/getMe"
        tg_response = requests.get(tg_url, timeout=10)
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
    found_total = 0
    new_total = 0
    duplicate_total = 0

    message = "📊 AI Auto Scan завершён\n\n"
    all_tenders = []

    try:
        tw = parse_tenderweek()
        all_tenders.extend(tw)
        message += f"Tenderweek найдено: {len(tw)}\n"
    except Exception as e:
        message += "Tenderweek ERROR\n"
        print(e)

    try:
        xt = parse_xt_xarid()
        all_tenders.extend(xt)
        message += f"XT-Xarid найдено: {len(xt)}\n"
    except Exception as e:
        message += "XT-Xarid ERROR\n"
        print(e)

    try:
        uzex = parse_uzex()
        all_tenders.extend(uzex)
        message += f"UZEX найдено: {len(uzex)}\n"
    except Exception as e:
        message += "UZEX ERROR\n"
        print(e)

    message += "\n"

    for tender in all_tenders[:20]:
        if not is_logistics_tender(tender["title"]):
            continue

        found_total += 1

        saved = save_to_sheet(
            tender["site"],
            tender["title"],
            tender["url"]
        )

        if saved:
            new_total += 1

            text = (
                f"🆕 Новый тендер\n\n"
                f"📌 {tender['site']}\n\n"
                f"{tender['title']}\n\n"
                f"{tender['url']}"
            )

            send_telegram(text)
        else:
            duplicate_total += 1

    message += (
        f"Всего найдено по логистике: {found_total}\n"
        f"Новых сохранено: {new_total}\n"
        f"Дубликатов пропущено: {duplicate_total}"
    )

    send_telegram(message)

    return {
        "status": "success",
        "found_total": found_total,
        "new_total": new_total,
        "duplicates": duplicate_total
    }
@app.get("/test_filter")
def test_filter():

    tests = [
        "Услуга по перевозке грузов",
        "Оказание транспортных услуг",
        "Закупка бетона для АЭС",
        "Поставка мебели",
        "Транспортно-экспедиционные услуги"
    ]

    result = {}

    for t in tests:
        result[t] = is_logistics_tender(t)

    return result
