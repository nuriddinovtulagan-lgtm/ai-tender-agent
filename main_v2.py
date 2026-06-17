import os
import json
import re
import requests
from datetime import datetime
from urllib.parse import urljoin

from bs4 import BeautifulSoup
from fastapi import FastAPI
import gspread
from google.oauth2.service_account import Credentials

app = FastAPI(title="AI Tender Agent Cargo V26 Smart Filter")

BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")
GOOGLE_SHEET_ID = os.getenv("GOOGLE_SHEET_ID")

VERSION = "cargo_v26_smart_filter"

# ============================================================
# Cargo V23/V24 logic
# V23: Soft Logistics Filter — all transport/freight lots are relevant.
# V24: Company Knowledge Base — compare tender requirements with company docs.
# ============================================================

SERVICE_PHRASES = [
    "перевозка грузов", "перевозке грузов", "перевозки грузов", "перевозка груза",
    "перевозка", "перевозке", "грузоперевоз", "грузовые перевозки",
    "доставка грузов", "доставка груза", "доставка товара автотранспортом",
    "транспортные услуги", "оказание транспортных услуг", "транспортная услуга",
    "услуги транспорта", "услуги по перевозке", "услуги перевозки",
    "транспортно-экспедиционные услуги", "транспортно экспедиционные услуги",
    "экспедиторские услуги", "логистические услуги", "аренда транспорта",
    "аренда транспортных средств", "автотранспортные услуги",
    "международные автомобильные перевозки", "международная перевозка", "международные перевозки",
    "cargo transportation", "freight transportation", "logistics service", "transport service", "delivery service",
    "yuk tashish", "yuklarni tashish", "yuk tashish xizmati", "yuklarni tashish xizmati",
    "yuk tashuvchi xizmatlari", "yuklarni tashuvchi", "transport xizmati", "transport xizmatlari",
    "logistika xizmati", "logistika xizmatlari", "ekspeditorlik xizmati", "ekspeditorlik xizmatlari",
    "xalqaro yuk tashish", "xalqaro tashuv", "xalqaro tashish", "avtotransport xizmati",
    "avtotransport xizmatlari", "ijarasi", "ijara", "avtransport vositalarini ijarasi",
]

BAD_TITLE_WORDS = [
    "регистрация", "зарегистрироваться", "войти", "выход", "стать заказчиком", "стать поставщиком",
    "english", "русский", "ўзбекча", "приглашение", "мои заявки", "моих заявок",
    "вопрос", "ответ", "помощь", "контакты", "написать нам письмо", "о сайте", "правила",
    "дата публикации", "личный кабинет", "кабинет", "профиль",
]

BAD_URL_PARTS = [
    "register", "login", "logout", "signin", "signup", "cabinet", "profile", "account", "user",
    "my", "add.html", "/add", "create", "invited", "invitation", "english", "/en/", "news",
    "blog", "faq", "help", "contact", "about", "rules", "terms", "privacy", "advertising",
    "banner", "calendar", "archive", "feedback", "javascript:", "mailto:", "tel:",
]

HARD_BAD_WORDS = [
    "лаборатор", "мебел", "канцеляр", "компьютер", "принтер", "медицин", "питание", "одежд",
    "обув", "юридическ", "аудит", "охрана", "дезинфек", "дезинсек", "автоматизац", "кредит",
    "программ", "система принятия решений", "it", "software", "crm", "erp", "сервер", "телеком",
    "интернет", "сайт", "портал",
]

# Cargo V26 Smart Filter:
# These words indicate repair, maintenance, spare parts, tires, fuel, oils,
# vehicle purchase or service works. They are NOT cargo transportation tenders.
REPAIR_MAINTENANCE_STOP_WORDS = [
    "texnik xizmat", "texnik xizmat ko'rsatish", "texnik xizmat ko‘rsatish",
    "texnik xizmat korsatish", "texnik xizmat ko’rsatish",
    "техник хизмат", "техник хизмат кўрсатиш", "техник хизмат курсатиш",
    "техническое обслуживание", "техобслуживание", "технического обслуживания",
    "техническое сервисное обслуживание", "сервисное обслуживание",
    "обслуживание автомобилей", "обслуживание автотранспорта",
    "обслуживание грузовых автомобилей", "обслуживание транспорта",
    "ремонт", "ремонт автомобилей", "ремонт автотранспорта", "ремонт транспорта",
    "ремонт грузовых автомобилей", "ремонт спецтехники", "ремонт машин",
    "автосервис", "сервис автомобилей", "сервис транспорта",
    "диагностика", "диагностика автомобилей", "диагностика транспорта",
    "шиномонтаж", "мойка автомобилей", "мойка транспорта", "кузовной ремонт",
    "замена масла", "технический осмотр", "техосмотр",
]

SPARE_PARTS_STOP_WORDS = [
    "запчасти", "запасные части", "автозапчасти", "ehtiyot qismlar",
    "эҳтиёт қисмлар", "extiyot qismlar", "ehtiyot qism",
    "шины", "автошины", "покрышки", "резина", "шиналар", "shina", "shinalar",
    "аккумулятор", "аккумуляторы", "akkumulyator",
    "масло", "моторное масло", "смазочные материалы", "смазка", "гсм",
    "yoqilg'i", "yoqilgi", "топливо", "дизельное топливо", "бензин",
    "фильтр масляный", "фильтр воздушный", "колодки", "тормозные колодки",
    "двигатель", "мотор", "коробка передач", "ходовая часть",
]

VEHICLE_PURCHASE_STOP_WORDS = [
    "закупка автомобиля", "закупка автомобилей", "поставка автомобиля",
    "поставка автомобилей", "покупка автомобиля", "покупка автомобилей",
    "приобретение автомобиля", "приобретение автомобилей",
    "грузовой автомобиль", "грузовые автомобили", "легковой автомобиль",
    "автобус", "микроавтобус", "самосвал харид", "avtomobil xarid",
    "avtotransport vositasi xarid", "transport vositasi xarid",
    "сотиб олиш", "харид қилиш", "харид килиш",
]

# These terms preserve real transportation/rental lots even if some neutral vehicle words are present.
TRANSPORT_SERVICE_ALLOW_WORDS = [
    "перевозка грузов", "перевозка груза", "перевозке грузов", "перевозки грузов",
    "доставка грузов", "доставка груза", "доставка товара",
    "транспортно-экспедиционные услуги", "экспедиторские услуги",
    "логистические услуги", "международная перевозка", "международные перевозки",
    "мультимодальная перевозка", "контейнерная перевозка",
    "yuk tashish", "yuklarni tashish", "yuk tashish xizmati",
    "xalqaro yuk tashish", "logistika xizmatlari", "ekspeditorlik xizmatlari",
    "transport xizmati", "transport xizmatlari",
]


LOGISTICS_TYPES = {
    "Международная перевозка": ["xalqaro", "международ", "international", "cmr", "customs", "bojxona", "тамож"],
    "Внутренняя перевозка": [
        "toshkent", "ташкент", "samarqand", "самарканд", "buxoro", "бухара", "urganch", "ургенч",
        "namangan", "наманган", "andijon", "андижан", "farg", "ферган", "navoiy", "навоий",
        "qarshi", "карши", "jizzax", "джизак", "termez", "термез", "oqdaryo", "окдар",
    ],
    "Строительная логистика": ["qurilish", "строитель", "монтаж", "дорожн", "йул", "yo'l", "yo‘llar", "yollardan"],
    "Экспедирование": ["экспедитор", "ekspeditor", "логистик", "logistika"],
    "Аренда транспорта с перевозкой": ["ijara", "ijarasi", "аренда транспорта", "аренда транспортных средств", "avtransport vositalarini ijarasi"],
    "Рефрижераторная перевозка": ["рефриж", "ref", "реф", "холод", "температур"],
    "Негабарит/тяжеловес": ["негабарит", "тяжеловес", "og'ir vazn", "og’ir vazn", "gabarit bo", "multimodal", "мультимод"],
}

COMPANY_KNOWLEDGE_BASE = {
    "Коммерческое предложение / ценовое предложение": {"status": "Шаблон есть", "owner": "Тендерный менеджер", "note": "Заполнить цену, срок и условия под конкретный лот"},
    "Реквизиты компании": {"status": "Есть", "owner": "Бухгалтерия", "note": "Проверить актуальность реквизитов"},
    "Свидетельство о регистрации компании": {"status": "Есть", "owner": "Бухгалтерия", "note": "Гувохнома / регистрационный документ"},
    "Устав компании": {"status": "Есть", "owner": "Директор", "note": "Проверить актуальную редакцию"},
    "Доверенность на подписанта или приказ директора": {"status": "Проверить", "owner": "Директор", "note": "Если подписывает не директор — доверенность"},
    "Паспортные данные / ID подписанта": {"status": "Проверить", "owner": "Директор", "note": "Нужны данные подписанта"},
    "Информация об опыте аналогичных перевозок": {"status": "Проверить", "owner": "Тендерный менеджер", "note": "Собрать договоры и акты"},
    "Договоры/акты по прошлым международным перевозкам": {"status": "Проверить", "owner": "Тендерный менеджер", "note": "Для подтверждения опыта"},
    "Список транспорта / данные по машинам": {"status": "Проверить", "owner": "Логист", "note": "Собственный и партнерский транспорт"},
    "Данные по водителям": {"status": "Проверить", "owner": "Логист", "note": "Права, опыт, допуски"},
    "Контакты ответственного менеджера": {"status": "Есть", "owner": "Тендерный менеджер", "note": "Указать ответственного"},
    "Документы для международной перевозки": {"status": "Проверить", "owner": "Логист", "note": "CMR, разрешения, маршрут"},
    "CMR накладная / международные транспортные документы": {"status": "Проверить", "owner": "Логист", "note": "Для международных перевозок"},
    "Документы на транспорт для международной перевозки": {"status": "Проверить", "owner": "Логист", "note": "Техпаспорта, разрешения, страховки"},
    "Справка об отсутствии налоговой задолженности": {"status": "Нужно получить", "owner": "Бухгалтерия", "note": "Обычно нужна свежая справка"},
    "Банковская гарантия": {"status": "Нужно получить", "owner": "Бухгалтерия", "note": "Проверить сумму и срок"},
    "Обеспечение заявки / залог": {"status": "Нужно проверить", "owner": "Бухгалтерия", "note": "Проверить размер обеспечения"},
    "Страховой полис груза / CMR страхование": {"status": "Проверить", "owner": "Логист", "note": "Проверить покрытие"},
    "Лицензия / разрешение на транспортную деятельность": {"status": "Проверить", "owner": "Директор", "note": "Если требуется в ТЗ"},
    "Квалификационная анкета участника": {"status": "Подготовить", "owner": "Тендерный менеджер", "note": "Заполнить по требованиям"},
    "Ответы на квалификационные вопросы UZEX": {"status": "Подготовить", "owner": "Тендерный менеджер", "note": "Заполнить квалификационные поля"},
    "Подтверждение соответствия техническому заданию": {"status": "Подготовить", "owner": "Тендерный менеджер", "note": "Ответить по требованиям ТЗ"},
    "График оказания услуг / план перевозки": {"status": "Подготовить", "owner": "Логист", "note": "Маршрут, сроки, тип транспорта"},
}

