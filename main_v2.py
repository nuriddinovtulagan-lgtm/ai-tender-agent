import os
import re
import json
import time
import threading
import traceback
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
import gspread
from fastapi import FastAPI, Query
from google.oauth2.service_account import Credentials


# ============================================================
# AI TENDER AGENT — CARGO V30 REAL API ENGINE
#
# Replace the full content of main_v2.py with this file.
#
# Render Start Command:
# uvicorn main_v2:app --host 0.0.0.0 --port $PORT
# ============================================================

APP_VERSION = "cargo_v30_real_api_engine"
app = FastAPI(title="AI Tender Agent", version=APP_VERSION)

# ---------------- Environment variables ----------------

BOT_TOKEN = os.getenv("BOT_TOKEN") or os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID") or os.getenv("TELEGRAM_CHAT_ID")

GOOGLE_SHEET_ID = os.getenv("GOOGLE_SHEET_ID")
GOOGLE_CREDS_JSON = (
    os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON")
    or os.getenv("GOOGLE_CREDS_JSON")
)
WORKSHEET_NAME = os.getenv("WORKSHEET_NAME", "Тендеры")

REQUEST_TIMEOUT = int(os.getenv("REQUEST_TIMEOUT", "30"))
TRADE_LIST_PAGE_SIZE = int(os.getenv("TRADE_LIST_PAGE_SIZE", "100"))
MAX_TRADE_LIST_ROWS = int(os.getenv("MAX_TRADE_LIST_ROWS", "1000"))
DETAIL_WORKERS = int(os.getenv("DETAIL_WORKERS", "8"))
MAX_DETAIL_CANDIDATES = int(os.getenv("MAX_DETAIL_CANDIDATES", "250"))
MAX_TOTAL_SECONDS = int(os.getenv("MAX_TOTAL_SECONDS", "240"))

# ---------------- UZEX Real API ----------------

UZEX_API_BASE = "https://apietender.uzex.uz"
UZEX_TRADE_LIST_URL = f"{UZEX_API_BASE}/api/common/TradeList"
UZEX_GET_TRADE_URL = f"{UZEX_API_BASE}/api/common/GetTrade"
UZEX_CATEGORIES_URL = "https://xarid-api-trade.uzex.uz/Lib/GetCategories"

UZEX_SITE_BASE = "https://etender.uzex.uz"
UZEX_SYSTEM_ID = int(os.getenv("UZEX_SYSTEM_ID", "0"))
UZEX_TYPE_ID = int(os.getenv("UZEX_TYPE_ID", "1"))

# Official transport/logistics categories confirmed from UZEX.
TRANSPORT_CATEGORY_IDS = {
    125374,  # Услуги сухопутного и трубопроводного транспорта
    125496,  # Услуги водного транспорта
    125577,  # Услуги воздушного и космического транспорта
    125609,  # Складирование и вспомогательные транспортные услуги
}

TRANSPORT_CATEGORY_NAMES = {
    125374: "Услуги сухопутного и трубопроводного транспорта",
    125496: "Услуги водного транспорта",
    125577: "Услуги воздушного и космического транспорта",
    125609: "Услуги по складированию и вспомогательные транспортные услуги",
}

# ---------------- Background state ----------------

SCAN_LOCK = threading.Lock()
SCAN_STATE = {
    "running": False,
    "started_at": None,
    "finished_at": None,
    "last_trigger": None,
    "last_result": None,
    "last_error": None,
}

DATA_LOCK = threading.Lock()
LAST_ITEMS = []
LAST_DIAGNOSTICS = {}


# ============================================================
# FILTERS
# ============================================================

TITLE_KEYWORDS = [
    # Russian
    "перевозка",
    "перевозки",
    "перевозке",
    "перевозку",
    "перевозчик",
    "грузоперевоз",
    "грузовой транспорт",
    "транспортные услуги",
    "транспортная услуга",
    "транспортно-экспедицион",
    "экспедитор",
    "экспедирование",
    "логистика",
    "логистические услуги",
    "доставка груз",
    "доставка товар",
    "доставка продукц",
    "международная доставка",
    "международные перевозки",
    "автомобильные перевозки",
    "железнодорожные перевозки",
    "контейнерные перевозки",
    "аренда транспорта",
    "аренда автотранспорта",
    "аренда спецтехники",
    "услуги спецтехники",
    "погрузочно-разгрузоч",
    "складские услуги",
    "хранение груза",
    "таможенное оформление",
    "таможенный брокер",
    "фура",
    "тягач",
    "полуприцеп",
    "рефрижератор",
    "контейнеровоз",
    "трал",
    # Uzbek latin
    "yuk tashish",
    "yuklarni tashish",
    "yuk tashuvchi",
    "transport xizmati",
    "transport xizmatlari",
    "logistika",
    "yetkazib berish",
    "ekspeditor",
    "ombor xizmati",
    "maxsus texnika",
    "maxsus transport",
    "fura",
    "tral",
    # Uzbek cyrillic
    "юк ташиш",
    "юкларни ташиш",
    "транспорт хизмати",
    "транспорт хизматлари",
    "логистика",
    "етказиб бериш",
    "экспедитор",
    # English
    "cargo transportation",
    "freight transportation",
    "freight forwarding",
    "transport services",
    "logistics services",
    "shipping services",
    "warehouse services",
    "customs clearance",
]

