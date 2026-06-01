import os
import json
import requests
from datetime import datetime
from bs4 import BeautifulSoup
from fastapi import FastAPI
import gspread
from google.oauth2.service_account import Credentials

app = FastAPI(title="AI Tender Agent Cargo Strict V6")

BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")
GOOGLE_SHEET_ID = os.getenv("GOOGLE_SHEET_ID")

SEARCH_WORDS = [
    "перевозка грузов",
    "услуги по перевозке грузов",
    "оказание услуг по перевозке грузов",
    "доставка грузов",
    "грузоперевозки",
    "транспортные услуги",
    "оказание транспортных услуг",
    "транспортно-экспедиционные услуги",
    "экспедиторские услуги",
    "логистические услуги",
    "cargo transportation",
    "freight forwarding",
    "logistics services",
    "yuk tashish",
    "transport xizmati",
]

REQUIRED_PHRASES = [
    "перевозка грузов",
    "перевозке грузов",
    "перевозку грузов",
    "перевозки грузов",
    "услуги по перевозке грузов",
    "оказание услуг по перевозке грузов",
    "оказание услуги по перевозке грузов",
    "доставка грузов",
    "доставке грузов",
    "доставку грузов",
    "грузоперевоз",
    "грузовые перевозки",
    "транспортные услуги",
    "оказание транспортных услуг",
    "услуги автотранспорта",
    "автотранспортные услуги",
    "услуги грузового транспорта",
    "грузовой транспорт",
    "транспортно-экспедиционные услуги",
    "экспедиторские услуги",
    "логистические услуги",
    "контейнерные перевозки",
    "международные перевозки грузов",
    "автомобильные перевозки грузов",
    "cargo transportation",
    "freight forwarding",
    "transportation of goods",
    "delivery of goods",
    "logistics services",
    "yuk tashish",
    "yuklarni tashish",
    "transport xizmati",
    "transport xizmatlari",
]

BAD_URL_PARTS = [
    "register", "login", "logout", "signin", "signup",
    "cabinet", "profile", "account", "user", "my",
    "add.html", "/add", "create", "invited", "invitation",
    "english", "/en/", "news", "blog", "faq", "help",
    "contact", "about", "rules", "terms", "privacy",
    "advertising", "banner", "calendar", "archive",
    "feedback", "javascript:", "mailto:", "tel:",
]

BAD_TITLE_WORDS = [
    "регистрация", "зарегистрироваться", "войти", "выход",
    "стать заказчиком", "стать поставщиком",
    "english", "русский", "ўзбекча",
    "приглашение", "мои заявки", "моих заявок",
    "вопрос", "ответ", "помощь", "контакты",
    "написать нам письмо", "о сайте", "правила",
    "дата публикации", "личный кабинет", "кабинет", "профиль",
]

BLOCKED_NON_CARGO = [
    "арматур", "бетон", "цемент", "лаборатор", "оборудован",
    "мебел", "пленк", "стретч", "консультац", "технадзор",
    "техническому надзору", "модернизац", "строительств",
    "ремонт", "канцеляр", "компьютер", "принтер",
    "медицин", "питание", "продукт", "одежд", "обув",
    "уголь", "газ", "топливо", "дизель", "электро",
    "юридическ", "аудит", "страхован", "охрана",
]


def clean_text(text):
    return " ".join((text or "").replace("\n", " ").replace("\t", " ").split())


def contains_required_phrase(text):
    text = clean_text(text).lower()
    return any(phrase in text for phrase in REQUIRED_PHRASES)


def contains_blocked_non_cargo(text):
    text = clean_text(text).lower()
    return any(word in text for word in BLOCKED_NON_CARGO)


def looks_like_bad_url(url):
    url = (url or "").lower()
    return any(part in url for part in BAD_URL_PARTS)


def looks_like_bad_title(title):
    title = clean_text(title).lower()

    if len(title) < 12:
        return True

    if len(title.split()) < 2:
        return True

    if title.isdigit():
        return True

    if any(word in title for word in BAD_TITLE_WORDS):
        return True

    return False


def fetch_lot_page_title(url):
    headers = {"User-Agent": "Mozilla/5.0 (compatible; AI-Tender-Agent-Cargo-V6/6.0)"}

    try:
        response = requests.get(url, headers=headers, timeout=5)
        response.raise_for_status()

        soup = BeautifulSoup(response.text, "html.parser")

        candidates = []

        if soup.title:
            candidates.append(clean_text(soup.title.get_text(" ", strip=True)))

        for selector in ["h1", "h2", ".title", ".lot-title", ".tender-title"]:
            for item in soup.select(selector):
                text = clean_text(item.get_text(" ", strip=True))
                if text:
                    candidates.append(text)

        candidates = [x for x in candidates if x and len(x) >= 10]

        if not candidates:
            return ""

        return max(candidates, key=len)

    except Exception as e:
        print("LOT PAGE ERROR:", url, e)
        return ""


def is_real_cargo_tender(title, url, page_title=""):
    title = clean_text(title)
    page_title = clean_text(page_title)

    if not title or not url:
        return False

    if looks_like_bad_url(url):
        return False

    if looks_like_bad_title(title) and not page_title:
        return False

    combined = f"{title} {page_title}".lower()

    if contains_blocked_non_cargo(combined):
        return False

    if not contains_required_phrase(combined):
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
            timeout=15,
        )
        return response.status_code == 200
    except Exception as e:
        print("TELEGRAM ERROR:", e)
        return False