EXTRA_SHEET_COLUMNS = [
    "Заказчик", "Сумма", "Валюта", "Оплата", "Срок оплаты", "Срок оказания услуг",
    "Тип логистики", "Маршрут", "Тип транспорта", "Требования", "Документы нужны",
    "Готовность компании", "Не хватает документов", "Проверить/подготовить",
    "Предупреждения по документам", "Ответственные задачи", "Рекомендация AI",
]

TENDER_MANAGER_COLUMNS = ["Подавать", "Ответственный", "Дата решения", "Статус участия", "Комментарий директора"]


def clean_text(text):
    return " ".join((text or "").replace("\n", " ").replace("\t", " ").split())


def normalize_text(text):
    text = clean_text(text).lower()
    return text.replace("’", "'").replace("‘", "'").replace("ʻ", "'").replace("`", "'").replace("ё", "е")


def get_headers(json_mode=False):
    return {
        "User-Agent": "Mozilla/5.0",
        "Accept": "application/json,text/plain,*/*" if json_mode else "text/html,application/xhtml+xml,*/*",
        "Accept-Language": "ru-RU,ru;q=0.9,uz;q=0.8,en;q=0.7",
        "Content-Type": "application/json" if json_mode else "text/html",
    }


def has_transport_intent(text):
    t = normalize_text(text)

    # V26: repair/maintenance/spare parts are not transportation tenders.
    if has_repair_or_maintenance_context(t):
        return False

    if any(phrase in t for phrase in SERVICE_PHRASES):
        return True

    cargo = any(w in t for w in ["yuk", "груз", "cargo", "freight"])
    action = any(w in t for w in ["tashish", "перевоз", "transport", "достав", "xizmat", "услуг"])
    return cargo and action


def looks_like_bad_url(url):
    return any(part in (url or "").lower() for part in BAD_URL_PARTS)


def has_real_transport_service_phrase(text):
    t = normalize_text(text)
    return any(w in t for w in TRANSPORT_SERVICE_ALLOW_WORDS)


def has_repair_or_maintenance_context(text):
    t = normalize_text(text)
    stop_groups = REPAIR_MAINTENANCE_STOP_WORDS + SPARE_PARTS_STOP_WORDS + VEHICLE_PURCHASE_STOP_WORDS
    return any(w in t for w in stop_groups)


def smart_filter_rejection_reason(text):
    t = normalize_text(text)

    for w in REPAIR_MAINTENANCE_STOP_WORDS:
        if w in t:
            return "rejected_repair_maintenance:" + w

    for w in SPARE_PARTS_STOP_WORDS:
        if w in t:
            return "rejected_spare_parts_fuel_tires:" + w

    for w in VEHICLE_PURCHASE_STOP_WORDS:
        if w in t:
            return "rejected_vehicle_purchase:" + w

    return ""


def filter_reason(title, url):
    t = normalize_text(title)
    if not title or not url:
        return "empty_title_or_url"
    if looks_like_bad_url(url):
        return "bad_url"
    if title.isdigit() or len(title.split()) < 2 or len(t) < 10:
        return "too_short"
    for bad in BAD_TITLE_WORDS:
        if bad in t:
            return "bad_title_word:" + bad

    smart_reject = smart_filter_rejection_reason(t)
    if smart_reject:
        return smart_reject

    if has_transport_intent(t):
        for phrase in SERVICE_PHRASES:
            if phrase in t:
                return "accepted_transport_phrase:" + phrase
        return "accepted_transport_intent"
    for bad in HARD_BAD_WORDS:
        if bad in t:
            return "hard_bad_word:" + bad
    return "not_transport_lot"


def is_real_cargo_tender(title, url):
    return filter_reason(title, url).startswith("accepted_")


def get_sheet():
    raw_json = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON")
    if not raw_json:
        raise Exception("GOOGLE_SERVICE_ACCOUNT_JSON is empty")
    info = json.loads(raw_json)
    creds = Credentials.from_service_account_info(
        info,
        scopes=["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"],
    )
    client = gspread.authorize(creds)
    return client.open_by_key(GOOGLE_SHEET_ID).worksheet("Тендеры")


def send_telegram(text):
    if not BOT_TOKEN or not CHAT_ID:
        return False
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            json={"chat_id": CHAT_ID, "text": text[:3900]},
            timeout=8,
        )
        return r.status_code == 200
    except Exception as e:
        print("TELEGRAM ERROR:", e)
        return False


def make_key(site, title, url):
    return f"{site}:{normalize_text(title)[:120]}:{url}".lower()


def add_tender(tenders, seen, site, title, url):
    title = clean_text(title)
    url = clean_text(url)
    if not title or not url:
        return
    key = make_key(site, title, url)
    if key in seen:
        return
    if not is_real_cargo_tender(title, url):
        return
    seen.add(key)
    tenders.append({"site": site, "title": title[:300], "url": url, "reason": filter_reason(title, url)})


def add_raw_candidate(candidates, seen, site, title, url):
    title = clean_text(title)
    url = clean_text(url)
    if not title or not url:
        return
    key = make_key(site, title, url)
    if key in seen:
        return
    seen.add(key)
    candidates.append({"site": site, "title": title[:300], "url": url, "accepted": is_real_cargo_tender(title, url), "reason": filter_reason(title, url)})


def collect_links(base_url, pages, site, limit=10, raw=False):
    tenders, candidates, seen = [], [], set()
    for page_url in pages[:limit]:
        try:
            r = requests.get(page_url, headers=get_headers(), timeout=12)
            print(f"{site} PAGE:", page_url, "STATUS:", r.status_code, "SIZE:", len(r.text))
            if r.status_code != 200:
                continue
            soup = BeautifulSoup(r.text, "html.parser")
            for a in soup.find_all("a"):
                title = clean_text(a.get_text(" ", strip=True))
                href = a.get("href")
                if not title or not href:
                    continue
                url = urljoin(base_url, href)
                if raw:
                    add_raw_candidate(candidates, seen, site, title, url)
                else:
                    add_tender(tenders, seen, site, title, url)
            for tag in soup.find_all(["div", "tr", "li", "article", "section"]):
                text = clean_text(tag.get_text(" ", strip=True))
                if 20 <= len(text) <= 300:
                    if raw:
                        add_raw_candidate(candidates, seen, site, text, page_url)
                    else:
                        add_tender(tenders, seen, site, text, page_url)
        except Exception as e:
            print(f"{site} HTML ERROR:", e)
    return candidates if raw else tenders


def flatten_items(data):
    items = []
    if isinstance(data, list):
        for x in data:
            if isinstance(x, dict):
                items.append(x)
                items.extend(flatten_items(x))
            elif isinstance(x, list):
                items.extend(flatten_items(x))
    elif isinstance(data, dict):
        for v in data.values():
            if isinstance(v, dict):
                items.append(v)
                items.extend(flatten_items(v))
            elif isinstance(v, list):
                items.extend(flatten_items(v))
    return items


def item_url(item, base_url, site):
    for key in ["url", "link", "href"]:
        if item.get(key):
            return urljoin(base_url, str(item.get(key)))
    lot_id = item.get("id") or item.get("lotId") or item.get("number") or item.get("lotNumber") or item.get("procedureId") or item.get("display_no")
    if site == "UZEX" and lot_id:
        return f"https://etender.uzex.uz/lot/{lot_id}"
    if site == "XT-Xarid" and lot_id:
        return f"https://xt-xarid.uz/tender/{lot_id}"
    return base_url


def item_title(item):
    keys = ["title", "name", "lotName", "productName", "subject", "description", "descriptionRu", "nameRu", "nameUz", "goodsName", "serviceName", "procedureName", "category_name", "company_name"]
    parts = []
    for key in keys:
        value = item.get(key)
        if value and str(value).lower() not in ["tender", "none", "null"]:
            parts.append(str(value))
    meta = item.get("meta")
    if isinstance(meta, dict):
        for gm in meta.get("good_maps", []):
            if isinstance(gm, dict):
                if gm.get("name"):
                    parts.append(str(gm.get("name")))
                category = gm.get("category")
                if isinstance(category, dict) and category.get("title"):
                    parts.append(str(category.get("title")))
        if meta.get("company_name"):
            parts.append(str(meta.get("company_name")))
    texts = [str(v) for v in item.values() if isinstance(v, str) and len(v) > 8]
    parts.extend(texts[:3])
    return clean_text(" | ".join(parts))