DETAIL_KEYWORDS = TITLE_KEYWORDS + [
    "услуга по перевозке грузов",
    "услуги по перевозке грузов",
    "услуги сухопутного транспорта",
    "услуги водного транспорта",
    "услуги воздушного транспорта",
    "вспомогательные транспортные услуги",
    "transportation",
    "freight",
    "cargo",
    "shipping",
    "forwarding",
    "warehouse",
]

REJECT_PHRASES = [
    "ремонт автомобиля",
    "ремонт автотранспорта",
    "ремонт транспортного средства",
    "техническое обслуживание автомобиля",
    "техническое обслуживание автотранспорта",
    "техобслуживание автомобиля",
    "запасные части",
    "запчасти",
    "поставка автомобиля",
    "покупка автомобиля",
    "приобретение автомобиля",
    "поставка автотранспорта",
    "автомобильные шины",
    "автошины",
    "моторное масло",
    "страхование автомобиля",
    "страхование транспортного средства",
]


# ============================================================
# GENERAL HELPERS
# ============================================================

def now_str() -> str:
    return datetime.now().strftime("%d.%m.%Y %H:%M:%S")


def normalize_text(value) -> str:
    if value is None:
        return ""
    text = str(value).replace("\xa0", " ").lower().strip()
    text = re.sub(r"\s+", " ", text)
    return text


def compact_text(value, limit=1000) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text[:limit]


def safe_json_loads(value, default):
    if value is None:
        return default
    if isinstance(value, (list, dict)):
        return value
    if not isinstance(value, str):
        return default
    value = value.strip()
    if not value:
        return default
    try:
        return json.loads(value)
    except Exception:
        return default


def format_money(value) -> str:
    try:
        number = float(value)
        return f"{number:,.0f}".replace(",", " ")
    except Exception:
        return str(value or "")


def contains_keyword(text: str, keywords) -> bool:
    normalized = normalize_text(text)
    return any(normalize_text(keyword) in normalized for keyword in keywords)


def reject_reason(text: str) -> str:
    normalized = normalize_text(text)
    for phrase in REJECT_PHRASES:
        if normalize_text(phrase) in normalized:
            return phrase
    return ""


def make_session() -> requests.Session:
    session = requests.Session()
    session.headers.update({
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/149.0.0.0 Safari/537.36"
        ),
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "ru-RU,ru;q=0.9,uz;q=0.8,en;q=0.7",
        "Origin": UZEX_SITE_BASE,
        "Referer": f"{UZEX_SITE_BASE}/",
        "Content-Type": "application/json",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
    })
    return session


def tender_key(item: dict) -> str:
    lot_id = item.get("id")
    if lot_id:
        return f"uzex:{lot_id}"

    display_no = normalize_text(item.get("display_no"))
    if display_no:
        return f"uzex_display:{display_no}"

    title = normalize_text(item.get("title"))
    url = normalize_text(item.get("url"))
    return f"{title}|{url}"


def send_telegram(text: str) -> bool:
    if not BOT_TOKEN or not CHAT_ID:
        print("TELEGRAM: BOT_TOKEN or CHAT_ID missing")
        return False

    try:
        response = requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            json={
                "chat_id": CHAT_ID,
                "text": text[:3900],
                "disable_web_page_preview": True,
            },
            timeout=20,
        )
        print("TELEGRAM:", response.status_code, response.text[:200])
        return response.status_code == 200
    except Exception as exc:
        print("TELEGRAM ERROR:", repr(exc))
        return False


# ============================================================
# GOOGLE SHEETS
# ============================================================

