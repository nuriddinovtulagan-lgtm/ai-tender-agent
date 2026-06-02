import os
import json
import requests
from datetime import datetime
from bs4 import BeautifulSoup
from fastapi import FastAPI
import gspread
from google.oauth2.service_account import Credentials

app = FastAPI(title="AI Tender Agent Cargo V10")

BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")
GOOGLE_SHEET_ID = os.getenv("GOOGLE_SHEET_ID")

SEARCH_WORDS = [
    "перевозка грузов",
    "транспортные услуги",
    "оказание транспортных услуг",
    "доставка грузов",
    "грузоперевозки",
    "экспедиторские услуги",
    "логистические услуги",
    "транспортно-экспедиционные услуги",
]

GOOD_WORDS = [
    "перевоз",
    "груз",
    "достав",
    "логист",
    "экспед",
    "транспорт",
    "автотранспорт",
    "контейнер",
    "фура",
    "тягач",
    "рефриж",
    "cargo",
    "freight",
    "delivery",
    "logistics",
    "transport",
    "yuk",
    "tashish",
]

BAD_WORDS = [
    "арматур", "бетон", "цемент", "лаборатор", "оборудован",
    "мебел", "пленк", "стретч", "консультац", "технадзор",
    "строительств", "ремонт", "канцеляр", "компьютер", "принтер",
    "медицин", "питание", "продукт", "одежд", "обув",
    "уголь", "газ", "топливо", "дизель", "электро",
    "юридическ", "аудит", "страхован", "охрана",
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


def clean_text(text):
    return " ".join((text or "").replace("\n", " ").replace("\t", " ").split())


def is_cargo_title(title):
    title = clean_text(title).lower()

    if len(title) < 10:
        return False

    if any(bad in title for bad in BAD_TITLE_WORDS):
        return False

    if any(bad in title for bad in BAD_WORDS):
        return False

    return any(good in title for good in GOOD_WORDS)


def looks_like_bad_url(url):
    url = (url or "").lower()
    return any(part in url for part in BAD_URL_PARTS)


def is_real_cargo_tender(title, url):
    title = clean_text(title)

    if not title or not url:
        return False

    if looks_like_bad_url(url):
        return False

    if title.isdigit():
        return False

    if len(title.split()) < 2:
        return False

    return is_cargo_title(title)


def get_headers():
    return {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,application/json;q=0.8,*/*;q=0.7",
        "Accept-Language": "ru-RU,ru;q=0.9,uz;q=0.8,en;q=0.7",
        "Connection": "keep-alive",
    }


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
            timeout=10,
        )
        return response.status_code == 200
    except Exception as e:
        print("TELEGRAM ERROR:", e)
        return False


def add_tender(tenders, seen_urls, site_name, title, url):
    title = clean_text(title)
    url = clean_text(url)

    if not title or not url:
        return

    if url in seen_urls:
        return

    if not is_real_cargo_tender(title, url):
        return

    seen_urls.add(url)
    tenders.append({
        "site": site_name,
        "title": title[:250],
        "url": url,
    })


def collect_links(base_url, pages_to_scan, site_name):
    headers = get_headers()

    tenders = []
    seen_urls = set()
    total_links = 0
    total_blocks = 0

    for page_url in pages_to_scan[:20]:
        try:
            response = requests.get(page_url, headers=headers, timeout=7)

            print(f"{site_name} PAGE:", page_url)
            print(f"{site_name} STATUS:", response.status_code)
            print(f"{site_name} SIZE:", len(response.text))

            response.raise_for_status()
            soup = BeautifulSoup(response.text, "html.parser")

            for link in soup.find_all("a"):
                total_links += 1

                title = clean_text(link.get_text(" ", strip=True))
                href = link.get("href")

                if not title or not href:
                    continue

                full_url = requests.compat.urljoin(base_url, href)
                add_tender(tenders, seen_urls, site_name, title, full_url)

            for tag in soup.find_all(["div", "tr", "li", "article", "section"]):
                total_blocks += 1

                text = clean_text(tag.get_text(" ", strip=True))

                if not text:
                    continue

                if len(text) < 20 or len(text) > 300:
                    continue

                add_tender(tenders, seen_urls, site_name, text, page_url)

        except Exception as e:
            print(f"{site_name.upper()} ERROR:", e)

    print(
        f"{site_name}: total_links={total_links}, "
        f"total_blocks={total_blocks}, cargo_tenders={len(tenders)}"
    )

    return tenders


def try_json_api(api_url, site_name, base_url):
    headers = get_headers()
    headers["Accept"] = "application/json,text/plain,*/*"

    tenders = []
    seen_urls = set()

    try:
        response = requests.get(api_url, headers=headers, timeout=7)

        print(f"{site_name} API:", api_url)
        print(f"{site_name} API STATUS:", response.status_code)
        print(f"{site_name} API SIZE:", len(response.text))
        print(f"{site_name} API TYPE:", response.headers.get("content-type", ""))

        if response.status_code != 200:
            return []

        content_type = response.headers.get("content-type", "").lower()
        if "json" not in content_type and not response.text.strip().startswith(("{", "[")):
            return []

        data = response.json()
        items = []

        if isinstance(data, list):
            items = data
        elif isinstance(data, dict):
            for key in ["data", "items", "result", "results", "content", "lots", "procedures"]:
                value = data.get(key)
                if isinstance(value, list):
                    items = value
                    break
                if isinstance(value, dict):
                    for nested_key in ["data", "items", "content", "results", "lots"]:
                        nested_value = value.get(nested_key)
                        if isinstance(nested_value, list):
                            items = nested_value
                            break

        for item in items:
            if not isinstance(item, dict):
                continue

            title = (
                item.get("title")
                or item.get("name")
                or item.get("lotName")
                or item.get("productName")
                or item.get("subject")
                or item.get("description")
                or item.get("descriptionRu")
                or item.get("nameRu")
                or ""
            )

            lot_id = (
                item.get("id")
                or item.get("lotId")
                or item.get("number")
                or item.get("lotNumber")
                or item.get("procedureId")
                or ""
            )

            url = base_url
            if lot_id:
                url = requests.compat.urljoin(base_url, f"/lot/{lot_id}")

            add_tender(tenders, seen_urls, site_name, title, url)

    except Exception as e:
        print(f"{site_name} API ERROR:", api_url, e)

    print(f"{site_name} API cargo_tenders={len(tenders)}")
    return tenders


def parse_tenderweek():
    base_url = "https://www.tenderweek.com/"
    pages_to_scan = [base_url]

    for page in range(2, 8):
        pages_to_scan.append(f"{base_url}?page={page}")

    return collect_links(base_url, pages_to_scan, "Tenderweek")


def parse_xt_xarid():
    base_url = "https://xt-xarid.uz/"

    pages_to_scan = [
        "https://xt-xarid.uz/procedure/tender",
        "https://xt-xarid.uz/procedure/selection",
        "https://xt-xarid.uz/procedure/reduction",
        "https://xt-xarid.uz/procedure",
    ]

    for proc in ["tender", "selection", "reduction"]:
        for word in SEARCH_WORDS[:6]:
            pages_to_scan.extend([
                f"https://xt-xarid.uz/procedure/{proc}?queryText={word}",
                f"https://xt-xarid.uz/procedure/{proc}?search={word}",
                f"https://xt-xarid.uz/procedure/{proc}?q={word}",
            ])

    all_tenders = collect_links(base_url, pages_to_scan, "XT-Xarid")

    api_candidates = [
        "https://xt-xarid.uz/api/procedure/tender",
        "https://xt-xarid.uz/api/procedures",
        "https://xt-xarid.uz/api/tender",
        "https://xt-xarid.uz/api/lots",
        "https://xt-xarid.uz/api/search",
        "https://xt-xarid.uz/api/v1/procedure/tender",
        "https://xt-xarid.uz/api/v1/procedures",
    ]

    for api_url in api_candidates:
        all_tenders.extend(try_json_api(api_url, "XT-Xarid", base_url))

    return all_tenders


def parse_uzex():
    base_url = "https://etender.uzex.uz/"

    pages_to_scan = []

    for lot_type in [1, 2, 5, 6]:
        for page in range(0, 5):
            pages_to_scan.append(f"https://etender.uzex.uz/lots/{lot_type}/{page}")

    pages_to_scan.extend([
        "https://etender.uzex.uz/",
        "https://xarid.uzex.uz/",
    ])

    for lot_type in [1, 2, 5, 6]:
        for word in SEARCH_WORDS[:6]:
            pages_to_scan.extend([
                f"https://etender.uzex.uz/lots/{lot_type}/0?search={word}",
                f"https://etender.uzex.uz/lots/{lot_type}/0?q={word}",
            ])

    all_tenders = collect_links(base_url, pages_to_scan, "UZEX")

    api_candidates = [
        "https://etender.uzex.uz/api/lots",
        "https://etender.uzex.uz/api/lot",
        "https://etender.uzex.uz/api/tenders",
        "https://etender.uzex.uz/api/v1/lots",
        "https://etender.uzex.uz/api/v1/tenders",
        "https://xarid.uzex.uz/api/lots",
        "https://xarid.uzex.uz/api/tenders",
    ]

    for api_url in api_candidates:
        all_tenders.extend(try_json_api(api_url, "UZEX", base_url))

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
            "Средний",
            "Проверить лот: найдено по словам перевозка / транспорт / логистика / груз",
            title,
        ]

        sheet.append_row(row)
        return True

    except Exception as e:
        print("GOOGLE SHEETS ERROR:", e)
        return False


@app.get("/")
def home():
    return {"status": "AI Tender Agent Cargo V10 is running"}


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
        "Доставка товара автотранспортом": is_real_cargo_tender(
            "Доставка товара автотранспортом",
            "https://www.tenderweek.com/tender-99999",
        ),
    }


