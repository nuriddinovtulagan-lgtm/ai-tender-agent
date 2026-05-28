import os, json, requests
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

KEYWORDS = ["логист", "груз", "перевоз", "транспорт", "доставка", "экспед", "контейнер", "cargo", "freight", "transport", "delivery", "truck"]
BAD = ["мебель", "ремонт", "строительство", "канц", "бумага", "продукт", "компьютер", "телефон"]

SITES = [
    "https://www.tenderweek.com/",
    "https://xt-xarid.uz/",
    "https://xt-xarid.uz/ru",
    "https://xarid.uzex.uz/home",
]

def send_telegram(text):
    requests.post(
        f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
        json={"chat_id": CHAT_ID, "text": text},
        timeout=20
    )

def sheet():
    info = json.loads(GOOGLE_SERVICE_ACCOUNT_JSON)
    creds = Credentials.from_service_account_info(
        info,
        scopes=["https://www.googleapis.com/auth/spreadsheets"]
    )
    return gspread.authorize(creds).open_by_key(GOOGLE_SHEET_ID).worksheet("Тендеры")

def clean(text):
    return " ".join((text or "").replace("\n", " ").split())

def is_logistics(text):
    t = text.lower()
    if any(x in t for x in BAD):
        return False
    return any(x in t for x in KEYWORDS)

def source(url):
    u = url.lower()
    if "tenderweek" in u: return "Tenderweek"
    if "xt-xarid" in u: return "XT-Xarid"
    if "xarid.uzex" in u or "etender.uzex" in u: return "Xarid UZEX"
    return "Другой источник"

def responsible(src):
    if src == "Tenderweek": return "Nurik"
    if src == "Xarid UZEX": return "Otabek"
    if src == "XT-Xarid": return "101"
    return "Nuriddin"

def priority(src, title):
    t = title.lower()
    if src == "Tenderweek" or any(x in t for x in ["cargo", "freight", "container", "международ"]):
        return "🔴 Высокий"
    if src in ["Xarid UZEX", "XT-Xarid"]:
        return "🟡 Средний"
    return "🟢 Низкий"

def save_tender(url, title):
    sh = sheet()
    if url in sh.col_values(3):
        return False

    src = source(url)
    resp = responsible(src)
    pr = priority(src, title + " " + url)
    chance = "⚖️ Средний" if src in ["Xarid UZEX", "XT-Xarid"] else "🏆 Высокий" if src == "Tenderweek" else "⚠️ Низкий"
    margin = "💰 Высокая" if pr == "🔴 Высокий" else "💰 Средняя"
    risk = "⚠️ Низкий" if src == "Xarid UZEX" else "⚠️ Средний"
    logistics = "🚚 Высокая" if src == "Tenderweek" else "🚚 Средняя"
    ai = "Изучить ТЗ, сроки, документы и условия оплаты."

    sh.append_row([
        datetime.now().strftime("%d.%m.%Y %H:%M"),
        "AUTO",
        url,
        src,
        "Новый",
        pr,
        ai,
        title,
        "",
        resp,
        "",
        chance,
        margin,
        risk,
        logistics
    ])

    send_telegram(
        "🆕 Новый логистический тендер\n\n"
        f"Название: {title}\n"
        f"Источник: {src}\n"
        f"Ссылка: {url}\n\n"
        f"👤 Ответственный: {resp}\n"
        f"📌 Приоритет: {pr}\n"
        f"🏆 Шанс победы: {chance}\n"
        f"{margin}\n{risk}\n{logistics}"
    )
    return True

def scan_site(url):
    found = 0
    html = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=30).text
    soup = BeautifulSoup(html, "html.parser")

    for a in soup.find_all("a", href=True)[:500]:
        title = clean(a.get_text(" ", strip=True))
        href = a["href"]
        full = requests.compat.urljoin(url, href)

        if len(title) < 4:
            continue

        combined = title + " " + full

        if is_logistics(combined):
            if save_tender(full, title):
                found += 1

    return found

@app.get("/")
def home():
    return {"status": "AI Tender Scraper is running"}

@app.get("/scan")
def scan():
    total = 0
    details = {}

    for url in SITES:
        try:
            count = scan_site(url)
        except Exception as e:
            count = 0
            send_telegram(f"❌ Ошибка сканирования\n{url}\n{e}")

        details[url] = count
        total += count

    msg = "📊 AI Auto Scan завершён\n\n"
    for url, count in details.items():
        msg += f"{url}: {count}\n"
    msg += f"\nВсего новых: {total}"

    send_telegram(msg)
    return {"total": total, "details": details}

@app.post("/webhook")
async def webhook(request: Request):
    data = await request.json()
    text = data.get("message", {}).get("text", "")

    if text == "/scan":
        return scan()

    if "http" in text:
        if not is_logistics(text):
            send_telegram("❌ Ссылка не похожа на логистический тендер.")
            return {"ok": True}

        if not save_tender(text, "Ссылка от сотрудника"):
            send_telegram("⚠️ Этот тендер уже есть в базе\n\n" + text)

    return {"ok": True}