def get_sheet():
    if not GOOGLE_CREDS_JSON:
        raise RuntimeError(
            "GOOGLE_SERVICE_ACCOUNT_JSON / GOOGLE_CREDS_JSON is empty"
        )
    if not GOOGLE_SHEET_ID:
        raise RuntimeError("GOOGLE_SHEET_ID is empty")

    info = json.loads(GOOGLE_CREDS_JSON)
    credentials = Credentials.from_service_account_info(
        info,
        scopes=[
            "https://www.googleapis.com/auth/spreadsheets",
            "https://www.googleapis.com/auth/drive",
        ],
    )

    client = gspread.authorize(credentials)
    book = client.open_by_key(GOOGLE_SHEET_ID)

    try:
        return book.worksheet(WORKSHEET_NAME)
    except Exception:
        return book.sheet1


def find_column(header, names):
    normalized_names = {normalize_text(name) for name in names}
    for index, value in enumerate(header):
        if normalize_text(value) in normalized_names:
            return index
    return None


def get_existing_keys(sheet):
    rows = sheet.get_all_values()
    if not rows:
        return set()

    header = rows[0]

    id_idx = find_column(header, ["ID", "Lot ID", "UZEX ID"])
    display_idx = find_column(
        header,
        ["Номер тендера", "display_no", "Номер", "Номер лота"],
    )
    title_idx = find_column(
        header,
        ["Название", "title", "Тендер", "Наименование"],
    )
    url_idx = find_column(header, ["Ссылка", "url", "link"])

    # Compatibility with the old table:
    # Дата, Источник, Название, Ссылка, Статус, Причина
    if title_idx is None and len(header) >= 3:
        title_idx = 2
    if url_idx is None and len(header) >= 4:
        url_idx = 3

    keys = set()

    for row in rows[1:]:
        lot_id = row[id_idx] if id_idx is not None and len(row) > id_idx else ""
        display_no = (
            row[display_idx]
            if display_idx is not None and len(row) > display_idx
            else ""
        )
        title = (
            row[title_idx]
            if title_idx is not None and len(row) > title_idx
            else ""
        )
        url = (
            row[url_idx]
            if url_idx is not None and len(row) > url_idx
            else ""
        )

        if lot_id:
            keys.add(f"uzex:{normalize_text(lot_id)}")
        if display_no:
            keys.add(f"uzex_display:{normalize_text(display_no)}")

        match = re.search(r"/lot/(\d+)", url)
        if match:
            keys.add(f"uzex:{match.group(1)}")

        if title or url:
            keys.add(
                f"{normalize_text(title)}|{normalize_text(url)}"
            )

    return keys


def save_new_tenders(sheet, items):
    # Keeps the existing 6-column table format.
    rows = []

    for item in items:
        details = (
            f"UZEX ID: {item.get('id', '')}; "
            f"№: {item.get('display_no', '')}; "
            f"Заказчик: {item.get('customer_name', '')}; "
            f"Сумма: {format_money(item.get('cost'))} "
            f"{item.get('currency_code', '')}; "
            f"Окончание: {item.get('end_date', '')}; "
            f"Категории: {', '.join(item.get('category_names', []))}; "
            f"Оплата: {item.get('payment_type', '')}; "
            f"Документы: {', '.join(item.get('document_names', []))}"
        )

        rows.append([
            datetime.now().strftime("%d.%m.%Y %H:%M"),
            "UZEX",
            item.get("title", ""),
            item.get("url", ""),
            "Новый",
            details[:5000],
        ])

    if rows:
        sheet.append_rows(rows, value_input_option="USER_ENTERED")

    return len(rows)


# ============================================================
# UZEX API CLIENT
# ============================================================

def fetch_trade_list_page(session, start_row: int, end_row: int):
    payload = {
        "TypeId": UZEX_TYPE_ID,
        "From": start_row,
        "To": end_row,
        "System_Id": UZEX_SYSTEM_ID,
    }

    response = session.post(
        UZEX_TRADE_LIST_URL,
        json=payload,
        timeout=REQUEST_TIMEOUT,
    )

    response.raise_for_status()
    data = response.json()

    if not isinstance(data, list):
        raise RuntimeError(
            f"TradeList returned {type(data).__name__}, expected list"
        )

    return data, {
        "from": start_row,
        "to": end_row,
        "status": response.status_code,
        "size": len(response.content),
        "rows": len(data),
    }