def parse_tenderweek(raw=False):
    base_url = "https://www.tenderweek.com/"
    pages = [base_url]
    queries = ["перевозка грузов", "транспортные услуги", "логистические услуги", "транспортно-экспедиционные услуги", "доставка грузов"]
    for page in range(2, 10):
        pages.append(f"{base_url}?page={page}")
    for q in queries:
        pages.append(f"{base_url}?search={requests.utils.quote(q)}")
        pages.append(f"{base_url}?q={requests.utils.quote(q)}")
    return collect_links(base_url, pages, "Tenderweek", limit=20, raw=raw)


def parse_uzex_api(raw=False):
    url = "https://apietender.uzex.uz/api/common/TradeList"
    base_url = "https://etender.uzex.uz/"
    tenders, candidates, seen = [], [], set()
    payloads = [{"TypeId": 1, "From": 1, "To": 300, "System_Id": 0}, {"TypeId": 2, "From": 1, "To": 300, "System_Id": 0}, {"TypeId": 3, "From": 1, "To": 300, "System_Id": 0}]
    for payload in payloads:
        try:
            r = requests.post(url, headers=get_headers(json_mode=True), json=payload, timeout=15)
            print("UZEX API STATUS:", r.status_code, "SIZE:", len(r.text), "TYPE:", r.headers.get("content-type", ""))
            if r.status_code != 200:
                continue
            for item in flatten_items(r.json()):
                if not isinstance(item, dict):
                    continue
                title = item_title(item)
                tender_url = item_url(item, base_url, "UZEX")
                if raw:
                    add_raw_candidate(candidates, seen, "UZEX", title, tender_url)
                else:
                    add_tender(tenders, seen, "UZEX", title, tender_url)
        except Exception as e:
            print("UZEX API ERROR:", e)
    return candidates if raw else tenders


def parse_xt_xarid_api(raw=False):
    url = "https://api.xt-xarid.uz/rpc"
    base_url = "https://xt-xarid.uz/"
    tenders, candidates, seen = [], [], set()
    for offset in [0, 100, 200, 300]:
        payload = {"id": 1, "jsonrpc": "2.0", "method": "ref", "params": {"ref": "ref_tender_public", "op": "read", "limit": 100, "offset": offset, "filters": {}}}
        try:
            r = requests.post(url, headers=get_headers(json_mode=True), json=payload, timeout=15)
            print("XT-Xarid API STATUS:", r.status_code, "SIZE:", len(r.text), "TYPE:", r.headers.get("content-type", ""))
            if r.status_code != 200:
                continue
            for item in flatten_items(r.json()):
                if not isinstance(item, dict):
                    continue
                title = item_title(item)
                tender_url = item_url(item, base_url, "XT-Xarid")
                if raw:
                    add_raw_candidate(candidates, seen, "XT-Xarid", title, tender_url)
                else:
                    add_tender(tenders, seen, "XT-Xarid", title, tender_url)
        except Exception as e:
            print("XT-Xarid API ERROR:", e)
    return candidates if raw else tenders


def parse_uzex(raw=False):
    return parse_uzex_api(raw=raw)


def parse_xt_xarid(raw=False):
    return parse_xt_xarid_api(raw=raw)


def extract_lot_id_from_url(url):
    if not url:
        return None
    for part in reversed(str(url).strip().rstrip("/").split("/")):
        if part.isdigit():
            return part
    return None


def safe_json_loads(raw, default=None):
    if default is None:
        default = []
    if not raw:
        return default
    try:
        return json.loads(raw)
    except Exception:
        return default


def get_uzex_lot_data(lot_id):
    api_url = f"https://apietender.uzex.uz/api/common/GetTrade/{lot_id}/0"
    r = requests.get(api_url, headers=get_headers(json_mode=True), timeout=20)
    r.raise_for_status()
    return r.json()


def get_uzex_trade(lot_id):
    return get_uzex_lot_data(lot_id)


def extract_budget_products(trade):
    return safe_json_loads(trade.get("budget_products"), [])


def short_requirements_from_trade(trade):
    parts = []
    products = extract_budget_products(trade)
    if products:
        p = products[0]
        if p.get("Product_Name"):
            parts.append("Предмет: " + clean_text(p.get("Product_Name")))
        if p.get("Description"):
            parts.append(clean_text(p.get("Description"))[:500])
        if p.get("Category_Name"):
            parts.append("Категория: " + clean_text(p.get("Category_Name")))
        if p.get("Delivery_Term"):
            parts.append(f"Срок оказания: {p.get('Delivery_Term')} дней")
    q_fields = trade.get("js_qualification_fields") or []
    q_short = []
    if isinstance(q_fields, list):
        for q in q_fields[:8]:
            if isinstance(q, dict):
                name = clean_text(q.get("name", "") or q.get("title", "") or q.get("label", ""))
                if name:
                    q_short.append(name[:120])
    if q_short:
        parts.append("Квалификация: " + "; ".join(q_short))
    return clean_text(" | ".join(parts))[:1400]


def classify_logistics_type(text):
    t = normalize_text(text)
    hits = []
    for name, words in LOGISTICS_TYPES.items():
        if any(w in t for w in words):
            hits.append(name)
    if not hits and has_transport_intent(t):
        hits.append("Перевозка грузов")
    return hits or ["Не определено"]


def extract_route_and_transport(title, text):
    t = normalize_text(title + " " + text)
    route_hints = []
    transport_hints = []
    route_words = ["ташкент", "toshkent", "узбекистан", "o'zbekiston", "ўзбекистон", "казахстан", "qozog", "китай", "xitoy", "россия", "москва", "moskva", "хоргос", "xorgos", "яллама", "yallama", "самарканд", "samarqand", "бухара", "buxoro", "андижан", "andijon", "наманган", "namangan", "ургенч", "urganch", "oqdaryo", "окдар"]
    for w in route_words:
        if w in t and w not in route_hints:
            route_hints.append(w)
    pairs = re.findall(r"(ташкент|toshkent|самарканд|samarqand|ургенч|urganch|наманган|namangan|бухара|buxoro)\s*[-–—]\s*([a-zа-яёʻ'’ўғқҳ ]{3,40})", t, flags=re.IGNORECASE)
    for a, b in pairs[:3]:
        route_hints.append(f"{a} → {clean_text(b)[:35]}")
    if any(x in t for x in ["рефриж", "ref", "реф"]):
        transport_hints.append("Рефрижератор")
    if "тент" in t or "tent" in t:
        transport_hints.append("Тент")
    if "mega" in t or "мега" in t:
        transport_hints.append("Мега")
    if "изотерм" in t:
        transport_hints.append("Изотерм")
    if "контейнер" in t:
        transport_hints.append("Контейнер")
    if "борт" in t or "bortli" in t:
        transport_hints.append("Бортовой транспорт")
    if "низкорам" in t or "негабарит" in t or "тяжеловес" in t:
        transport_hints.append("Низкорамный трал / спецтранспорт")
    if "фура" in t or "fura" in t or "20 тонн" in t or "22 тонн" in t or "22 ton" in t:
        transport_hints.append("Фура 20-22 тонн")
    if "ijara" in t or "аренда" in t:
        transport_hints.append("Аренда транспорта")
    return {
        "route_text": ", ".join(dict.fromkeys(route_hints[:10])) if route_hints else "Маршрут не найден в API/ТЗ",
        "transport_text": ", ".join(dict.fromkeys(transport_hints[:10])) if transport_hints else "Тип транспорта не найден в API/ТЗ",
    }