def collect_links(base_url, pages_to_scan, site_name):
    headers = {"User-Agent": "Mozilla/5.0 (compatible; AI-Tender-Agent-Cargo-V6/6.0)"}

    tenders = []
    seen_urls = set()
    total_links = 0
    checked_lot_pages = 0

    for page_url in pages_to_scan:
        try:
            response = requests.get(page_url, headers=headers, timeout=5)
            response.raise_for_status()
            soup = BeautifulSoup(response.text, "html.parser")

            for link in soup.find_all("a"):
                total_links += 1

                raw_title = clean_text(link.get_text(" ", strip=True))
                href = link.get("href")

                if not raw_title or not href:
                    continue

                full_url = requests.compat.urljoin(base_url, href)

                if full_url in seen_urls:
                    continue

                if looks_like_bad_url(full_url):
                    continue

                page_title = ""

                # Сначала быстрая проверка по названию ссылки
                if contains_required_phrase(raw_title) and not contains_blocked_non_cargo(raw_title):
                    final_title = raw_title
                else:
                    # Если ссылка похожа на лот, открываем саму страницу и читаем заголовок
                    if any(x in full_url.lower() for x in ["tender", "lot", "lots", "xarid", "etender"]):
                        checked_lot_pages += 1
                        page_title = fetch_lot_page_title(full_url)
                        final_title = page_title or raw_title
                    else:
                        continue

                if not is_real_cargo_tender(raw_title, full_url, page_title):
                    continue

                seen_urls.add(full_url)

                tenders.append({
                    "site": site_name,
                    "title": final_title,
                    "url": full_url,
                })

        except Exception as e:
            print(f"{site_name.upper()} ERROR:", e)

    print(
        f"{site_name}: total_links={total_links}, "
        f"checked_lot_pages={checked_lot_pages}, cargo_tenders={len(tenders)}"
    )

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

    return collect_links(base_url, pages_to_scan, "Tenderweek")


def parse_xt_xarid():
    base_url = "https://xt-xarid.uz/"
    pages_to_scan = [base_url]

    for word in SEARCH_WORDS:
        pages_to_scan.extend([
            f"{base_url}?search={word}",
            f"{base_url}?q={word}",
            f"{base_url}?keyword={word}",
        ])

    return collect_links(base_url, pages_to_scan, "XT-Xarid")


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

        all_tenders.extend(collect_links(base_url, pages_to_scan, "UZEX"))

    return all_tenders


def tender_exists(url):
    try:
        sheet = get_sheet()
        urls_col_c = sheet.col_values(3)
        urls_col_d = sheet.col_values(4)
        return url in urls_col_c or url in urls_col_d
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
            "AI Agent",
            url,
            site,
            "Новый",
            "Высокий",
            "Лот связан с перевозкой грузов / транспортно-экспедиционными услугами",
            title,
        ]

        sheet.append_row(row)
        return True

    except Exception as e:
        print("GOOGLE SHEETS ERROR:", e)
        return False


@app.get("/")
def home():
    return {"status": "AI Tender Agent Cargo Strict V6 is running"}


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
        sheet = get_sheet()
        sheet.row_values(1)
        result["google_sheets"] = True
    except Exception as e:
        result["errors"].append("Google Sheets error: " + str(e))

    if result["errors"]:
        result["status"] = "warning"

    return result


@app.get("/test_filter")
def test_filter():
    return {
        "Услуга по перевозке грузов": is_real_cargo_tender(
            "Услуга по перевозке грузов",
            "https://etender.uzex.uz/lot/123",
        ),
        "Оказание транспортных услуг": is_real_cargo_tender(
            "Оказание транспортных услуг",
            "https://xarid.uzex.uz/tender/456",
        ),
        "Транспортно-экспедиционные услуги": is_real_cargo_tender(
            "Транспортно-экспедиционные услуги",
            "https://etender.uzex.uz/lot/999",
        ),
        "Лабораторное оборудование": is_real_cargo_tender(
            "Лабораторное оборудование",
            "https://www.tenderweek.com/tender-35921",
        ),
        "Консультационные услуги по техническому надзору": is_real_cargo_tender(
            "Оказание консультационных услуг по техническому надзору",
            "https://www.tenderweek.com/tender-35920",
        ),
        "Закупка арматуры для АЭС": is_real_cargo_tender(
            "Закупка арматуры для АЭС",
            "https://www.tenderweek.com/tender-35911",
        ),
        "Поставка стретч-худ пленки": is_real_cargo_tender(
            "Поставка стретч-худ пленки",
            "https://www.tenderweek.com/tender-35910",
        ),
        "Написать нам письмо": is_real_cargo_tender(
            "Написать нам письмо",
            "https://www.tenderweek.com/feedback",
        ),
    }


@app.get("/scan")
def scan():
    found_total = 0
    new_total = 0
    duplicate_total = 0
    all_tenders = []
    seen_urls = set()

    message = "📊 AI Tender Agent Cargo Strict V6 Scan завершён\n\n"

    sources = [
        ("Tenderweek", parse_tenderweek),
        ("XT-Xarid", parse_xt_xarid),
        ("UZEX", parse_uzex),
    ]

    for source_name, parser in sources:
        try:
            result = parser()
            all_tenders.extend(result)
            message += f"{source_name}: лотов по перевозке грузов {len(result)}\n"
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

        if not is_real_cargo_tender(title, url):
            continue

        found_total += 1
        saved = save_to_sheet(tender["site"], title, url)

        if saved:
            new_total += 1
            text = (
                f"🚚 Новый лот по перевозке грузов\n\n"
                f"📌 Источник: {tender['site']}\n\n"
                f"📋 {title}\n\n"
                f"🔗 {url}"
            )
            send_telegram(text)
        else:
            duplicate_total += 1

    message += (
        f"Всего лотов по перевозке грузов: {found_total}\n"
        f"Новых сохранено: {new_total}\n"
        f"Дубликатов пропущено: {duplicate_total}"
    )

    send_telegram(message)

    return {
        "status": "success",
        "version": "cargo_strict_v6",
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