@app.get("/scan")
def scan():
    print("SCAN STARTED")

    found_total = 0
    new_total = 0
    duplicate_total = 0
    all_tenders = []
    seen_urls = set()

    message = "📊 AI Tender Agent Cargo V10 Scan завершён\n\n"

    sources = [
        ("Tenderweek", parse_tenderweek),
        ("XT-Xarid", parse_xt_xarid),
        ("UZEX", parse_uzex),
    ]

    for source_name, parser in sources:
        print("PARSING:", source_name)

        try:
            result = parser()
            all_tenders.extend(result)
            message += f"{source_name}: найдено по грузоперевозкам {len(result)}\n"
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

        print("=" * 50)
        print("FOUND CARGO:", title)
        print("URL:", url)
        print("=" * 50)

        saved = save_to_sheet(tender["site"], title, url)

        if saved:
            new_total += 1
            text = (
                f"🚚 Новый возможный лот по перевозке / логистике\n\n"
                f"📌 Источник: {tender['site']}\n\n"
                f"📋 {title}\n\n"
                f"🔗 {url}"
            )
            send_telegram(text)
        else:
            duplicate_total += 1

    message += (
        f"Всего найдено: {found_total}\n"
        f"Новых сохранено: {new_total}\n"
        f"Дубликатов пропущено: {duplicate_total}"
    )

    send_telegram(message)

    return {
        "status": "success",
        "version": "cargo_v10",
        "found_total": found_total,
        "new_total": new_total,
        "duplicates": duplicate_total,
    }


@app.post("/webhook")
def webhook():
    return scan()


@app.get("/webhook")
def webhook_get():
    return scan()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        app,
        host="0.0.0.0",
        port=int(os.getenv("PORT", 10000)),
    )