def detect_required_documents_from_text(title, text, trade=None):
    t = normalize_text((title or "") + " " + (text or ""))
    checklist = []
    warnings = []

    def add_doc(name, reason=""):
        if name not in [x["document"] for x in checklist]:
            checklist.append({"document": name, "reason": reason, "status": "Подготовить"})

    def add_warning(w):
        if w not in warnings:
            warnings.append(w)

    for name, reason in [
        ("Коммерческое предложение / ценовое предложение", "Основной документ для участия"),
        ("Реквизиты компании", "Нужно для договора и заявки"),
        ("Свидетельство о регистрации компании", "Стандартный регистрационный документ"),
        ("Устав компании", "Часто требуется для подтверждения полномочий"),
        ("Доверенность на подписанта или приказ директора", "Если заявку подписывает не директор"),
        ("Паспортные данные / ID подписанта", "Для подтверждения полномочий"),
        ("Информация об опыте аналогичных перевозок", "Для подтверждения квалификации"),
        ("Список транспорта / данные по машинам", "Для подтверждения возможности выполнить перевозку"),
        ("Данные по водителям", "Если требуется допуск водителей к перевозке"),
        ("Контакты ответственного менеджера", "Для связи с заказчиком"),
    ]:
        add_doc(name, reason)
    if any(w in t for w in ["лиценз", "license", "ruxsatnoma", "рухсатнома"]):
        add_doc("Лицензия / разрешение на транспортную деятельность", "Есть признак требования лицензии")
        add_warning("Проверить наличие лицензии или разрешения")
    if any(w in t for w in ["налог", "задолж", "qarzdorlik", "soliq"]):
        add_doc("Справка об отсутствии налоговой задолженности", "Есть признак требования по налоговой чистоте")
        add_warning("Проверить налоговую задолженность до подачи")
    if any(w in t for w in ["банк гаран", "банковская гарантия", "bank kafolat", "kafolat"]):
        add_doc("Банковская гарантия", "Есть признак банковской гарантии")
        add_warning("Срочно проверить сумму и срок банковской гарантии")
    if any(w in t for w in ["обеспечение заявки", "залог", "deposit", "zakalat", "garov"]):
        add_doc("Обеспечение заявки / залог", "Есть признак обеспечения заявки")
        add_warning("Проверить размер обеспечения заявки")
    if any(w in t for w in ["страхов", "insurance", "sug'urta", "sugurta"]):
        add_doc("Страховой полис груза / CMR страхование", "Есть признак требования страхования")
    if any(w in t for w in ["cmr", "смр"]):
        add_doc("CMR накладная / международные транспортные документы", "Есть признак международной перевозки")
        add_doc("Документы на транспорт для международной перевозки", "CMR требует корректных данных транспорта")
    if any(w in t for w in ["тамож", "customs", "bojxona"]):
        add_doc("Таможенные документы / данные для оформления", "Есть признак таможенного оформления")
    if any(w in t for w in ["международ", "xalqaro", "international"]):
        add_doc("Документы для международной перевозки", "Тендер похож на международную перевозку")
        add_doc("Договоры/акты по прошлым международным перевозкам", "Для подтверждения опыта")
        add_warning("Проверить маршрут, погранпереходы, разрешения и транзитные страны")
    if any(w in t for w in ["негабарит", "тяжеловес", "og'ir vazn", "og’ir vazn", "gabarit bo"]):
        add_doc("Разрешения на негабаритный / тяжеловесный груз", "Есть признак сложной перевозки")
        add_doc("Схема крепления груза / план перевозки", "Для сложной логистики")
        add_doc("Фото/техпаспорт спецтранспорта", "Для подтверждения технической возможности")
    if any(w in t for w in ["рефриж", "холод", "температур", "refrigerator", "ref"]):
        add_doc("Документы на рефрижераторный транспорт", "Есть температурный режим")
        add_doc("Подтверждение температурного режима", "Может потребоваться для груза")
    if any(w in t for w in ["техническое задание", "тз", "technical", "texnik topshiriq", "talab"]):
        add_doc("Подтверждение соответствия техническому заданию", "Нужно ответить по требованиям ТЗ")
    if any(w in t for w in ["срок", "delivery", "muddat", "календар"]):
        add_doc("График оказания услуг / план перевозки", "Есть требования по срокам")
    if any(w in t for w in ["тендерная комиссия", "квалификац", "qualification", "malaka"]):
        add_doc("Квалификационная анкета участника", "Есть квалификационные требования")
    if trade:
        q_fields = trade.get("js_qualification_fields") or []
        if isinstance(q_fields, list) and q_fields:
            add_doc("Ответы на квалификационные вопросы UZEX", "В API есть квалификационные поля")
            for q in q_fields[:10]:
                if isinstance(q, dict):
                    name = clean_text(q.get("name", "") or q.get("title", "") or q.get("label", ""))
                    if name:
                        add_doc("Квалификационный документ: " + name[:80], "Из поля квалификации UZEX")
    responsible = [
        {"role": "Тендерный менеджер", "task": "Собрать документы и проверить дедлайн"},
        {"role": "Логист", "task": "Проверить маршрут, транспорт, тоннаж, сроки"},
        {"role": "Бухгалтерия", "task": "Проверить налоги, оплату, обеспечение заявки"},
        {"role": "Директор", "task": "Принять решение участвовать / не участвовать"},
    ]
    return {"required_documents": checklist, "warnings": warnings, "responsible_tasks": responsible, "documents_count": len(checklist), "summary": "; ".join([x["document"] for x in checklist[:12]])}


def normalize_doc_name(name):
    return "Ответы на квалификационные вопросы UZEX" if normalize_text(name).startswith("квалификационный документ") else name


def compare_with_company_kb(required_documents):
    results = []
    ok_count = check_count = missing_count = 0
    for item in required_documents:
        doc_name = item.get("document", "")
        base_name = normalize_doc_name(doc_name)
        kb = COMPANY_KNOWLEDGE_BASE.get(base_name)
        if kb:
            status, owner, note = kb.get("status", "Проверить"), kb.get("owner", "Не назначен"), kb.get("note", "")
        else:
            status, owner, note = "Нет в базе", "Тендерный менеджер", "Добавить в базу документов компании"
        if status in ["Есть", "Шаблон есть"]:
            readiness = "Есть"
            ok_count += 1
        elif status in ["Проверить", "Нужно проверить", "Подготовить"]:
            readiness = "Проверить/подготовить"
            check_count += 1
        else:
            readiness = "Не хватает"
            missing_count += 1
        results.append({"document": doc_name, "required_reason": item.get("reason", ""), "company_status": status, "readiness": readiness, "owner": owner, "note": note})
    total = len(results)
    score = round((ok_count + check_count * 0.5) / total * 100, 1) if total else 0
    return {
        "total_required": total,
        "available": ok_count,
        "to_check_or_prepare": check_count,
        "missing": missing_count,
        "readiness_score": score,
        "results": results,
        "missing_documents": [r for r in results if r["readiness"] == "Не хватает"],
        "to_check_documents": [r for r in results if r["readiness"] == "Проверить/подготовить"],
        "summary": f"Требуется: {total}; есть: {ok_count}; проверить/подготовить: {check_count}; не хватает: {missing_count}; готовность: {score}%",
    }



# ============================================================
# Cargo V25 — Tender Intelligence
# Cargo V26 — Draft Assistant
# Cargo V27 — Market Analytics
# ============================================================

REGION_DISTANCE_KM = {
    "ташкент": 0, "toshkent": 0,
    "самарканд": 310, "samarqand": 310,
    "наманган": 300, "namangan": 300,
    "ургенч": 970, "urganch": 970,
    "бухара": 580, "buxoro": 580,
    "андижан": 350, "andijon": 350,
    "ферган": 320, "farg": 320,
    "карши": 520, "qarshi": 520,
    "навоий": 470, "navoiy": 470,
    "джизак": 200, "jizzax": 200,
    "термез": 710, "termez": 710,
    "нукус": 1100, "nukus": 1100,
}

def extract_money_number(value):
    try:
        return float(value or 0)
    except Exception:
        try:
            return float(str(value).replace(" ", "").replace(",", "."))
        except Exception:
            return 0.0


def detect_route_distance_km(route_text, full_text):
    t = normalize_text((route_text or "") + " " + (full_text or ""))
    distances = []
    for city, km in REGION_DISTANCE_KM.items():
        if city in t and km:
            distances.append(km)
    if not distances:
        return None
    return max(distances)


def detect_vehicle_need(text):
    t = normalize_text(text)
    vehicles = []
    if any(x in t for x in ["фура", "fura", "22 тонн", "22 ton", "20 тонн", "tent", "тент", "mega", "мега", "ref", "реф"]):
        vehicles.append("Фура 20-22 тонн")
    if any(x in t for x in ["рефриж", "реф", "ref", "температур"]):
        vehicles.append("Рефрижератор")
    if any(x in t for x in ["тент", "tent"]):
        vehicles.append("Тент")
    if any(x in t for x in ["борт", "bortli"]):
        vehicles.append("Бортовой транспорт")
    if any(x in t for x in ["самосвал", "samosval"]):
        vehicles.append("Самосвал")
    if any(x in t for x in ["низкорам", "трал", "негабарит", "тяжеловес"]):
        vehicles.append("Спецтранспорт / трал")

    qty_matches = re.findall(r"(\d{1,3})\s*(?:ед|единиц|машин|авто|фур|ta|дона)", t)
    qty = int(qty_matches[0]) if qty_matches else 1
    return {"vehicle_types": list(dict.fromkeys(vehicles)) or ["Тип транспорта уточнить"], "estimated_vehicle_count": qty}


def estimate_transport_economics(trade, title, full_text, route_text, transport_text):
    amount = extract_money_number(trade.get("start_cost"))
    currency = trade.get("currency_codeabc") or trade.get("currency_name") or "UZS"

    budget_products = extract_budget_products(trade)
    delivery_days = ""
    if budget_products:
        delivery_days = budget_products[0].get("Delivery_Term", "") or ""

    distance_km = detect_route_distance_km(route_text, full_text)
    vehicle_need = detect_vehicle_need(full_text + " " + transport_text)
    t = normalize_text(full_text)
    logistics_type = ", ".join(classify_logistics_type(full_text))

    if distance_km is None:
        distance_km = 350 if "Внутренняя перевозка" in logistics_type else 800

    base_cost_per_km = 10500
    if any(x in t for x in ["рефриж", "реф", "ref"]):
        base_cost_per_km = 13000
    if any(x in t for x in ["негабарит", "тяжеловес", "трал"]):
        base_cost_per_km = 22000
    if any(x in t for x in ["xalqaro", "международ"]):
        base_cost_per_km = 16000

    vehicle_count = vehicle_need["estimated_vehicle_count"]
    estimated_cost = distance_km * base_cost_per_km * 1.25 * 1.18 * vehicle_count

    if amount > 0:
        margin = amount - estimated_cost
        margin_percent = round(margin / amount * 100, 1)
    else:
        margin = 0
        margin_percent = 0

    if amount <= 0:
        profitability = "Нет суммы для оценки"
    elif margin_percent >= 25:
        profitability = "Высокая потенциальная маржа"
    elif margin_percent >= 10:
        profitability = "Средняя потенциальная маржа"
    elif margin_percent >= 0:
        profitability = "Низкая маржа, нужно торговаться"
    else:
        profitability = "Риск убытка, нужна ручная калькуляция"

    return {
        "currency": currency,
        "amount": amount,
        "distance_km": distance_km,
        "vehicle_count": vehicle_count,
        "vehicle_types": vehicle_need["vehicle_types"],
        "estimated_cost": round(estimated_cost, 0),
        "estimated_margin": round(margin, 0),
        "estimated_margin_percent": margin_percent,
        "profitability": profitability,
        "market_note": f"Оценка предварительная: дистанция ~{distance_km} км, машин: {vehicle_count}, тип: {', '.join(vehicle_need['vehicle_types'])}.",
        "delivery_days": delivery_days,
    }