def fetch_all_trade_list():
    session = make_session()
    all_rows = []
    logs = []

    start_row = 1
    total_count = None

    while start_row <= MAX_TRADE_LIST_ROWS:
        end_row = min(
            start_row + TRADE_LIST_PAGE_SIZE - 1,
            MAX_TRADE_LIST_ROWS,
        )

        rows, page_log = fetch_trade_list_page(
            session,
            start_row,
            end_row,
        )
        logs.append(page_log)

        if rows and total_count is None:
            try:
                total_count = int(rows[0].get("total_count") or 0)
            except Exception:
                total_count = 0

        all_rows.extend(rows)

        print(
            "UZEX TRADE LIST:",
            start_row,
            end_row,
            "rows:",
            len(rows),
            "total:",
            total_count,
        )

        if not rows:
            break

        if total_count and len(all_rows) >= total_count:
            break

        if len(rows) < TRADE_LIST_PAGE_SIZE:
            break

        start_row = end_row + 1

    # Deduplicate by lot ID.
    unique = {}
    for row in all_rows:
        lot_id = row.get("id")
        if lot_id:
            unique[str(lot_id)] = row

    return list(unique.values()), {
        "total_count_reported": total_count,
        "rows_received": len(all_rows),
        "unique_rows": len(unique),
        "pages": logs,
    }


def title_is_candidate(row: dict) -> bool:
    text = " ".join([
        str(row.get("name") or ""),
        str(row.get("category_name") or ""),
        str(row.get("seller_name") or ""),
    ])

    if reject_reason(text):
        return False

    return contains_keyword(text, TITLE_KEYWORDS)


def fetch_trade_detail(lot_id):
    session = make_session()
    url = f"{UZEX_GET_TRADE_URL}/{lot_id}/{UZEX_SYSTEM_ID}"

    try:
        response = session.get(url, timeout=REQUEST_TIMEOUT)

        if response.status_code != 200:
            return None, {
                "id": lot_id,
                "status": response.status_code,
                "size": len(response.content),
                "error": "non_200",
            }

        data = response.json()

        if not isinstance(data, dict):
            return None, {
                "id": lot_id,
                "status": response.status_code,
                "size": len(response.content),
                "error": f"unexpected_json:{type(data).__name__}",
            }

        return data, {
            "id": lot_id,
            "status": response.status_code,
            "size": len(response.content),
            "error": None,
        }

    except Exception as exc:
        return None, {
            "id": lot_id,
            "status": None,
            "size": 0,
            "error": f"{type(exc).__name__}: {exc}",
        }


# ============================================================
# UZEX DETAIL ANALYSIS
# ============================================================

def extract_budget_products(detail: dict):
    products = safe_json_loads(
        detail.get("budget_products"),
        [],
    )

    if not isinstance(products, list):
        return []

    return [
        product
        for product in products
        if isinstance(product, dict)
    ]


def extract_category_data(products):
    category_ids = set()
    category_names = set()

    for product in products:
        category_id = (
            product.get("Category_Id")
            or product.get("category_id")
        )
        category_name = (
            product.get("Category_Name")
            or product.get("category_name")
        )

        try:
            if category_id is not None:
                category_ids.add(int(category_id))
        except Exception:
            pass

        if category_name:
            category_names.add(str(category_name).strip())

    return category_ids, sorted(category_names)


def build_detail_search_text(list_row: dict, detail: dict, products):
    chunks = [
        list_row.get("name"),
        detail.get("description"),
        detail.get("addon_description"),
        detail.get("technical_description"),
        detail.get("type_name"),
        detail.get("customer_name"),
    ]

    for product in products:
        chunks.extend([
            product.get("Product_Name"),
            product.get("Category_Name"),
            product.get("Description"),
            json.dumps(
                product.get("Js_Properties"),
                ensure_ascii=False,
            ),
        ])

    return " ".join(str(chunk or "") for chunk in chunks)


def extract_documents(detail: dict):
    documents = []

    field_groups = [
        ("Технический файл", "tech_file"),
        ("Технический документ", "tech_doc_file"),
        ("Дополнительный файл", "add_file"),
        ("Проект договора", "contract_proform_file"),
        ("Договор", "contract_file"),
        ("Экспертиза", "expertise_file"),
    ]

    for label, prefix in field_groups:
        name = detail.get(f"{prefix}_name")
        path = detail.get(f"{prefix}_path")
        ext = detail.get(f"{prefix}_ext")

        if not path:
            continue

        path = str(path)
        if path.startswith("http://") or path.startswith("https://"):
            full_url = path
        else:
            full_url = f"{UZEX_API_BASE}/{path.lstrip('/')}"

        documents.append({
            "label": label,
            "name": name or f"{label}.{ext or ''}".rstrip("."),
            "url": full_url,
            "ext": ext,
        })

    return documents


def extract_routes(products):
    routes = []

    for product in products:
        description = compact_text(
            product.get("Description"),
            1000,
        )
        if description:
            routes.append(description)

    return routes[:30]


