import os
import json
import requests
from datetime import datetime
from bs4 import BeautifulSoup
from fastapi import FastAPI
import gspread
from google.oauth2.service_account import Credentials

app = FastAPI(title="AI Tender Agent V2")

BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")
GOOGLE_SHEET_ID = os.getenv("GOOGLE_SHEET_ID")

KEYWORDS = [
    "услуга по перевозке грузов", "услуги по перевозке грузов",
    "оказание услуг по перевозке грузов", "перевозка грузов",
    "перевозка товара", "перевозка товаров", "доставка грузов",
    "доставка товара", "доставка товаров", "грузоперевоз",
    "грузовые перевозки", "грузовой транспорт", "логистика",
    "логистические услуги", "транспортные услуги",
    "оказание транспортных услуг", "автотранспортные услуги",
    "автомобильные перевозки", "международные перевозки",
    "внутренние перевозки", "междугородние перевозки",
    "железнодорожные перевозки", "жд перевозки", "ж/д перевозки",
    "контейнерные перевозки", "контейнер", "мультимодальные перевозки",
    "экспедирование", "экспедиторские услуги",
    "транспортно-экспедиционные услуги", "транспортная экспедиция",
    "складские услуги", "хранение груза", "погрузка", "разгрузка",
    "погрузочно-разгрузочные работы", "спецтехника",
    "аренда спецтехники", "фура", "тягач", "полуприцеп",
    "рефрижератор", "самосвал", "контейнеровоз",
    "таможенное оформление", "таможенный брокер", "таможенные услуги",
    "freight", "freight forwarding", "cargo", "cargo transportation",
    "transportation", "transport services", "logistics",
    "logistics services", "delivery", "shipping", "forwarding",
    "warehouse", "customs clearance",
    "yuk tashish", "yuklarni tashish", "transport xizmati",
    "transport xizmatlari", "logistika", "yetkazib berish",
    "юк ташиш", "юкларни ташиш", "транспорт хизмати",
    "транспорт хизматлари", "логистика", "етказиб бериш",
]

SEARCH_WORDS = [
    "перевозка грузов", "транспортные услуги", "логистика",
    "экспедирование", "доставка грузов", "контейнерные перевозки",
    "таможенное оформление", "погрузочно-разгрузочные работы",
    "cargo transportation", "logistics services", "yuk tashish",
    "transport xizmati", "logistika",
]

BAD_URL_PARTS = [
    "register", "login", "logout", "signin", "signup",
    "cabinet", "profile", "account", "user", "my",
    "add.html", "/add", "create", "new",
    "invited", "invitation",
    "english", "/en/", "/ru/", "/uz/",
    "news", "blog", "faq", "help", "contact", "about",
    "rules", "terms", "privacy", "advertising",
    "banner", "calendar", "archive",
    "javascript:", "mailto:", "tel:",
]

BAD_TITLE_WORDS = [
    "регистрация", "зарегистрироваться", "войти", "выход",
    "стать заказчиком", "стать поставщиком",
    "english", "русский", "ўзбекча",
    "приглашение", "мои заявки", "моих заявок",
    "вопрос", "ответ", "помощь", "контакты",
    "о сайте", "правила", "дата публикации",
    "личный кабинет", "кабинет", "профиль",
]

TENDER_URL_HINTS = [
    "tender", "lot", "lots", "auction", "procurement",
    "purchase", "zakup", "xarid", "etender", "tender-",
]


def clean_text(text):
    return " ".join((text or "").replace("\n", " ").replace("\t", " ").split())


def is_logistics_tender(text):
    text = (text or "").lower()
    return any(word.lower() in text for word in KEYWORDS)


def looks_like_bad_url(url):
    url = (url or "").lower()
    return any(part in url for part in BAD_URL_PARTS)


def looks_like_bad_title(title):
    title = (title or "").lower().strip()

    if len(title) < 12:
        return True

    if any(word in title for word in BAD_TITLE_WORDS):
        return True

    if title.isdigit():
        return True

    if len(title.split()) < 2:
        return True

    return False


def looks_like_tender_url(url):
    url = (url or "").lower()
    return any(hint in url for hint in TENDER_URL_HINTS)


def is_real_tender_candidate(title, url):
    title = clean_text(title)
    combined = f"{title} {url}".lower()

    if not title or not url:
        return False

    if looks_like_bad_url(url):
        return False

    if looks_like_bad_title(title):
        return False

    if not is_logistics_tender(combined):
        return False

    if not looks_like_tender_url(url) and len(title) < 25:
        return False

    return True


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


