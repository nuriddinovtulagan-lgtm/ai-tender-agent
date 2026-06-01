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


def is_logistics_tender(title):
    title = (title or "").lower()
    return any(word.lower() in title for word in KEYWORDS)


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
        print("Telegram variables missing")
        return False

    try:
        response = requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            json={"chat_id": CHAT_ID, "text": text[:3900]},
            timeout=20,
        )
        return response.status_code == 200
    except Exception as e:
        print("TELEGRAM ERROR:", e)
        return False


def collect_links(base_url, pages_to_scan, site_name, min_title_len=6, require_tender_in_url=False):
    headers = {"User-Agent": "Mozilla/5.0 (compatible; AI-Tender-Agent/1.0)"}
    tenders = []
    seen_urls = set()

    for url in pages_to_scan:
        try:
            r = requests.get(url, headers=headers, timeout=30)
            r.raise_for_status()
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
                tenders.append({"site": site_name, "title": title, "url": full_url})

        except Exception as e:
            print(f"{site_name.upper()} ERROR:", e)

    return tenders


def parse_tenderweek():
    base_url = "https://www.tenderweek.com/"
    pages_to_scan = [base_url]

    for word in SEARCH_WORDS:
        pages_to_scan.extend([
            f"{base_url}?search={word}",
            f"{base_url}?q={word}",
            f"{base_url}?keyword={word}",
        ])

    return collect_links(base_url, pages_to_scan, "Tenderweek", min_title_len=10, require_tender_in_url=True)


def parse_xt_xarid():
    base_url = "https://xt-xarid.uz/"
    pages_to_scan = [base_url]

    for word in SEARCH_WORDS:
        pages_to_scan.extend([
            f"{base_url}?search={word}",
            f"{base_url}?q={word}",
            f"{base_url}?keyword={word}",
        ])

    return collect_links(base_url, pages_to_scan, "XT-Xarid", min_title_len=6)


def parse_uzex():
    base_urls = [
        "https://etender.uzex.uz/lots/1/0",
        "https://etender.uzex.uz/",
        "https://xarid.uzex.uz/",
    ]
    pages_to_scan = []

    for base_url in base_urls:
        pages_to_scan.append(base_url)
        for word in SEARCH_WORDS:
            pages_to_scan.extend([
                f"{base_url}?search={word}",
                f"{base_url}?q={word}",
                f"{base_url}?keyword={word}",
            ])

    return collect_links("https://etender.uzex.uz/", pages_to_scan, "UZEX", min_title_len=6)


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
            "Новый",
        ]
        sheet.append_row(row)
        return True

    except Exception as e:
        print("GOOGLE SHEETS ERROR:", e)
        return False


@app.get("/")
def home():
    return {"status": "AI Tender Agent is running"}


@app.head("/")
def head_home():
    return {}


@app.get("/health")
def health():
    result = {"status": "ok", "telegram": False, "google_sheets": False, "errors": []}

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


@app.get("/scan")
def scan():
    found_total = 0
    new_total = 0
    duplicate_total = 0
    all_tenders = []
    seen_urls = set()

    message = "📊 AI Auto Scan завершён\n\n"

    sources = [
        ("Tenderweek", parse_tenderweek),
        ("XT-Xarid", parse_xt_xarid),
        ("UZEX", parse_uzex),
    ]

    for source_name, parser in sources:
        try:
            result = parser()
            all_tenders.extend(result)
            message += f"{source_name} найдено: {len(result)}\n"
        except Exception as e:
            message += f"{source_name} ERROR\n"
            print(f"{source_name} ERROR:", e)

    message += "\n"

    for tender in all_tenders:
        url = tender.get("url")
        title = tender.get("title")

        if not url or url in seen_urls:
            continue
        seen_urls.add(url)

        if not is_logistics_tender(f"{title} {url}"):
            continue

        found_total += 1
        saved = save_to_sheet(tender["site"], title, url)

        if saved:
            new_total += 1
            text = (
                f"🆕 Новый логистический тендер\n\n"
                f"📌 {tender['site']}\n\n"
                f"{title}\n\n"
                f"{url}"
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
        "duplicates": duplicate_total,
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", 10000)))