def extract_qualification_requirements(detail: dict):
    fields = detail.get("js_qualification_fields")

    if not isinstance(fields, list):
        fields = safe_json_loads(
            detail.get("qualification_fields"),
            [],
        )

    output = []

    if isinstance(fields, list):
        for field in fields:
            if not isinstance(field, dict):
                continue
            name = field.get("name") or field.get("Name")
            if name:
                output.append(str(name).strip())

    return output[:30]


def analyze_trade(list_row: dict, detail: dict):
    products = extract_budget_products(detail)
    category_ids, category_names = extract_category_data(products)

    full_text = build_detail_search_text(
        list_row,
        detail,
        products,
    )

    rejected = reject_reason(full_text)
    if rejected:
        return None, f"rejected:{rejected}"

    accepted_by_category = bool(
        category_ids.intersection(TRANSPORT_CATEGORY_IDS)
    )
    accepted_by_text = contains_keyword(
        full_text,
        DETAIL_KEYWORDS,
    )

    if not accepted_by_category and not accepted_by_text:
        return None, "not_logistics"

    lot_id = detail.get("id") or list_row.get("id")
    display_no = (
        detail.get("display_no")
        or list_row.get("display_no")
        or ""
    )

    title = (
        list_row.get("name")
        or (
            products[0].get("Product_Name")
            if products
            else None
        )
        or detail.get("addon_description")
        or f"UZEX тендер № {display_no or lot_id}"
    )

    documents = extract_documents(detail)
    routes = extract_routes(products)
    requirements = extract_qualification_requirements(detail)

    reason_parts = []

    if accepted_by_category:
        matched = sorted(
            category_ids.intersection(TRANSPORT_CATEGORY_IDS)
        )
        reason_parts.append(
            "category:" + ",".join(map(str, matched))
        )

    if accepted_by_text:
        reason_parts.append("logistics_text")

    item = {
        "site": "UZEX",
        "id": lot_id,
        "display_no": display_no,
        "title": compact_text(title, 1000),
        "url": f"{UZEX_SITE_BASE}/lot/{lot_id}",
        "start_date": (
            detail.get("start_date")
            or list_row.get("start_date")
            or ""
        ),
        "end_date": (
            detail.get("end_date")
            or list_row.get("end_date")
            or ""
        ),
        "cost": (
            detail.get("start_cost")
            or list_row.get("cost")
            or 0
        ),
        "currency": detail.get("currency_name") or list_row.get(
            "currency_name"
        ) or "",
        "currency_code": (
            detail.get("currency_codeabc")
            or list_row.get("currency_codeabc")
            or ""
        ),
        "customer_name": (
            detail.get("customer_name")
            or list_row.get("seller_name")
            or ""
        ),
        "customer_tin": (
            detail.get("customer_tin")
            or list_row.get("seller_tin")
            or ""
        ),
        "region_name": (
            detail.get("customer_region_name")
            or list_row.get("region_name")
            or ""
        ),
        "district_name": (
            detail.get("customer_district_name")
            or list_row.get("district_name")
            or ""
        ),
        "status_name": detail.get("status_name") or "",
        "payment_type": detail.get("payment_type_name") or "",
        "advance_payment_perc": detail.get(
            "advance_payment_perc"
        ),
        "term_payment_days": detail.get("term_payment_days"),
        "pledge_name": detail.get("pledge_name") or "",
        "pledge_value": detail.get("pledge_value"),
        "category_ids": sorted(category_ids),
        "category_names": category_names,
        "routes": routes,
        "documents": documents,
        "document_names": [
            document.get("name", "")
            for document in documents
        ],
        "qualification_requirements": requirements,
        "addon_description": detail.get(
            "addon_description"
        ) or "",
        "technical_description": detail.get(
            "technical_description"
        ) or "",
        "reason": ";".join(reason_parts),
    }

    return item, item["reason"]