def make_commercial_offer_draft(trade, title, analytics):
    customer = trade.get("customer_name") or "Заказчик"
    amount = trade.get("start_cost") or ""
    currency = trade.get("currency_codeabc") or trade.get("currency_name") or "UZS"
    route = analytics.get("route_text", "маршрут согласно ТЗ")
    transport = analytics.get("transport_text", "транспорт согласно ТЗ")
    logistics_type = analytics.get("logistics_type_text", "перевозка грузов")
    return clean_text(f"""
Коммерческое предложение

Компания Trans Ocean Logistics выражает готовность оказать услуги по тендеру: {title}.

Заказчик: {customer}
Тип услуги: {logistics_type}
Маршрут: {route}
Транспорт: {transport}
Ориентировочная сумма тендера: {amount} {currency}

Мы готовы обеспечить транспорт, организацию перевозки, контроль сроков, координацию водителей и сопровождение выполнения услуги согласно техническому заданию.

Окончательная цена и условия выполнения будут подтверждены после детального изучения технического задания, маршрута, графика погрузки/выгрузки и требований заказчика.

С уважением,
Trans Ocean Logistics
""")


def make_cover_letter_draft(trade, title, analytics):
    customer = trade.get("customer_name") or "Уважаемые партнёры"
    route = analytics.get("route_text", "согласно ТЗ")
    transport = analytics.get("transport_text", "согласно ТЗ")
    return clean_text(f"""
Уважаемые представители {customer}!

Компания Trans Ocean Logistics сообщает о заинтересованности в участии в тендере: {title}.

Мы готовы рассмотреть оказание транспортно-логистических услуг по маршруту: {route}.
Предварительно требуемый транспорт: {transport}.

Просим принять наши документы к рассмотрению. После изучения полного технического задания мы готовы предоставить окончательное коммерческое предложение и подтвердить условия выполнения перевозки.

С уважением,
Trans Ocean Logistics
""")


def build_tender_intelligence(trade, title, full_text, base_analysis):
    economics = estimate_transport_economics(
        trade,
        title,
        full_text,
        base_analysis.get("route_text", ""),
        base_analysis.get("transport_text", ""),
    )
    return {
        "economics": economics,
        "commercial_offer_draft": make_commercial_offer_draft(trade, title, base_analysis),
        "cover_letter_draft": make_cover_letter_draft(trade, title, base_analysis),
        "decision_support": {
            "recommended_next_step": "Рассчитать точную ставку и проверить ТЗ",
            "price_comment": economics["profitability"],
            "market_note": economics["market_note"],
        },
    }


def market_analytics_from_sheet(limit=500):
    try:
        sheet = get_sheet()
        rows = sheet.get_all_values()
        if len(rows) <= 1:
            return {"status": "warning", "message": "No data in sheet", "total_rows": 0}

        headers = rows[0]
        idx = {h.strip(): i for i, h in enumerate(headers)}

        def get(row, col):
            i = idx.get(col)
            if i is None or i >= len(row):
                return ""
            return row[i]

        data_rows = rows[1:][-limit:]
        source_counts, customer_counts, route_counts, transport_counts, priority_counts = {}, {}, {}, {}, {}
        amounts = []

        for row in data_rows:
            source = get(row, "Источник") or "Не указано"
            customer = get(row, "Заказчик") or "Не указано"
            route = get(row, "Маршрут") or "Не указано"
            transport = get(row, "Тип транспорта") or "Не указано"
            priority = get(row, "Приоритет") or get(row, "Приоритет ") or "Не указано"
            amount = extract_money_number(get(row, "Сумма"))

            source_counts[source] = source_counts.get(source, 0) + 1
            customer_counts[customer] = customer_counts.get(customer, 0) + 1
            route_counts[route] = route_counts.get(route, 0) + 1
            transport_counts[transport] = transport_counts.get(transport, 0) + 1
            priority_counts[priority] = priority_counts.get(priority, 0) + 1
            if amount > 0:
                amounts.append(amount)

        def top_items(d, n=10):
            return sorted([{"name": k, "count": v} for k, v in d.items()], key=lambda x: x["count"], reverse=True)[:n]

        return {
            "status": "ok",
            "version": "market_analytics_v27",
            "total_rows": len(data_rows),
            "sources": top_items(source_counts),
            "top_customers": top_items(customer_counts),
            "top_routes": top_items(route_counts),
            "top_transport": top_items(transport_counts),
            "priority_distribution": top_items(priority_counts),
            "amount_stats": {
                "count_with_amount": len(amounts),
                "average_amount": round(sum(amounts) / len(amounts), 0) if amounts else 0,
                "max_amount": round(max(amounts), 0) if amounts else 0,
            },
        }
    except Exception as e:
        return {"status": "error", "version": "market_analytics_v27", "error": str(e)}


def smart_api_analysis_from_trade(trade, title=""):
    products = extract_budget_products(trade)
    product_name = description = category_name = delivery_term = ""
    if products:
        first = products[0]
        product_name = clean_text(first.get("Product_Name", ""))
        description = clean_text(first.get("Description", ""))
        category_name = clean_text(first.get("Category_Name", ""))
        delivery_term = first.get("Delivery_Term", "") or ""
    q_texts = []
    for q in trade.get("js_qualification_fields") or []:
        if isinstance(q, dict):
            for key in ["name", "title", "description", "label"]:
                if q.get(key):
                    q_texts.append(str(q.get(key)))
    raw_text = " ".join([title or "", str(trade.get("customer_name") or ""), product_name, description, category_name, str(trade.get("technical_description") or ""), " ".join(q_texts)])
    text = normalize_text(raw_text)
    smart_reject = smart_filter_rejection_reason(text)
    transport_intent = has_transport_intent(text)

    if smart_reject:
        docs = detect_required_documents_from_text(title, raw_text, trade)
        readiness = compare_with_company_kb(docs["required_documents"])
        route_transport = extract_route_and_transport(title, raw_text)
        types = ["Непрофильный лот: ремонт/обслуживание/запчасти"]
        tender_intelligence = {
            "economics": {
                "currency": trade.get("currency_codeabc") or trade.get("currency_name") or "UZS",
                "amount": 0,
                "distance_km": 0,
                "vehicle_count": 0,
                "vehicle_types": [],
                "estimated_cost": 0,
                "estimated_margin": 0,
                "estimated_margin_percent": 0,
                "profitability": "Не считать: непрофильный лот",
                "market_note": "V26 Smart Filter отклонил лот: ремонт/обслуживание/запчасти/покупка транспорта.",
                "delivery_days": "",
            },
            "commercial_offer_draft": "",
            "cover_letter_draft": "",
            "decision_support": {
                "recommended_next_step": "Не участвовать",
                "price_comment": "Непрофильный лот",
                "market_note": smart_reject,
            },
        }
        return {
            "score": 0,
            "priority": "Непрофильный",
            "win_chance": "Низкий",
            "risk": "Высокий",
            "logistics": "Нет",
            "logistics_types": types,
            "logistics_type_text": ", ".join(types),
            "decision": "Не участвовать: это ремонт/техническое обслуживание/запчасти/покупка транспорта, а не перевозка груза.",
            "reasons": [],
            "risks": [smart_reject],
            "reason_text": "Лот отклонён V26 Smart Filter",
            "risk_text": smart_reject,
            "document_note": "V26 Smart Filter: непрофильный лот для Trans Ocean Logistics.",
            "requirements_short": short_requirements_from_trade(trade),
            "api_text_used": clean_text(" | ".join([product_name, description, category_name, " ".join(q_texts[:5])]))[:1200],
            "required_documents": docs["required_documents"],
            "document_warnings": docs["warnings"],
            "responsible_tasks": docs["responsible_tasks"],
            "documents_summary": docs["summary"],
            "documents_count": docs["documents_count"],
            "company_readiness": readiness,
            "tender_intelligence": tender_intelligence,
            "route_text": route_transport["route_text"],
            "transport_text": route_transport["transport_text"],
        }

    amount = 0.0
    try:
        amount = float(trade.get("start_cost") or 0)
    except Exception:
        pass
    payment_type = clean_text(trade.get("payment_type_name", ""))
    score, reasons, risks = 35, [], []

    def add(points, reason):
        nonlocal score
        score += points
        reasons.append(reason)

    def sub(points, reason):
        nonlocal score
        score -= points
        risks.append(reason)

    if transport_intent:
        add(30, "лот связан с перевозкой/транспортной услугой")
    if any(w in text for w in ["перевозка груза", "перевозка грузов", "перевозке грузов", "yuk tashish", "yuklarni tashish"]):
        add(25, "перевозка грузов")
    if any(w in text for w in ["transport xizmati", "transport xizmatlari", "автотранспорт", "avtransport"]):
        add(12, "транспортная услуга")
    if any(w in text for w in ["fura", "фура", "22 тонн", "22 ton", "20 тонн", "tent", "тент", "ref", "реф"]):
        add(18, "указан подходящий тип транспорта/тоннаж")
    if any(w in text for w in ["маршрут", "yo'nalishi", "йуналиши", "yo‘nalishi"]):
        add(10, "указан маршрут")
    if any(w in text for w in ["xalqaro", "международ"]):
        add(18, "международная перевозка")
    if any(w in text for w in ["logistika", "логист"]):
        add(12, "логистическая услуга")
    if any(w in text for w in ["экспедитор", "ekspeditor"]):
        add(12, "экспедиторская услуга")
    if any(w in text for w in ["ijara", "ijarasi", "аренда транспорта", "аренда транспортных средств"]):
        add(10, "аренда транспорта может быть профильной")
    if any(w in text for w in ["qurilish", "строитель", "монтаж", "дорожн", "yo'l", "йул"]):
        add(6, "строительная/дорожная логистика")
    if any(w in normalize_text(payment_type) for w in ["олдиндан", "предоплат", "аванс"]):
        add(8, "есть предоплата")
    if amount >= 1_000_000_000:
        add(16, "крупная сумма больше 1 млрд UZS")
    elif amount >= 500_000_000:
        add(12, "сумма больше 500 млн UZS")
    elif amount >= 100_000_000:
        add(8, "сумма от 100 млн UZS")
    elif amount and amount < 50_000_000:
        sub(5, "небольшая сумма")
    if not transport_intent and any(w in text for w in HARD_BAD_WORDS):
        sub(30, "похоже на непрофильную закупку")
    if transport_intent and score < 60:
        score = 60
        reasons.append("V23 правило: любая реальная перевозка минимум средний интерес")
    score = max(0, min(100, score))
    if score >= 80:
        priority, win_chance, decision = "Высокий", "Высокий", "Участвовать: профильный тендер. Проверить ТЗ, маршрут, транспорт и рассчитать ставку."
    elif score >= 60:
        priority, win_chance, decision = "Средний", "Средний", "Изучить условия участия: лот связан с перевозкой, нужно рассчитать цену и проверить документы."
    elif score >= 40:
        priority, win_chance, decision = "Низкий", "Средний/Низкий", "Изучить вручную: есть признаки логистики, но мало данных."
    else:
        priority, win_chance, decision = "Непрофильный", "Низкий", "Скорее не участвовать: нет достаточных признаков перевозки."
    risk = "Средний" if risks else ("Низкий/Средний" if score >= 80 else "Средний")
    logistics = "Да" if transport_intent or score >= 55 else "Сомнительно" if score >= 40 else "Нет"
    docs = detect_required_documents_from_text(title, raw_text, trade)
    readiness = compare_with_company_kb(docs["required_documents"])
    route_transport = extract_route_and_transport(title, raw_text)
    types = classify_logistics_type(raw_text)
    base_analysis_for_intelligence = {
        "route_text": route_transport["route_text"],
        "transport_text": route_transport["transport_text"],
        "logistics_type_text": ", ".join(types),
    }
    tender_intelligence = build_tender_intelligence(trade, title, raw_text, base_analysis_for_intelligence)

    return {
        "score": score, "priority": priority, "win_chance": win_chance, "risk": risk, "logistics": logistics,
        "logistics_types": types, "logistics_type_text": ", ".join(types), "decision": decision,
        "reasons": reasons, "risks": risks, "reason_text": "; ".join(reasons[:7]) if reasons else "нет сильных положительных факторов",
        "risk_text": "; ".join(risks[:6]) if risks else "критических рисков по API не найдено",
        "document_note": "V26: агент отсекает ремонт/обслуживание/запчасти и анализирует только реальные перевозки.",
        "requirements_short": short_requirements_from_trade(trade), "api_text_used": clean_text(" | ".join([product_name, description, category_name, " ".join(q_texts[:5])]))[:1200],
        "required_documents": docs["required_documents"], "document_warnings": docs["warnings"], "responsible_tasks": docs["responsible_tasks"],
        "documents_summary": docs["summary"], "documents_count": docs["documents_count"], "company_readiness": readiness,
        "tender_intelligence": tender_intelligence,
        "route_text": route_transport["route_text"], "transport_text": route_transport["transport_text"],
    }


