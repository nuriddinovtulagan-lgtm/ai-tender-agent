import os
import json
import requests
from datetime import datetime
from bs4 import BeautifulSoup
from fastapi import FastAPI
import gspread
from google.oauth2.service_account import Credentials

app = FastAPI(title="AI Tender Scraper")

BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")
GOOGLE_SHEET_ID = os.getenv("GOOGLE_SHEET_ID")
GOOGLE_SERVICE_ACCOUNT_JSON = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON")

LOGISTICS_KEYWORDS = [
    "логистика", "груз", "грузоперевоз", "перевозка", "транспорт",
    "доставка", "экспед", "контейнер", "склад", "тамож",
    "cargo", "freight", "transport", "truck", "warehouse", "container"
]

BLOCKED_KEYWORDS = [
    "мебель", "канц", "бумага", "продукт", "питание",
    "строительство", "ремонт", "компьютер", "сервер", "телефон"
]


def send_telegram(text: str) -> None:
    if not BOT_TOKEN or not CHAT_ID:
        print("Telegram env vars missing")
        return
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    requests.post(url, json={"chat_id": CHAT_ID, "text": text}, timeout=20)


def get_sheet():
    if not GOOGLE_SHEET_ID or not GOOGLE_SERVICE_ACCOUNT_JSON:
        print("Google Sheets env vars missing")
        return None
    info = json.loads(GOOGLE_SERVICE_ACCOUNT_JSON)
    scopes = ["https://www.googleapis.com/auth/spreadsheets"]
    creds = Credentials.from_service_account_info(info, scopes=scopes)
    client = gspread.authorize(creds)
    return client.open_by_key(GOOGLE_SHEET_ID).worksheet("Тендеры")


def is_logistics(text: str) -> bool:
    clean = text.lower()
    if any(word in clean for word in BLOCKED_KEYWORDS):
        return False
    return any(word in clean for word in LOGISTICS_KEYWORDS)


def detect_source(url: str) -> str:
    if "tenderweek" in url:
        return "Tenderweek"
    if "xarid.uzex" in url or "etender.uzex" in url:
        return "Xarid UZEX"
    if "xt-xarid" in url:
        return "XT-Xarid"
    return "Другой источник"


def assign_responsible(source: str) -> str:
    if source == "Tenderweek":
        return "Nurik"
    if source == "Xarid UZEX":
        return "Otabek"
    if source == "XT-Xarid":
        return "101"
    return "Nuriddin"


def get_priority(source: str, text: str) -> str:
    low = text.lower()
    if source == "Tenderweek" or any(k in low for k in ["logistic", "transport", "cargo", "freight"]):
        return "🔴 Высокий"
    if source in ["Xarid UZEX", "XT-Xarid"]:
        return "🟡 Средний"
    return "🟢 Низкий"


def get_win_chance(source: str, priority: str) -> str:
    if source == "Tenderweek" and priority == "🔴 Высокий":
        return "🏆 Высокий"
    if source in ["Xarid UZEX", "XT-Xarid"]:
        return "⚖️ Средний"
    return "⚠️ Низкий"


def finance_analysis(source: str, priority: str):
    margin = "💰 Средняя"
    risk = "⚠️ Средний"
    logistics = "🚚 Средняя"
    if source == "Tenderweek":
        margin = "💰 Высокая"
        logistics = "🚚 Высокая"
    if source == "Xarid UZEX":
        risk = "⚠️ Низкий"
    if priority == "🔴 Высокий":
        margin = "💰 Высокая"
    return margin, risk, logistics


def save_tender(url: str, title: str) -> bool:
    sheet = get_sheet()
    if sheet is None:
        return False

    existing = sheet.col_values(3)
    if url in existing:
        return False

    source = detect_source(url)
    responsible = assign_responsible(source)
    priority = get_priority(source, title + " " + url)
    chance = get_win_chance(source, priority)
    margin, risk, logistics = finance_analysis(source, priority)

    ai = {
        "Tenderweek": "Проверить международные требования и дедлайн.",
        "Xarid UZEX": "Изучить ТЗ и требования госзакупки.",
        "XT-Xarid": "Проверить соответствие логистическим услугам.",
    }.get(source, "Требуется анализ тендера.")

    sheet.append_row([
        datetime.now().strftime("%d.%m.%Y %H:%M"),
        "AUTO",
        url,
        source,
        "Новый",
        priority,
        ai,
        title,
        "",
        responsible,
        "",
        chance,
        margin,
        risk,
        logistics,
    ])

    send_telegram(
        "🆕 Новый логистический тендер\n\n"
        f"Название: {title}\n"
        f"Источник: {source}\n"
        f"Ссылка: {url}\n\n"
        f"👤 Ответственный: {responsible}\n"
        f"📌 Приоритет: {priority}\n"
        f"🏆 Шанс победы: {chance}\n"
        f"{margin}\n{risk}\n{logistics}"
    )
    return True


def scan_site(base_url: str, source_name: str) -> int:
    try:
        html = requests.get(base_url, timeout=30, headers={"User-Agent": "Mozilla/5.0"}).text
        soup = BeautifulSoup(html, "html.parser")
        links = soup.find_all("a", href=True)
        count = 0

        for a in links[:150]:
            title = " ".join(a.get_text(" ", strip=True).split())
            href = a["href"]
            if not title or len(title) < 5:
                continue
            full_url = href if href.startswith("http") else requests.compat.urljoin(base_url, href)
            combined = title + " " + full_url
            if is_logistics(combined):
                if save_tender(full_url, title):
                    count += 1
        return count
    except Exception as exc:
        send_telegram(f"❌ Ошибка scan {source_name}: {exc}")
        return 0


def scan_tenderweek() -> int:
    return scan_site("https://www.tenderweek.com/", "Tenderweek")


def scan_xt_xarid() -> int:
    return scan_site("https://xt-xarid.uz/", "XT-Xarid")


@app.get("/")
def home():
    return {"status": "AI Tender Scraper is running"}


@app.get("/scan")
def run_scan():
    tenderweek_count = scan_tenderweek()
    xt_count = scan_xt_xarid()
    total = tenderweek_count + xt_count
    send_telegram(
        "📊 AI Auto Scan завершён\n\n"
        f"Tenderweek новых: {tenderweek_count}\n"
        f"XT-Xarid новых: {xt_count}\n"
        f"Всего новых: {total}"
    )
    return {"tenderweek": tenderweek_count, "xt_xarid": xt_count, "total": total}