def parse_uzex():
    started = time.time()
    list_rows, list_diag = fetch_all_trade_list()

    title_candidates = [
        row for row in list_rows
        if title_is_candidate(row)
    ]

    # Safety limit to prevent accidental overload.
    title_candidates = title_candidates[:MAX_DETAIL_CANDIDATES]

    detail_logs = []
    accepted_items = []
    rejected_count = 0
    failed_count = 0

    print(
        "UZEX CANDIDATES:",
        len(title_candidates),
        "from list:",
        len(list_rows),
    )

    with ThreadPoolExecutor(
        max_workers=DETAIL_WORKERS
    ) as executor:
        future_map = {
            executor.submit(
                fetch_trade_detail,
                row.get("id"),
            ): row
            for row in title_candidates
            if row.get("id")
        }

        for future in as_completed(future_map):
            if time.time() - started > MAX_TOTAL_SECONDS - 20:
                break

            list_row = future_map[future]

            try:
                detail, detail_log = future.result()
                detail_logs.append(detail_log)

                if not detail:
                    failed_count += 1
                    continue

                item, reason = analyze_trade(
                    list_row,
                    detail,
                )

                if item:
                    accepted_items.append(item)
                else:
                    rejected_count += 1

            except Exception as exc:
                failed_count += 1
                detail_logs.append({
                    "id": list_row.get("id"),
                    "error": (
                        f"{type(exc).__name__}: {exc}"
                    ),
                })

    # Deduplicate.
    unique = {}
    for item in accepted_items:
        unique[tender_key(item)] = item

    items = list(unique.values())
    items.sort(
        key=lambda item: str(item.get("end_date") or "")
    )

    diagnostics = {
        "status": "ok",
        "duration_seconds": round(
            time.time() - started,
            2,
        ),
        "trade_list": list_diag,
        "title_candidates": len(title_candidates),
        "detail_checked": len(detail_logs),
        "accepted": len(items),
        "rejected": rejected_count,
        "failed": failed_count,
        "detail_logs": detail_logs[:300],
    }

    if not list_rows:
        diagnostics["status"] = "error"
        diagnostics["warning"] = (
            "TradeList returned no rows"
        )
    elif not title_candidates:
        diagnostics["status"] = "warning"
        diagnostics["warning"] = (
            "TradeList worked, but no titles matched "
            "the logistics pre-filter"
        )
    elif not items:
        diagnostics["status"] = "warning"
        diagnostics["warning"] = (
            "Candidate lots were checked, but no "
            "logistics tenders were accepted"
        )

    with DATA_LOCK:
        global LAST_ITEMS, LAST_DIAGNOSTICS
        LAST_ITEMS = items
        LAST_DIAGNOSTICS = diagnostics

    return items, diagnostics


# ============================================================
# SCAN CORE
# ============================================================

def build_tender_message(item: dict) -> str:
    categories = ", ".join(
        item.get("category_names", [])
    )
    routes = item.get("routes", [])
    route_preview = "\n".join(
        f"• {route}"
        for route in routes[:4]
    )

    documents = item.get("documents", [])
    document_preview = "\n".join(
        f"• {doc.get('name')}"
        for doc in documents[:5]
    )

    message = (
        "🆕 Новый логистический тендер UZEX\n\n"
        f"📌 {item.get('title', '')}\n\n"
        f"🔢 №: {item.get('display_no', '')}\n"
        f"🏢 Заказчик: {item.get('customer_name', '')}\n"
        f"💰 Сумма: {format_money(item.get('cost'))} "
        f"{item.get('currency_code', '')}\n"
        f"📅 Окончание: {item.get('end_date', '')}\n"
        f"💳 Оплата: {item.get('payment_type', '')}\n"
        f"📂 Категория: {categories}\n"
    )

    if route_preview:
        message += f"\n🚚 Маршруты:\n{route_preview}\n"

    if document_preview:
        message += f"\n📎 Документы:\n{document_preview}\n"

    message += f"\n🔗 {item.get('url', '')}"

    return message