def smart_api_analysis_for_url(site, title, url):
    if site != "UZEX":
        docs = detect_required_documents_from_text(title, "", None)
        return {"score": 60 if has_transport_intent(title) else 40, "priority": "Средний" if has_transport_intent(title) else "Низкий", "win_chance": "Требуется проверка", "risk": "Средний", "logistics": "Да" if has_transport_intent(title) else "Сомнительно", "logistics_types": classify_logistics_type(title), "logistics_type_text": ", ".join(classify_logistics_type(title)), "decision": "Проверить вручную: для этого источника нет глубокого UZEX API-анализа.", "reason_text": "источник не UZEX", "risk_text": "нет API-деталей", "document_note": "Документы нужно открыть вручную.", "requirements_short": "", "required_documents": docs["required_documents"], "document_warnings": docs["warnings"], "responsible_tasks": docs["responsible_tasks"], "documents_summary": docs["summary"], "documents_count": docs["documents_count"], "company_readiness": compare_with_company_kb(docs["required_documents"]), "route_text": "Маршрут не найден", "transport_text": "Тип транспорта не найден"}
    lot_id = extract_lot_id_from_url(url)
    if not lot_id:
        docs = detect_required_documents_from_text(title, "", None)
        return {"score": 0, "priority": "Непрофильный", "win_chance": "Низкий", "risk": "Высокий", "logistics": "Нет", "logistics_types": ["Не определено"], "logistics_type_text": "Не определено", "decision": "Не удалось определить ID лота.", "reason_text": "нет lot_id", "risk_text": "невозможно получить API-данные", "document_note": "", "requirements_short": "", "required_documents": docs["required_documents"], "document_warnings": ["Не удалось определить ID лота"], "responsible_tasks": docs["responsible_tasks"], "documents_summary": docs["summary"], "documents_count": docs["documents_count"], "company_readiness": compare_with_company_kb(docs["required_documents"]), "route_text": "Маршрут не найден", "transport_text": "Тип транспорта не найден"}
    return smart_api_analysis_from_trade(get_uzex_lot_data(lot_id), title)


def ensure_sheet_columns():
    sheet = get_sheet()
    headers = sheet.row_values(1)
    if not headers:
        headers = ["Дата", "Отправил", "Ссылка", "Источник", "Статус", "Приоритет", "AI анализ", "Комментарий"]
        sheet.update("A1:H1", [headers])
    current = sheet.row_values(1)
    added = []
    for col in EXTRA_SHEET_COLUMNS:
        if col not in current:
            current.append(col)
            added.append(col)
    if added:
        end_a1 = gspread.utils.rowcol_to_a1(1, len(current))
        end_col = ''.join([c for c in end_a1 if c.isalpha()])
        sheet.update(f"A1:{end_col}1", [current])
    return {"headers_total": len(current), "added": added, "headers": current}


def ensure_tender_manager_columns():
    sheet = get_sheet()
    if not sheet.row_values(1):
        ensure_sheet_columns()
    current = sheet.row_values(1)
    added = []
    for col in TENDER_MANAGER_COLUMNS:
        if col not in current:
            current.append(col)
            added.append(col)
    if added:
        end_a1 = gspread.utils.rowcol_to_a1(1, len(current))
        end_col = ''.join([c for c in end_a1 if c.isalpha()])
        sheet.update(f"A1:{end_col}1", [current])
    return {"headers_total": len(current), "added": added, "headers": current}


def get_header_index_map(headers):
    return {h.strip(): i for i, h in enumerate(headers, start=1) if h}


def update_row_analytics(sheet, row_number, headers_map, analytics):
    cells = []
    for col, value in analytics.items():
        idx = headers_map.get(col)
        if idx:
            cells.append(gspread.Cell(row_number, idx, value))
    if cells:
        sheet.update_cells(cells, value_input_option="USER_ENTERED")
    return len(cells)


def analyze_uzex_for_sheet(site, title, url):
    empty = {"Логистика": "", "Приоритет": "", "Приоритет ": "", "Риск": "", "Риск ": "", "Шанс победы": "", "Заказчик": "", "Сумма": "", "Валюта": "", "Оплата": "", "Срок оплаты": "", "Срок оказания услуг": "", "Тип логистики": "", "Маршрут": "", "Тип транспорта": "", "Требования": "", "Документы нужны": "", "Готовность компании": "", "Не хватает документов": "", "Проверить/подготовить": "", "Предупреждения по документам": "", "Ответственные задачи": "", "Рекомендация AI": ""}
    try:
        smart = smart_api_analysis_for_url(site, title, url)
        trade = None
        delivery_term = ""
        if site == "UZEX" and extract_lot_id_from_url(url):
            trade = get_uzex_lot_data(extract_lot_id_from_url(url))
            products = extract_budget_products(trade)
            if products:
                delivery_term = products[0].get("Delivery_Term", "") or ""
        readiness = smart.get("company_readiness", {})
        tender_intelligence = smart.get("tender_intelligence", {})
        economics = tender_intelligence.get("economics", {})
        docs_text = "; ".join([d.get("document", "") for d in smart.get("required_documents", [])[:25]])
        missing_text = "; ".join([d.get("document", "") for d in readiness.get("missing_documents", [])[:15]])
        check_text = "; ".join([d.get("document", "") for d in readiness.get("to_check_documents", [])[:15]])
        responsible = "; ".join([f"{x.get('role')}: {x.get('task')}" for x in smart.get("responsible_tasks", [])])
        empty.update({
            "Логистика": smart.get("logistics", ""), "Приоритет": smart.get("priority", ""), "Приоритет ": smart.get("priority", ""),
            "Риск": smart.get("risk", ""), "Риск ": smart.get("risk", ""), "Шанс победы": smart.get("win_chance", ""),
            "Заказчик": (trade.get("customer_name", "") if trade else ""), "Сумма": (trade.get("start_cost", "") if trade else ""),
            "Валюта": (trade.get("currency_codeabc", "") or trade.get("currency_name", "") if trade else ""),
            "Оплата": (trade.get("payment_type_name", "") if trade else ""), "Срок оплаты": (trade.get("term_payment_days", "") if trade else ""),
            "Срок оказания услуг": delivery_term, "Тип логистики": smart.get("logistics_type_text", ""),
            "Маршрут": smart.get("route_text", ""), "Тип транспорта": smart.get("transport_text", ""),
            "Требования": short_requirements_from_trade(trade) if trade else smart.get("requirements_short", ""),
            "Документы нужны": docs_text, "Готовность компании": readiness.get("summary", ""), "Не хватает документов": missing_text,
            "Проверить/подготовить": check_text, "Предупреждения по документам": "; ".join(smart.get("document_warnings", [])[:10]),
            "Ответственные задачи": responsible, "Рекомендация AI": smart.get("decision", ""),
            "Оценка рынка": economics.get("profitability", ""),
            "Ориентир себестоимости": economics.get("estimated_cost", ""),
            "Потенциальная маржа": f"{economics.get('estimated_margin', '')} / {economics.get('estimated_margin_percent', '')}%",
            "Черновик КП": tender_intelligence.get("commercial_offer_draft", "")[:1000],
            "Черновик письма": tender_intelligence.get("cover_letter_draft", "")[:1000],
        })
        return empty
    except Exception as e:
        empty["Рекомендация AI"] = "Ошибка анализа: " + str(e)[:180]
        return empty