def collect_links(base_url, pages_to_scan, site_name, min_title_len=12):
    headers = {"User-Agent": "Mozilla/5.0 (compatible; AI-Tender-Agent-V2/2.0)"}
    tenders = []
    seen_urls = set()

    for page_url in pages_to_scan:
        try:
            r = requests.get(page_url, headers=headers, timeout=30)
            r.raise_for_status()
            soup = BeautifulSoup(r.text, "html.parser")

            for link in soup.find_all("a"):
                title = clean_text(link.get_text(" ", strip=True))
                href = link.get("href")

                if not title or not href:
                    continue

                if len(title) < min_title_len:
                    continue

                full_url = requests.compat.urljoin(base_url, href)

                if full_url in seen_urls:
                    continue

                if not is_real_tender_candidate(title, full_url):
                    continue

                seen_urls.add(full_url)
                tenders.append({
                    "site": site_name,
                    "title": title,
                    "url": full_url,
                })

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

    return collect_links(base_url, pages_to_scan, "Tenderweek", min_title_len=12)


def parse_xt_xarid():
    base_url = "https://xt-xarid.uz/"
    pages_to_scan = [base_url]

    for word in SEARCH_WORDS:
        pages_to_scan.extend([
            f"{base_url}?search={word}",
            f"{base_url}?q={word}",
            f"{base_url}?keyword={word}",
        ])

    return collect_links(base_url, pages_to_scan, "XT-Xarid", min_title_len=12)


def parse_uzex():
    base_urls = [
        "https://etender.uzex.uz/lots/1/0",
        "https://etender.uzex.uz/",
        "https://xarid.uzex.uz/",
    ]

    all_tenders = []

    for base_url in base_urls:
        pages_to_scan = [base_url]

        for word in SEARCH_WORDS:
            pages_to_scan.extend([
                f"{base_url}?search={word}",
                f"{base_url}?q={word}",
                f"{base_url}?keyword={word}",
            ])

        all_tenders.extend(
            collect_links(base_url, pages_to_scan, "UZEX", min_title_len=12)
        )

    return all_tenders


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
    return {"status": "AI Tender Agent V2 is ready"}


@app.head("/")
def head_home():
    return {}


@app.get("/health")
def health():
    result = {
        "status": "ok",
        "telegram": False,
        "google_sheets": False,
        "errors": [],
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


@app.get("/test_filter")
def test_filter():
    tests = {
        "Услуга по перевозке грузов": is_real_tender_candidate(
            "Услуга по перевозке грузов",
            "https://etender.uzex.uz/lot/123",
        ),
        "Оказание транспортных услуг": is_real_tender_candidate(
            "Оказание транспортных услуг",
            "https://xarid.uzex.uz/tender/456",
        ),
        "Регистрация": is_real_tender_candidate(
            "Регистрация",
            "https://www.tenderweek.com/register",
        ),
        "Стать заказчиком": is_real_tender_candidate(
            "Стать заказчиком",
            "https://www.tenderweek.com/add.html",
        ),
        "English": is_real_tender_candidate(
            "English",
            "https://www.tenderweek.com/en/",
        ),
        "Закупка бетона для АЭС": is_real_tender_candidate(
            "Закупка бетона для АЭС",
            "https://etender.uzex.uz/lot/789",
        ),
        "Транспортно-экспедиционные услуги": is_real_tender_candidate(
            "Транспортно-экспедиционные услуги",
            "https://etender.uzex.uz/lot/999",
        ),
    }

    return tests


@app.get("/scan")
def scan():
    found_total = 0
    new_total = 0
    duplicate_total = 0
    all_tenders = []
    seen_urls = set()

    message = "📊 AI Tender Agent V2 Scan завершён\n\n"

    sources = [
        ("Tenderweek", parse_tenderweek),
        ("XT-Xarid", parse_xt_xarid),
        ("UZEX", parse_uzex),
    ]

    for source_name, parser in sources:
        try:
            result = parser()
            all_tenders.extend(result)
            message += f"{source_name}: найдено после фильтра {len(result)}\n"
        except Exception as e:
            message += f"{source_name}: ERROR\n"
            print(f"{source_name} ERROR:", e)

    message += "\n"

    for tender in all_tenders:
        url = tender.get("url")
        title = tender.get("title")

        if not url or url in seen_urls:
            continue

        seen_urls.add(url)

        if not is_real_tender_candidate(title, url):
            continue

        found_total += 1
        saved = save_to_sheet(tender["site"], title, url)

        if saved:
            new_total += 1
            text = (
                f"🆕 Новый логистический тендер\n\n"
                f"📌 Источник: {tender['site']}\n\n"
                f"📋 {title}\n\n"
                f"🔗 {url}"
            )
            send_telegram(text)
        else:
            duplicate_total += 1

    message += (
        f"Всего реальных логистических тендеров: {found_total}\n"
        f"Новых сохранено: {new_total}\n"
        f"Дубликатов пропущено: {duplicate_total}"
    )

    send_telegram(message)

    return {
        "status": "success",
        "version": "v2",
        "found_total": found_total,
        "new_total": new_total,
        "duplicates": duplicate_total,
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=int(os.getenv("PORT", 10000)),
    )