def run_scan(trigger="manual"):
    scan_started = time.time()

    result = {
        "status": "success",
        "version": APP_VERSION,
        "sources": {
            "Tenderweek": 0,
            "UZEX": 0,
            "XT-Xarid": 0,
        },
        "found_total": 0,
        "new_total": 0,
        "duplicates": 0,
        "errors": [],
        "warnings": [],
        "duration_seconds": 0,
        "uzex_diagnostics": {},
    }

    print("SCAN STARTED:", APP_VERSION, trigger)

    try:
        items, diagnostics = parse_uzex()
        result["sources"]["UZEX"] = len(items)
        result["found_total"] = len(items)
        result["uzex_diagnostics"] = {
            "status": diagnostics.get("status"),
            "trade_list_rows": (
                diagnostics
                .get("trade_list", {})
                .get("unique_rows", 0)
            ),
            "title_candidates": diagnostics.get(
                "title_candidates",
                0,
            ),
            "detail_checked": diagnostics.get(
                "detail_checked",
                0,
            ),
            "accepted": diagnostics.get(
                "accepted",
                0,
            ),
            "failed": diagnostics.get(
                "failed",
                0,
            ),
        }

        if diagnostics.get("status") != "ok":
            result["status"] = "warning"
            warning = diagnostics.get("warning")
            if warning:
                result["warnings"].append(warning)

    except Exception as exc:
        items = []
        result["status"] = "warning"
        result["errors"].append(
            f"UZEX: {type(exc).__name__}: {exc}"
        )
        print("UZEX SCAN ERROR:", traceback.format_exc())

    try:
        sheet = get_sheet()
        existing_keys = get_existing_keys(sheet)

        new_items = []
        duplicates = 0

        for item in items:
            possible_keys = {
                tender_key(item),
                f"uzex_display:{normalize_text(item.get('display_no'))}",
                (
                    f"{normalize_text(item.get('title'))}|"
                    f"{normalize_text(item.get('url'))}"
                ),
            }

            if any(
                key in existing_keys
                for key in possible_keys
            ):
                duplicates += 1
            else:
                new_items.append(item)

        result["new_total"] = save_new_tenders(
            sheet,
            new_items,
        )
        result["duplicates"] = duplicates

        for item in new_items[:15]:
            send_telegram(build_tender_message(item))

    except Exception as exc:
        result["status"] = "warning"
        result["errors"].append(
            f"Google Sheets: {type(exc).__name__}: {exc}"
        )
        print(
            "GOOGLE SHEETS ERROR:",
            traceback.format_exc(),
        )

    result["duration_seconds"] = round(
        time.time() - scan_started,
        2,
    )

    summary = (
        f"📊 AI Tender Agent\n"
        f"{APP_VERSION}\n"
        f"Сканирование завершено\n\n"
        f"UZEX: {result['sources']['UZEX']}\n"
        f"Tenderweek: {result['sources']['Tenderweek']}\n"
        f"XT-Xarid: {result['sources']['XT-Xarid']}\n\n"
        f"Всего найдено: {result['found_total']}\n"
        f"Новых сохранено: {result['new_total']}\n"
        f"Дубликатов: {result['duplicates']}\n"
        f"Время: {result['duration_seconds']} сек.\n"
        f"Статус: {result['status']}"
    )

    if result["warnings"]:
        summary += (
            "\n\n⚠️ Предупреждения:\n"
            + "\n".join(result["warnings"][:5])
        )

    if result["errors"]:
        summary += (
            "\n\n❌ Ошибки:\n"
            + "\n".join(result["errors"][:5])
        )

    send_telegram(summary)

    print(
        "SCAN FINISHED:",
        json.dumps(result, ensure_ascii=False),
    )

    return result


def background_scan_worker(trigger):
    try:
        result = run_scan(trigger)

        with SCAN_LOCK:
            SCAN_STATE["last_result"] = result
            SCAN_STATE["last_error"] = None

    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"

        print(
            "BACKGROUND WORKER ERROR:",
            traceback.format_exc(),
        )

        with SCAN_LOCK:
            SCAN_STATE["last_error"] = error

        send_telegram(
            "❌ AI Tender Agent error\n\n"
            f"Version: {APP_VERSION}\n"
            f"Trigger: {trigger}\n"
            f"Error: {error}"
        )

    finally:
        with SCAN_LOCK:
            SCAN_STATE["running"] = False
            SCAN_STATE["finished_at"] = now_str()


def start_background_scan(trigger="manual_http"):
    with SCAN_LOCK:
        if SCAN_STATE["running"]:
            return {
                "status": "already_running",
                "version": APP_VERSION,
                "running": True,
                "started_at": SCAN_STATE["started_at"],
                "message": "Scan already running",
            }

        SCAN_STATE["running"] = True
        SCAN_STATE["started_at"] = now_str()
        SCAN_STATE["finished_at"] = None
        SCAN_STATE["last_trigger"] = trigger
        SCAN_STATE["last_error"] = None
        SCAN_STATE["last_result"] = None

    thread = threading.Thread(
        target=background_scan_worker,
        args=(trigger,),
        daemon=True,
    )
    thread.start()

    return {
        "status": "accepted",
        "version": APP_VERSION,
        "running": True,
        "started_at": SCAN_STATE["started_at"],
        "message": "Scan started. Check /scan_status.",
    }


# ============================================================
# ENDPOINTS
# ============================================================

@app.get("/")
def home():
    return {
        "status": "AI Tender Agent is running",
        "version": APP_VERSION,
        "engine": "UZEX Real API",
    }


@app.head("/")
def head_home():
    return {}


@app.get("/version")
def version():
    return {"version": APP_VERSION}