def make_row_by_headers(headers, base_values, analytics):
    base_map = {"Дата": base_values.get("Дата", ""), "Отправил": base_values.get("Отправил", ""), "Ссылка": base_values.get("Ссылка", ""), "Источник": base_values.get("Источник", ""), "Статус": base_values.get("Статус", ""), "Приоритет": base_values.get("Приоритет", ""), "Приоритет ": base_values.get("Приоритет", ""), "AI анализ": base_values.get("AI анализ", ""), "Комментарий": base_values.get("Комментарий", "")}
    return [base_map.get(h, analytics.get(h.strip(), "")) for h in headers]


def tender_exists(url):
    try:
        sheet = get_sheet()
        return url in sheet.col_values(3) or url in sheet.col_values(4)
    except Exception as e:
        print("CHECK ERROR:", e)
        return False


def save_to_sheet(site, title, url):
    try:
        if tender_exists(url):
            return False
        ensure_sheet_columns()
        ensure_tender_manager_columns()
        sheet = get_sheet()
        headers = sheet.row_values(1)
        analytics = analyze_uzex_for_sheet(site, title, url)
        base_values = {"Дата": datetime.now().strftime("%d.%m.%Y %H:%M"), "Отправил": "AI Agent", "Ссылка": url, "Источник": site, "Статус": "Новый", "Приоритет": analytics.get("Приоритет", "Средний"), "AI анализ": "Cargo V26: Smart Filter + Tender Intelligence", "Комментарий": title}
        sheet.append_row(make_row_by_headers(headers, base_values, analytics))
        return True
    except Exception as e:
        print("GOOGLE SHEETS ERROR:", e)
        return False


def format_document_checklist_for_telegram(smart):
    text = ""
    docs = smart.get("required_documents", [])
    readiness = smart.get("company_readiness", {})
    if docs:
        text += "\n📋 Документы для подготовки:\n"
        for i, doc in enumerate(docs[:10], 1):
            text += f"{i}. {doc.get('document')}\n"
    if readiness:
        text += "\n🏢 Готовность Trans Ocean Logistics:\n" + readiness.get("summary", "") + "\n"
        missing = readiness.get("missing_documents", [])
        checks = readiness.get("to_check_documents", [])
        if missing:
            text += "\n❌ Не хватает:\n"
            for d in missing[:5]:
                text += f"- {d.get('document')} ({d.get('owner')})\n"
        if checks:
            text += "\n🔎 Проверить/подготовить:\n"
            for d in checks[:5]:
                text += f"- {d.get('document')} ({d.get('owner')})\n"
    warnings = smart.get("document_warnings", [])
    if warnings:
        text += "\n⚠️ Важные предупреждения:\n"
        for w in warnings[:5]:
            text += f"- {w}\n"
    responsible = smart.get("responsible_tasks", [])
    if responsible:
        text += "\n👥 Кому что сделать:\n"
        for r in responsible[:4]:
            text += f"- {r.get('role')}: {r.get('task')}\n"
    return text


def format_tender_message(tender):
    site, title, url = tender.get("site", ""), tender.get("title", ""), tender.get("url", "")
    analytics = analyze_uzex_for_sheet(site, title, url)
    smart = smart_api_analysis_for_url(site, title, url)
    delivery = analytics.get("Срок оказания услуг") or "-"
    if str(delivery).isdigit():
        delivery = str(delivery) + " дней"
    message = (
        f"🚚 Новый логистический тендер\n\n"
        f"📌 Источник: {site}\n🧠 AI Score: {smart.get('score')}/100\n🎯 Приоритет: {smart.get('priority')}\n"
        f"📂 Тип логистики: {smart.get('logistics_type_text')}\n\n"
        f"📋 {title}\n\n"
        f"🏢 Заказчик: {analytics.get('Заказчик') or '-'}\n💰 Сумма: {analytics.get('Сумма') or '-'}\n"
        f"💵 Валюта: {analytics.get('Валюта') or '-'}\n💳 Оплата: {analytics.get('Оплата') or '-'}\n"
        f"⏳ Срок оплаты: {analytics.get('Срок оплаты') or '-'} дней\n📅 Срок оказания: {delivery}\n\n"
        f"🗺 Маршрут: {smart.get('route_text')}\n🚛 Транспорт: {smart.get('transport_text')}\n\n"
        f"📈 Шанс победы: {smart.get('win_chance')}\n⚠️ Риск: {smart.get('risk')}\n\n"
        f"✅ Почему интересно:\n{smart.get('reason_text')}\n\n"
        f"⚠️ Что проверить:\n{smart.get('risk_text')}\n\n"
        f"🤖 AI рекомендация:\n{smart.get('decision')}\n"
    )
    if smart.get("requirements_short"):
        message += f"\n📄 Требования/API кратко:\n{smart.get('requirements_short')[:500]}\n"
    message += format_document_checklist_for_telegram(smart)
    message += f"\n📎 Документы: {smart.get('document_note')}\n\n🔗 {url}"
    return message[:3900]


@app.get("/")
def home():
    return {"status": "AI Tender Agent Cargo V26 Smart Filter is running"}


@app.head("/")
def head_home():
    return {}


@app.get("/version")
def version():
    return {"version": VERSION, "status": "running"}


@app.get("/health")
def health():
    result = {"status": "ok", "telegram": False, "google_sheets": False, "errors": []}
    try:
        tg = requests.get(f"https://api.telegram.org/bot{BOT_TOKEN}/getMe", timeout=8)
        result["telegram"] = tg.status_code == 200
    except Exception as e:
        result["errors"].append("Telegram error: " + str(e))
    try:
        get_sheet().row_values(1)
        result["google_sheets"] = True
    except Exception as e:
        result["errors"].append("Google Sheets error: " + str(e))
    if result["errors"]:
        result["status"] = "warning"
    return result


@app.get("/test_filter")
def test_filter():
    samples = {
        "Перевозка груза Ташкент Самарканд 22 тонны фура тент": "https://etender.uzex.uz/lot/487462",
        "Oqdaryo qurilish montaj ishlarida yuklarni tashish xizmati": "https://etender.uzex.uz/lot/496034",
        "Аренда транспортных средств для перевозки грузов": "https://etender.uzex.uz/lot/999",
        "Лабораторное оборудование": "https://www.tenderweek.com/tender-35921",
        "Автоматизация кредитного конвейера": "https://xt-xarid.uz/tender/111",
        "Texnik xizmat ko'rsatish yuk mashinalariga": "https://etender.uzex.uz/lot/497043",
        "Техническое обслуживание грузовых автомобилей": "https://etender.uzex.uz/lot/497043",
        "Ремонт грузовых автомобилей": "https://etender.uzex.uz/lot/497044",
        "Закупка запчастей для грузовых автомобилей": "https://etender.uzex.uz/lot/497045",
    }
    return {k: {"accepted": is_real_cargo_tender(k, v), "reason": filter_reason(k, v)} for k, v in samples.items()}


@app.get("/setup_sheet_columns")
def setup_sheet_columns():
    try:
        return {"status": "ok", "version": "sheet_setup_v27", "analytics": ensure_sheet_columns(), "manager": ensure_tender_manager_columns()}
    except Exception as e:
        return {"status": "error", "version": "sheet_setup_v27", "error": str(e)}


@app.get("/tender_manager_status")
def tender_manager_status():
    try:
        headers = get_sheet().row_values(1)
        missing = [c for c in EXTRA_SHEET_COLUMNS + TENDER_MANAGER_COLUMNS if c not in headers]
        return {"status": "ok" if not missing else "warning", "version": "tender_manager_v27", "headers_total": len(headers), "missing_columns": missing}
    except Exception as e:
        return {"status": "error", "version": "tender_manager_v27", "error": str(e)}


@app.get("/company_kb")
def company_kb():
    return {"status": "ok", "version": "company_kb_v27", "company": "Trans Ocean Logistics", "documents_total": len(COMPANY_KNOWLEDGE_BASE), "documents": COMPANY_KNOWLEDGE_BASE}


@app.get("/document_checklist")
def document_checklist(lot_id: str):
    try:
        trade = get_uzex_trade(lot_id)
        products = extract_budget_products(trade)
        title, parts = "", []
        if products:
            title = clean_text(products[0].get("Product_Name", ""))
            parts.extend([clean_text(products[0].get("Description", "")), clean_text(products[0].get("Category_Name", ""))])
        parts.extend([clean_text(trade.get("technical_description", "")), clean_text(trade.get("customer_name", ""))])
        checklist = detect_required_documents_from_text(title, " ".join(parts), trade)
        return {"status": "ok", "version": "document_checklist_v27", "lot_id": lot_id, "title": title, **checklist}
    except Exception as e:
        return {"status": "error", "version": "document_checklist_v27", "lot_id": lot_id, "error": str(e)}


