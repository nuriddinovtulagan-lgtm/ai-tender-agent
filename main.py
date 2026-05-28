import os
import json
import requests
from datetime import datetime
from bs4 import BeautifulSoup
from fastapi import FastAPI, Request
import gspread
from google.oauth2.service_account import Credentials

app = FastAPI(title="AI Tender Scraper")

BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")
GOOGLE_SHEET_ID = os.getenv("GOOGLE_SHEET_ID")
GOOGLE_SERVICE_ACCOUNT_JSON = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON")

LOGISTICS_KEYWORDS = [
    "логистика", "логистик", "груз", "грузоперевоз", "перевозка",
    "перевоз", "транспорт", "автотранспорт", "доставка", "экспед",
    "экспедитор", "контейнер", "склад", "тамож", "cargo", "freight",
    "transport", "truck", "warehouse", "container", "delivery", "shipping"
]

BLOCKED_KEYWORDS = [
    "мебель", "канц", "бумага", "питание", "строительство", "ремонт",
    "компьютер", "сервер", "телефон", "молоко", "хлеб", "мясо"
]

SCAN_URLS = [
    "https://www.tenderweek.com/",
    "https://xt-xarid.uz/",
    "https://xt-xarid.uz/ru",
    "https://xarid.uzex.uz/home",
]


def send_telegram(text: str) -> None:
    if not BOT_TOKEN or not CHAT_ID:
        print("Telegram env vars missing")
        return

    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    requests.post(url, json={"chat_id": CHAT_ID, "text": text}, timeout=20)


def get_sheet():
    info = json.loads(GOOGLE_SERVICE_ACCOUNT_JSON)
    scopes = ["https://www.googleapis.com/auth/spreadsheets"]
    creds = Credentials.from_service_account_info(info, scopes=scopes)
    client = gspread.authorize(creds)
    return client.open_by_key(GOOGLE_SHEET_ID).worksheet("Тендеры")


def normalize_text(text: str) -> str:
    return " ".join((text or "").replace("\n", " ").split())


def is_logistics(text: str) -> bool:
    clean = text.lower()

    if any(word in clean for word in BLOCKED_KEYWORDS):
        return False

    return any(word in clean for word in LOGISTICS_KEYWORDS)


def detect_source(url: str) -> str:
    low = url.lower()

    if "tenderweek" in low:
        return "Tenderweek"

    if "xarid.uzex" in low or "etender.uzex" in low:
        return "Xarid UZEX"

    if "xt-xarid" in low:
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

    if source == "Tenderweek":
        return "🔴 Высокий"

    if any(k in low for k in ["международ", "international", "cargo", "freight", "container"]):
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


def get_ai_analysis(source: str) -> str:
    if source == "Tenderweek":
        return "Проверить международные требования, дедлайн и условия участия."

    if source == "Xarid UZEX":
        return "Изучить ТЗ, требования госзакупки, сроки и документы."

    if source == "XT-Xarid":
        return "Проверить соответствие логистическим услугам и условия оплаты."

    return "Требуется анализ тендера."


def save_tender(url: str, title: str) -> bool:
    sheet = get_sheet()

    existing_links = sheet.col_values(3)
    if url in existing_links:
        return False

    source = detect_source(url)
    responsible = assign_responsible(source)
    combined_text = title + " " + url
    priority = get_priority(source, combined_text)
    chance = get_win_chance(source, priority)
    margin, risk, logistics = finance_analysis(source, priority)
    ai = get_ai_analysis(source)

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


def extract_links_from_page(page_url: str):
    headers = {
        "User-Agent": "Mozilla/5.0"
    }

    response = requests.get(page_url, timeout=30, headers=headers)
    response.raise_for_status()

    soup = BeautifulSoup(response.text, "html.parser")
    results = []

    for a in soup.find_all("a", href=True):
        title = normalize_text(a.get_text(" ", strip=True))
        href = a.get("href")

        if not href:
            continue

        full_url = requests.compat.urljoin(page_url, href)
        combined = title + " " + full_url

        if len(title) < 4:
            continue

        results.append({
            "title": title,
            "url": full_url,
            "combined": combined
        })

    return results


def scan_page(page_url: str) -> int:
    found = 0

    try:
        links = extract_links_from_page(page_url)

        for item in links[:300]:
            if is_logistics(item["combined"]):
                if save_tender(item["url"], item["title"]):
                    found += 1

    except Exception as e:
        send_telegram(f"❌ Ошибка сканирования:\n{page_url}\n\n{e}")

    return found


def run_all_scans():
    details = {}
    total = 0

    for url in SCAN_URLS:
        count = scan_page(url)
        details[url] = count
        total += count

    return total, details


@app.get("/")
def home():
    return {"status": "AI Tender Scraper is running"}


@app.get("/scan")
def scan():
    total, details = run_all_scans()

    msg = "📊 AI Auto Scan завершён\n\n"
    for url, count in details.items():
        msg += f"{url}: {count}\n"
    msg += f"\nВсего новых: {total}"

    send_telegram(msg)

    return {
        "total": total,
        "details": details
    }


@app.post("/webhook")
async def telegram_webhook(request: Request):
    data = await request.json()

    message = data.get("message", {})
    text = message.get("text", "")

    if not text:
        return {"ok": True}

    if text == "/scan":
        total, details = run_all_scans()
        send_telegram(f"📊 Ручной AI Scan завершён\nВсего новых: {total}")
        return {"ok": True}

    if "http" in text:
        if not is_logistics(text):
            send_telegram("❌ Ссылка не похожа на логистический тендер.")
            return {"ok": True}

        saved = save_tender(text, "Ссылка от сотрудника")
        if not saved:
            send_telegram("⚠️ Этот тендер уже есть в базе\n\n" + text)

    return {"ok": True}
