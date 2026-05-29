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
GOOGLE_SERVICE_ACCOUNT_JSON = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON")

SITES = [
    "https://www.tenderweek.com/",
    "https://xt-xarid.uz/",
    "https://xt-xarid.uz/ru",
    "https://xarid.uzex.uz/home",
    "https://etender.uzex.uz/lots/1/0"
]

# =========================
# GOOGLE SHEETS
# =========================

def get_sheet():
    info = json.loads(GOOGLE_SERVICE_ACCOUNT_JSON)

    creds = Credentials.from_service_account_info(
        info,
        scopes=[
            "https://www.googleapis.com/auth/spreadsheets",
            "https://www.googleapis.com/auth/drive"
        ]
    )

    client = gspread.authorize(creds)

    sheet = client.open_by_key(GOOGLE_SHEET_ID).sheet1
    return sheet

# =========================
# TELEGRAM
# =========================

def send_telegram(text):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"

    requests.post(
        url,
        json={
            "chat_id": CHAT_ID,
            "text": text
        }
    )

# =========================
# PARSERS
# =========================

def parse_tenderweek():
    url = "https://www.tenderweek.com/"

    headers = {
        "User-Agent": "Mozilla/5.0"
    }

    r = requests.get(url, headers=headers, timeout=30)

    soup = BeautifulSoup(r.text, "html.parser")

    tenders = []

    links = soup.find_all("a")

    for link in links:
        text = link.get_text(strip=True)

        if len(text) > 15:
            tenders.append({
                "site": "Tenderweek",
                "title": text,
                "url": link.get("href")
            })

    return tenders[:10]

# =========================

def parse_xt_xarid():
    url = "https://xt-xarid.uz/"

    headers = {
        "User-Agent": "Mozilla/5.0"
    }

    r = requests.get(url, headers=headers, timeout=30)

    soup = BeautifulSoup(r.text, "html.parser")

    tenders = []

    cards = soup.find_all("div")

    for card in cards:
        text = card.get_text(strip=True)

        if "тендер" in text.lower() or "закуп" in text.lower():
            if len(text) > 20:
                tenders.append({
                    "site": "XT-Xarid",
                    "title": text[:300],
                    "url": url
                })

    return tenders[:10]

# =========================

def parse_uzex():
    url = "https://etender.uzex.uz/lots/1/0"

    headers = {
        "User-Agent": "Mozilla/5.0"
    }

    r = requests.get(url, headers=headers, timeout=30)

    soup = BeautifulSoup(r.text, "html.parser")

    tenders = []

    cards = soup.find_all("div")

    for card in cards:
        text = card.get_text(strip=True)

        if len(text) > 40:
            if "UZS" in text or "лот" in text.lower():
                tenders.append({
                    "site": "UZEX",
                    "title": text[:300],
                    "url": url
                })

    return tenders[:10]

# =========================
# SAVE TO GOOGLE SHEETS
# =========================

def save_to_sheet(site, title, url):
    try:
        sheet = get_sheet()

        row = [
            datetime.now().strftime("%d.%m.%Y %H:%M"),
            site,
            title,
            url,
            "Новый"
        ]

        sheet.append_row(row)

    except Exception as e:
        print("GOOGLE SHEETS ERROR:", e)

# =========================
# MAIN SCAN
# =========================

@app.get("/scan")
def scan():

    total = 0
    message = "📊 AI Auto Scan завершён\n\n"

    all_tenders = []

    try:
        tw = parse_tenderweek()
        all_tenders.extend(tw)
        message += f"Tenderweek: {len(tw)}\n"
    except Exception as e:
        message += f"Tenderweek ERROR\n"
        print(e)

    try:
        xt = parse_xt_xarid()
        all_tenders.extend(xt)
        message += f"XT-Xarid: {len(xt)}\n"
    except Exception as e:
        message += f"XT-Xarid ERROR\n"
        print(e)

    try:
        uzex = parse_uzex()
        all_tenders.extend(uzex)
        message += f"UZEX: {len(uzex)}\n"
    except Exception as e:
        message += f"UZEX ERROR\n"
        print(e)

    message += "\n"

    for tender in all_tenders[:20]:

        total += 1

        text = (
            f"📌 {tender['site']}\n\n"
            f"{tender['title']}\n\n"
            f"{tender['url']}"
        )

        send_telegram(text)

        save_to_sheet(
            tender["site"],
            tender["title"],
            tender["url"]
        )

    message += f"Всего найдено: {total}"

    send_telegram(message)

    return {
        "status": "success",
        "total": total
    }

# =========================
# ROOT
# =========================

@app.get("/")
def home():
    return {
        "status": "AI Tender Agent is running"
    }

@app.head("/")
def head_home():
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
    return {}

# =========================
# START
# =========================

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        app,
        host="0.0.0.0",
        port=10000
    )