@app.get("/company_readiness")
def company_readiness(lot_id: str):
    checklist = document_checklist(lot_id)
    if checklist.get("status") != "ok":
        return checklist
    readiness = compare_with_company_kb(checklist.get("required_documents", []))
    return {"status": "ok", "version": "company_readiness_v27", "lot_id": lot_id, "title": checklist.get("title"), "document_checklist": checklist, "company_readiness": readiness}


@app.get("/analyze_uzex_lot")
def analyze_uzex_lot(lot_id: str):
    try:
        trade = get_uzex_trade(lot_id)
        products = extract_budget_products(trade)
        title = ""
        if products:
            title = clean_text((products[0].get("Product_Name", "") or "") + " | " + (products[0].get("Description", "") or ""))
        smart = smart_api_analysis_from_trade(trade, title)
        return {
            "status": "ok", "version": "document_analyzer_v27", "lot_id": lot_id,
            "lot": {
                "id": trade.get("id"), "display_no": trade.get("display_no"), "customer_name": trade.get("customer_name"),
                "start_date": trade.get("start_date"), "end_date": trade.get("end_date"), "start_cost": trade.get("start_cost"),
                "currency": trade.get("currency_codeabc") or trade.get("currency_name"), "payment_type_name": trade.get("payment_type_name"),
                "term_payment_days": trade.get("term_payment_days"), "requirements_short": short_requirements_from_trade(trade),
            },
            "smart_api_analysis": smart,
            "document_checklist": {"required_documents": smart["required_documents"], "warnings": smart["document_warnings"], "documents_count": smart["documents_count"]},
            "company_readiness": smart["company_readiness"],
            "analysis": {
                "priority": smart["priority"], "win_chance": smart["win_chance"], "risk": smart["risk"],
                "logistics": smart["logistics"], "logistics_type": smart["logistics_type_text"], "decision": smart["decision"],
                "route": smart["route_text"], "transport": smart["transport_text"],
                "summary": f"AI Score: {smart['score']}/100. Тип: {smart['logistics_type_text']}. Готовность компании: {smart['company_readiness']['readiness_score']}%. Рекомендация: {smart['decision']}",
            },
        }
    except Exception as e:
        return {"status": "error", "version": "document_analyzer_v27", "lot_id": lot_id, "error": str(e)}


@app.get("/debug_sources")
def debug_sources():
    result = {"version": VERSION, "Tenderweek": 0, "UZEX": 0, "XT-Xarid": 0, "total": 0, "errors": []}
    try:
        result["Tenderweek"] = len(parse_tenderweek())
    except Exception as e:
        result["errors"].append("Tenderweek error: " + str(e))
    try:
        result["UZEX"] = len(parse_uzex())
    except Exception as e:
        result["errors"].append("UZEX error: " + str(e))
    try:
        result["XT-Xarid"] = len(parse_xt_xarid())
    except Exception as e:
        result["errors"].append("XT-Xarid error: " + str(e))
    result["total"] = result["Tenderweek"] + result["UZEX"] + result["XT-Xarid"]
    return result


@app.get("/debug_items")
def debug_items():
    all_items = []
    for source, parser in [("Tenderweek", parse_tenderweek), ("UZEX", parse_uzex), ("XT-Xarid", parse_xt_xarid)]:
        try:
            for item in parser()[:15]:
                all_items.append({"site": source, "title": item.get("title"), "url": item.get("url"), "reason": item.get("reason")})
        except Exception as e:
            all_items.append({"site": source, "error": str(e)})
    return {"version": VERSION, "count": len(all_items), "items": all_items[:40]}


@app.get("/debug_raw_candidates")
def debug_raw_candidates():
    all_items = []
    for source, parser in [("Tenderweek", parse_tenderweek), ("UZEX", parse_uzex), ("XT-Xarid", parse_xt_xarid)]:
        try:
            all_items.extend(parser(raw=True)[:25])
        except Exception as e:
            all_items.append({"site": source, "error": str(e)})
    return {"version": VERSION, "total_candidates_sample": len(all_items), "accepted_sample": len([x for x in all_items if x.get("accepted") is True]), "rejected_sample": len([x for x in all_items if x.get("accepted") is False]), "items": all_items[:75]}




@app.get("/tender_intelligence")
def tender_intelligence(lot_id: str):
    try:
        trade = get_uzex_trade(lot_id)
        budget_products = extract_budget_products(trade)
        title = ""
        description = ""
        category_name = ""

        if budget_products:
            first = budget_products[0]
            title = clean_text(first.get("Product_Name", ""))
            description = clean_text(first.get("Description", ""))
            category_name = clean_text(first.get("Category_Name", ""))

        smart = smart_api_analysis_from_trade(trade, " | ".join([title, description]))

        return {
            "status": "ok",
            "version": "tender_intelligence_v27",
            "lot_id": lot_id,
            "title": title,
            "description": description,
            "category_name": category_name,
            "ai_score": smart.get("score"),
            "priority": smart.get("priority"),
            "logistics": smart.get("logistics"),
            "logistics_type": smart.get("logistics_type_text"),
            "route": smart.get("route_text"),
            "transport": smart.get("transport_text"),
            "decision": smart.get("decision"),
            "tender_intelligence": smart.get("tender_intelligence"),
        }
    except Exception as e:
        return {"status": "error", "version": "tender_intelligence_v27", "lot_id": lot_id, "error": str(e)}


@app.get("/drafts")
def drafts(lot_id: str):
    try:
        trade = get_uzex_trade(lot_id)
        budget_products = extract_budget_products(trade)
        title = ""
        description = ""

        if budget_products:
            first = budget_products[0]
            title = clean_text(first.get("Product_Name", ""))
            description = clean_text(first.get("Description", ""))

        smart = smart_api_analysis_from_trade(trade, " | ".join([title, description]))
        ti = smart.get("tender_intelligence", {})

        return {
            "status": "ok",
            "version": "draft_assistant_v26_v27",
            "lot_id": lot_id,
            "title": title,
            "commercial_offer_draft": ti.get("commercial_offer_draft", ""),
            "cover_letter_draft": ti.get("cover_letter_draft", ""),
            "documents": smart.get("required_documents", []),
            "responsible_tasks": smart.get("responsible_tasks", []),
        }
    except Exception as e:
        return {"status": "error", "version": "draft_assistant_v26_v27", "lot_id": lot_id, "error": str(e)}


@app.get("/market_analytics")
def market_analytics(limit: int = 500):
    return market_analytics_from_sheet(limit=limit)


@app.get("/backfill_existing_tenders")
def backfill_existing_tenders(limit: int = 50):
    try:
        ensure_sheet_columns(); ensure_tender_manager_columns()
        sheet = get_sheet()
        values = sheet.get_all_values()
        if not values:
            return {"status": "warning", "version": "backfill_v27", "message": "Sheet is empty"}
        headers, headers_map = values[0], get_header_index_map(values[0])
        if "Ссылка" not in headers_map:
            return {"status": "error", "version": "backfill_v27", "error": "Required column not found: Ссылка", "headers": headers}
        link_col = headers_map["Ссылка"]
        processed = updated = skipped = 0
        errors = []
        for row_idx, row in enumerate(values[1:], start=2):
            if processed >= limit:
                break
            link = row[link_col - 1] if len(row) >= link_col else ""
            if not link or "etender.uzex.uz/lot/" not in link:
                skipped += 1
                continue
            processed += 1
            try:
                analytics = analyze_uzex_for_sheet("UZEX", "Backfill UZEX lot", link)
                update_row_analytics(sheet, row_idx, headers_map, analytics)
                updated += 1
            except Exception as e:
                errors.append({"row": row_idx, "link": link, "error": str(e)[:250]})
        return {"status": "ok", "version": "backfill_v27", "processed": processed, "updated": updated, "skipped": skipped, "errors_count": len(errors), "errors": errors[:10]}
    except Exception as e:
        return {"status": "error", "version": "backfill_v27", "error": str(e)}


@app.get("/quality_backfill_existing_tenders")
def quality_backfill_existing_tenders(limit: int = 100):
    return backfill_existing_tenders(limit=limit)


@app.get("/scan")
def scan():
    print("SCAN STARTED V27")
    found_total = new_total = duplicate_total = 0
    all_tenders, seen_urls = [], set()
    source_counts = {}
    message = "📊 AI Tender Agent Cargo V27 Tender Intelligence Scan завершён\n\n"
    for source, parser in [("Tenderweek", parse_tenderweek), ("UZEX", parse_uzex), ("XT-Xarid", parse_xt_xarid)]:
        try:
            result = parser()
            source_counts[source] = len(result)
            all_tenders.extend(result)
            message += f"{source}: найдено транспортных лотов {len(result)}\n"
        except Exception as e:
            source_counts[source] = 0
            message += f"{source}: ERROR\n"
            print(source, "ERROR:", e)
    message += "\n"
    for tender in all_tenders:
        url, title = tender.get("url"), tender.get("title")
        if not url or url in seen_urls:
            continue
        seen_urls.add(url)
        if not is_real_cargo_tender(title, url):
            continue
        found_total += 1
        if save_to_sheet(tender["site"], title, url):
            new_total += 1
            send_telegram(format_tender_message(tender))
        else:
            duplicate_total += 1
    message += f"Всего найдено: {found_total}\nНовых сохранено: {new_total}\nДубликатов пропущено: {duplicate_total}\n\nV27: Tender Intelligence + подготовка черновиков + аналитика рынка."
    send_telegram(message)
    return {"status": "success", "version": VERSION, "sources": source_counts, "found_total": found_total, "new_total": new_total, "duplicates": duplicate_total}


@app.head("/scan")
def scan_head():
    return {}


@app.post("/webhook")
def webhook():
    return scan()


@app.get("/webhook")
def webhook_get():
    return scan()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", 10000)))
