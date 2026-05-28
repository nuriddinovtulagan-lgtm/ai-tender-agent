import os
import json
import requests
from datetime import datetime
from bs4 import BeautifulSoup
from fastapi import FastAPI, Request
import gspread
from google.oauth2.service_account import Credentials

app = FastAPI(title="AI Tender Agent")

BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")
GOOGLE_SHEET_ID = os.getenv("GOOGLE_SHEET_ID")
GOOGLE_SERVICE_ACCOUNT_JSON = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON")

KEYWORDS = [
    "логист", "груз", "грузоперевоз", "перевоз", "транспорт",
    "доставка", "экспед", "контейнер", "склад", "тамож",
    "cargo", "freight", "transport", "delivery", "truck",
    "container", "warehouse", "shipping"
]

BAD_WORDS = [
    "мебель", "ремонт", "строительство", "канц", "бумага",
    "продукт", "компьютер", "телефон", "молоко", "хлеб"
]

SITES = [
    "https://etender.uzex.uz/lots/1/0",
    "https://etender.uzex.uz/",
    "https://xt-xarid.uz/",
    "https://xt-xarid.uz/ru",
    "https://www.tenderweek.com/",
    "https://atom.tenderweek.com/tender-35899"
]


def send_telegram(text):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    requests.post(url, json={"chat_id": CHAT_ID, "text": text}, timeout=20)


def get_sheet():
    info = json.loads(GOOGLE_SERVICE_ACCOUNT_JSON)
    creds = Credentials.from_service_account_info(
        info,
        scopes=["https://www.googleapis.com/auth/spreadsheets"]
    )
    client = gspread.authorize(creds)
    return client.open_by_key(GOOGLE_SHEET_ID).worksheet("Тендеры")


def clean(text):
    return " ".join((text or "").replace("\n", " ").split())


def is_logistics(text):
    t = text.lower()

    if any(bad in t for bad in BAD_WORDS):
        return False

    return any(key in t for key in KEYWORDS)


def detect_source(url):
    u = url.lower()

    if "tenderweek" in u:
        return "Tenderweek"

    if "xt-xarid" in u:
        return "XT-Xarid"

    if "xarid.uzex" in u or "etender.uzex" in u:
        return "Xarid UZEX"

    return "Другой источник"


def assign_responsible(source):
    if source == "Tenderweek":
        return "Nurik"
    if source == "Xarid UZEX":
        return "Otabek"
    if source == "XT-Xarid":
        return "101"
    return "Nuriddin"


def get_priority(source, text):
    t = text.lower()

    if source == "Tenderweek":
        return "🔴 Высокий"

    if any(x in t for x in ["international", "международ", "cargo", "freight", "container"]):
        return "🔴 Высокий"

    if source in ["Xarid UZEX", "XT-Xarid"]:
        return "🟡 Средний"

    return "🟢 Низкий"


def get_win_chance(source, priority):
    if source == "Tenderweek" and priority == "🔴 Высокий":
        return "🏆 Высокий"

    if source in ["Xarid UZEX", "XT-Xarid"]:
        return "⚖️ Средний"

    return "⚠️ Низкий"


def financial_analysis(source, priority):
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


def ai_analysis(source):
    if source == "Tenderweek":
        return "Проверить международные требования, дедлайн и условия участия."

    if source == "Xarid UZEX":
        return "Изучить ТЗ, требования госзакупки, сроки и документы."

    if source == "XT-Xarid":
        return "Проверить соответствие логистическим услугам и условия оплаты."

    return "Требуется анализ тендера."


def save_tender(url, title, sender="AUTO"):
    sheet = get_sheet()

    existing_links = sheet.col_values(3)

    if url in existing_links:
        return False

    source = detect_source(url)
    responsible = assign_responsible(source)
    priority = get_priority(source, title + " " + url)
    chance = get_win_chance(source, priority)
    margin, risk, logistics = financial_analysis(source, priority)
    analysis = ai_analysis(source)

    sheet.append_row([
        datetime.now().strftime("%d.%m.%Y %H:%M"),
        sender,
        url,
        source,
        "Новый",
        priority,
        analysis,
        title,
        "",
        responsible,
        "",
        chance,
        margin,
        risk,
        logistics
    ])

    send_telegram(
        "🆕 Новый логистический тендер\n\n"
        f"Название: {title}\n"
        f"Источник: {source}\n"
        f"Ссылка: {url}\n\n"
        f"👤 Ответственный: {responsible}\n"
        f"📌 Приоритет: {priority}\n"
        f"🏆 Шанс победы: {chance}\n"
        f"{margin}\n"
        f"{risk}\n"
        f"{logistics}"
    )

    return True


def scan_site(url):
    found = 0

    html = requests.get(
        url,
        headers={"User-Agent": "Mozilla/5.0"},
        timeout=30
    ).text

    soup = BeautifulSoup(html, "html.parser")

    page_text = clean(soup.get_text(" ", strip=True))

    if is_logistics(page_text):
        title = page_text[:120]
        if save_tender(url, title):
            found += 1

    for a in soup.find_all("a", href=True)[:500]:
        title = clean(a.get_text(" ", strip=True))
        href = a.get("href")
        full_url = requests.compat.urljoin(url, href)

        if len(title) < 4:
            continue

        combined = title + " " + full_url

        if is_logistics(combined):
            if save_tender(full_url, title):
                found += 1

    return found


def run_scan():
    total = 0
    details = {}

    for url in SITES:
        try:
            count = scan_site(url)
        except Exception as e:
            count = 0
            send_telegram(
                "❌ Ошибка сканирования\n\n"
                f"Источник: {url}\n"
                f"Ошибка: {e}"
            )

        details[url] = count
        total += count

    return total, details


@app.get("/")
def home():
    return {"status": "AI Tender Scraper is running"}


@app.get("/scan")
def scan():
    total, details = run_scan()

    message = "📊 AI Auto Scan завершён\n\n"

    for url, count in details.items():
        message += f"{url}: {count}\n"

    message += f"\nВсего новых: {total}"

    send_telegram(message)

    return {
        "total": total,
        "details": details
    }


@app.post("/webhook")
async def webhook(request: Request):
    data = await request.json()

    message = data.get("message", {})
    text = message.get("text", "")
    sender = message.get("from", {}).get("first_name", "Сотрудник")

    if not text:
        return {"ok": True}

    if text == "/scan":
        total, details = run_scan()
        send_telegram(f"📊 Ручной AI Scan завершён\nВсего новых: {total}")
        return {"ok": True}

    if "http" in text:
        if not is_logistics(text):
            send_telegram(
                "❌ Ссылка не похожа на логистический тендер.\n\n"
                f"{text}"
            )
            return {"ok": True}

        saved = save_tender(text, "Ссылка от сотрудника", sender)

        if not saved:
            send_telegram(
                "⚠️ Этот тендер уже есть в базе\n\n"
                f"{text}"
            )

    return {"ok": True}
@app.get("/")
def home():
    return {"status": "AI Tender Agent is running"}
    if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=10000)