@app.get("/health")
def health():
    result = {
        "status": "ok",
        "telegram": False,
        "google_sheets": False,
        "uzex_trade_list": False,
        "uzex_get_trade": False,
        "errors": [],
    }

    try:
        if not BOT_TOKEN or not CHAT_ID:
            raise RuntimeError("BOT_TOKEN/CHAT_ID missing")

        response = requests.get(
            f"https://api.telegram.org/bot{BOT_TOKEN}/getMe",
            timeout=10,
        )
        result["telegram"] = response.status_code == 200

        if response.status_code != 200:
            result["errors"].append(
                f"Telegram HTTP {response.status_code}"
            )

    except Exception as exc:
        result["errors"].append(
            f"Telegram: {type(exc).__name__}: {exc}"
        )

    try:
        sheet = get_sheet()
        sheet.row_values(1)
        result["google_sheets"] = True

    except Exception as exc:
        result["errors"].append(
            f"Google Sheets: {type(exc).__name__}: {exc}"
        )

    try:
        session = make_session()

        rows, _ = fetch_trade_list_page(
            session,
            1,
            1,
        )
        result["uzex_trade_list"] = isinstance(
            rows,
            list,
        )

        if rows and rows[0].get("id"):
            detail, detail_log = fetch_trade_detail(
                rows[0].get("id")
            )
            result["uzex_get_trade"] = isinstance(
                detail,
                dict,
            )

            if detail_log.get("error"):
                result["errors"].append(
                    f"GetTrade: {detail_log.get('error')}"
                )

    except Exception as exc:
        result["errors"].append(
            f"UZEX API: {type(exc).__name__}: {exc}"
        )

    if result["errors"]:
        result["status"] = "warning"

    return result


@app.get("/scan")
def scan():
    return start_background_scan("cron_http")


@app.get("/scan_start")
def scan_start():
    return start_background_scan("manual_http")


@app.get("/scan_status")
def scan_status():
    with SCAN_LOCK:
        return {
            "status": "ok",
            "version": APP_VERSION,
            **SCAN_STATE,
        }


@app.get("/debug_uzex_list")
def debug_uzex_list(
    start: int = Query(default=1, ge=1),
    end: int = Query(default=20, ge=1, le=200),
):
    if end < start:
        return {
            "status": "error",
            "message": "end must be greater than or equal to start",
        }

    session = make_session()
    rows, diagnostics = fetch_trade_list_page(
        session,
        start,
        end,
    )

    return {
        "version": APP_VERSION,
        "count": len(rows),
        "diagnostics": diagnostics,
        "items": rows,
    }


@app.get("/debug_trade/{lot_id}")
def debug_trade(lot_id: int):
    detail, diagnostics = fetch_trade_detail(lot_id)

    return {
        "version": APP_VERSION,
        "found": bool(detail),
        "diagnostics": diagnostics,
        "trade": detail,
    }


@app.get("/debug_items")
def debug_items(run: bool = Query(default=False)):
    if run:
        items, diagnostics = parse_uzex()

        return {
            "version": APP_VERSION,
            "count": len(items),
            "items": items[:200],
            "diagnostics": diagnostics,
        }

    with DATA_LOCK:
        return {
            "version": APP_VERSION,
            "count": len(LAST_ITEMS),
            "items": LAST_ITEMS[:200],
            "diagnostics": LAST_DIAGNOSTICS,
            "message": (
                "Use /debug_items?run=true "
                "to run a fresh UZEX scan."
            ),
        }


@app.get("/debug_categories")
def debug_categories():
    session = make_session()

    response = session.get(
        UZEX_CATEGORIES_URL,
        timeout=REQUEST_TIMEOUT,
    )
    response.raise_for_status()
    data = response.json()

    selected = [
        category
        for category in data
        if category.get("id") in TRANSPORT_CATEGORY_IDS
    ]

    return {
        "version": APP_VERSION,
        "transport_category_ids": sorted(
            TRANSPORT_CATEGORY_IDS
        ),
        "selected_categories": selected,
        "all_categories_count": (
            len(data)
            if isinstance(data, list)
            else None
        ),
    }


@app.get("/test_filter")
def test_filter():
    tests = [
        "Услуга по перевозке грузов",
        "Оказание транспортных услуг",
        "Транспортно-экспедиционные услуги",
        "Yuk tashish xizmatlari",
        "Cargo transportation services",
        "Аренда спецтехники с водителем",
        "Ремонт грузового автомобиля",
        "Поставка автомобильных шин",
        "Закупка бетона",
    ]

    output = {}

    for text in tests:
        output[text] = {
            "title_candidate": contains_keyword(
                text,
                TITLE_KEYWORDS,
            ) and not bool(reject_reason(text)),
            "reject_reason": reject_reason(text),
        }

    return output


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        app,
        host="0.0.0.0",
        port=int(os.getenv("PORT", "10000")),
    )
