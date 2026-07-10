import os
import re
import json
import time
import threading
import traceback
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urlencode

import requests
import gspread
from bs4 import BeautifulSoup
from fastapi import FastAPI, Query
from google.oauth2.service_account import Credentials


# ============================================================
# AI TENDER AGENT — CARGO V29 UZEX ENGINE
# Main file: main_v2.py
# Render Start Command:
# uvicorn main_v2:app --host 0.0.0.0 --port $PORT
# ============================================================

APP_VERSION = "cargo_v29_uzex_engine"
app = FastAPI(title="AI Tender Agent", version=APP_VERSION)

BOT_TOKEN = os.getenv("BOT_TOKEN") or os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID") or os.getenv("TELEGRAM_CHAT_ID")
GOOGLE_SHEET_ID = os.getenv("GOOGLE_SHEET_ID")
GOOGLE_CREDS_JSON = (
    os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON")
    or os.getenv("GOOGLE_CREDS_JSON")
)
WORKSHEET_NAME = os.getenv("WORKSHEET_NAME", "Тендеры")

REQUEST_TIMEOUT = int(os.getenv("REQUEST_TIMEOUT", "25"))
MAX_TOTAL_SECONDS = int(os.getenv("MAX_TOTAL_SECONDS", "240"))
UZEX_LIST_PAGES = int(os.getenv("UZEX_LIST_PAGES", "8"))
UZEX_MAX_LOTS = int(os.getenv("UZEX_MAX_LOTS", "160"))
UZEX_DETAIL_WORKERS = int(os.getenv("UZEX_DETAIL_WORKERS", "8"))

UZEX_BASE = "https://etender.uzex.uz"
UZEX_LIST_URLS = [
    f"{UZEX_BASE}/lots/1/0",
    f"{UZEX_BASE}/lots/2/0",
]

SCAN_LOCK = threading.Lock()
SCAN_STATE = {
    "running": False,
    "started_at": None,
    "finished_at": None,
    "last_trigger": None,
    "last_result": None,
    "last_error": None,
}

DIAG_LOCK = threading.Lock()
DIAGNOSTICS = {
    "updated_at": None,
    "uzex": {},
    "tenderweek": {},
    "xt_xarid": {},
}


KEYWORDS = [
    "услуга по перевозке грузов",
    "услуги по перевозке грузов",
    "оказание услуг по перевозке грузов",
    "перевозка грузов",
    "перевозка товара",
    "перевозка товаров",
    "доставка грузов",
    "доставка товара",
    "доставка товаров",
    "грузоперевоз",
    "грузовые перевозки",
    "грузовой транспорт",
    "транспортные услуги",
    "оказание транспортных услуг",
    "автотранспортные услуги",
    "автомобильные перевозки",
    "международные перевозки",
    "внутренние перевозки",
    "междугородние перевозки",
    "железнодорожные перевозки",
    "жд перевозки",
    "ж/д перевозки",
    "контейнерные перевозки",
    "мультимодальные перевозки",
    "интермодальные перевозки",
    "экспедирование",
    "экспедиторские услуги",
    "транспортно-экспедиционные услуги",
    "транспортная экспедиция",
    "экспедитор",
    "складские услуги",
    "хранение груза",
    "погрузка",
    "разгрузка",
    "погрузочно-разгрузочные работы",
    "погрузо-разгрузочные работы",
    "аренда спецтехники",
    "фура",
    "тягач",
    "полуприцеп",
    "рефрижератор",
    "контейнеровоз",
    "таможенное оформление",
    "таможенный брокер",
    "таможенные услуги",
    "freight",
    "freight forwarding",
    "cargo transportation",
    "transport services",
    "logistics services",
    "shipping",
    "forwarding",
    "warehouse services",
    "customs clearance",
    "yuk tashish",
    "yuklarni tashish",
    "yuk tashish xizmati",
    "yuk tashish xizmatlari",
    "transport xizmati",
    "transport xizmatlari",
    "logistika xizmatlari",
    "yetkazib berish xizmati",
    "юк ташиш",
    "юкларни ташиш",
    "транспорт хизмати",
    "транспорт хизматлари",
    "логистика хизматлари",
    "етказиб бериш хизмати",
]

# Слова, которые сами по себе слишком общие и не должны принимать тендер.
WEAK_WORDS = {
    "транспорт",
    "логистика",
    "доставка",
    "cargo",
    "logistics",
    "delivery",
    "transportation",
    "ombor",
    "омбор",
}

REJECT_WORDS = [
    "ремонт автомобиля",
    "ремонт транспорт",
    "техническое обслуживание автомобиля",
    "техническое обслуживание транспорт",
    "техобслуживание автомобиля",
    "запасные части",
    "запчаст",
    "поставка автомобиля",
    "покупка автомобиля",
    "приобретение автомобиля",
    "автомобильная шина",
    "автошина",
    "масло моторное",
    "страхование транспорта",
    "мебель",
    "канцеляр",
    "бетон",
    "строительные материалы",
    "компьютер",
    "принтер",
    "картридж",
    "кондиционер",
    "медицинское оборудование",
]

STRONG_PATTERNS = [
    r"\bперевоз\w*\s+(?:груз\w*|товар\w*|продукц\w*)",
    r"\bтранспортн\w+\s+услуг\w*",
    r"\bэкспедитор\w*\s+услуг\w*",
    r"\bтранспортно[-\s]?экспедицион\w+",
    r"\bгрузоперевоз\w*",
    r"\byuk\w*\s+tash\w*",
    r"\btransport\w*\s+xizmat\w*",
    r"\bfreight\s+(?:forwarding|transportation)",
    r"\bcargo\s+transportation",
    r"\blogistics\s+services",
]


def now_str():
    return datetime.now().strftime("%d.%m.%Y %H:%M:%S")


def normalize_text(value) -> str:
    if value is None:
        return ""
    text = str(value).replace("\xa0", " ").lower()
    text = re.sub(r"\s+", " ", text).strip()
    return text


def compact_text(value, limit=1000) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text[:limit]


def contains_reject(text: str) -> str:
    t = normalize_text(text)
    for word in REJECT_WORDS:
        if word in t:
            return word
    return ""


def logistics_match(text: str):
    t = normalize_text(text)
    if not t:
        return False, "empty"

    rejected = contains_reject(t)
    if rejected:
        return False, f"rejected:{rejected}"

    for pattern in STRONG_PATTERNS:
        if re.search(pattern, t, flags=re.IGNORECASE):
            return True, f"accepted_pattern:{pattern}"

    for keyword in KEYWORDS:
        k = normalize_text(keyword)
        if k in WEAK_WORDS:
            continue
        if k in t:
            return True, f"accepted_keyword:{keyword}"

    return False, "no_logistics_phrase"


def is_logistics_tender(text: str) -> bool:
    return logistics_match(text)[0]


def tender_key(item: dict) -> str:
    # UZEX lot URL is the most stable key.
    url = normalize_text(item.get("url"))
    lot_match = re.search(r"/lot/(\d+)", url)
    if lot_match:
        return f"uzex_lot:{lot_match.group(1)}"

    title = normalize_text(item.get("title"))
    return f"{title}|{url}"


def update_diag(source: str, data: dict):
    with DIAG_LOCK:
        DIAGNOSTICS["updated_at"] = now_str()
        DIAGNOSTICS[source] = data


def make_session() -> requests.Session:
    session = requests.Session()
    session.headers.update({
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/149.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8",
        "Accept-Language": "ru-RU,ru;q=0.9,uz;q=0.8,en;q=0.7",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
    })
    return session


def safe_get(session: requests.Session, url: str, *, params=None, timeout=None):
    response = session.get(
        url,
        params=params,
        timeout=timeout or REQUEST_TIMEOUT,
        allow_redirects=True,
    )
    return response


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


def get_sheet():
    if not GOOGLE_CREDS_JSON:
        raise RuntimeError("GOOGLE_SERVICE_ACCOUNT_JSON / GOOGLE_CREDS_JSON is empty")
    if not GOOGLE_SHEET_ID:
        raise RuntimeError("GOOGLE_SHEET_ID is empty")

    info = json.loads(GOOGLE_CREDS_JSON)
    creds = Credentials.from_service_account_info(
        info,
        scopes=[
            "https://www.googleapis.com/auth/spreadsheets",
            "https://www.googleapis.com/auth/drive",
        ],
    )
    client = gspread.authorize(creds)
    book = client.open_by_key(GOOGLE_SHEET_ID)

    try:
        return book.worksheet(WORKSHEET_NAME)
    except Exception:
        return book.sheet1


def get_existing_keys(sheet):
    rows = sheet.get_all_values()
    if not rows:
        return set()

    header = [normalize_text(cell) for cell in rows[0]]
    title_idx = None
    url_idx = None

    for index, heading in enumerate(header):
        if heading in {"название", "title", "тендер", "наименование"}:
            title_idx = index
        if heading in {"ссылка", "url", "link"}:
            url_idx = index

    if title_idx is None:
        title_idx = 2
    if url_idx is None:
        url_idx = 3

    keys = set()
    for row in rows[1:]:
        title = row[title_idx] if len(row) > title_idx else ""
        url = row[url_idx] if len(row) > url_idx else ""
        if title or url:
            keys.add(tender_key({"title": title, "url": url}))

    return keys


def save_new_tenders(sheet, items):
    rows = []
    for item in items:
        rows.append([
            datetime.now().strftime("%d.%m.%Y %H:%M"),
            item.get("site", ""),
            item.get("title", ""),
            item.get("url", ""),
            "Новый",
            item.get("reason", ""),
        ])

    if rows:
        sheet.append_rows(rows, value_input_option="USER_ENTERED")
    return len(rows)


# ============================================================
# UZEX ENGINE
# ============================================================

def extract_lot_ids_from_html(html: str):
    ids = set()

    # Обычные ссылки /lot/123456
    for lot_id in re.findall(r"(?:https?://etender\.uzex\.uz)?/lot/(\d+)", html, re.I):
        ids.add(lot_id)

    # Экранированные ссылки в JSON / JS: \/lot\/123456
    for lot_id in re.findall(r"\\?/lot\\?/(\d+)", html, re.I):
        ids.add(lot_id)

    # Возможные JSON-поля.
    for lot_id in re.findall(
        r'["\'](?:lotId|lot_id|tradeId|trade_id|id)["\']\s*:\s*["\']?(\d{4,})',
        html,
        re.I,
    ):
        ids.add(lot_id)

    return ids


def build_uzex_list_candidates():
    urls = []

    # Несколько вариантов пагинации. Неподдерживаемые варианты просто
    # вернут ту же страницу и будут дедуплицированы по lot ID.
    for base in UZEX_LIST_URLS:
        urls.append(base)
        for page in range(1, UZEX_LIST_PAGES + 1):
            variants = [
                {"page": page},
                {"Page": page},
                {"pageNumber": page},
                {"pageIndex": page - 1},
                {"currentPage": page},
            ]
            for params in variants:
                urls.append(f"{base}?{urlencode(params)}")

    # Убираем повторы, сохраняя порядок.
    return list(dict.fromkeys(urls))


def discover_uzex_lot_ids():
    session = make_session()
    list_urls = build_uzex_list_candidates()
    discovered = set()
    request_log = []
    errors = []

    for url in list_urls:
        if len(discovered) >= UZEX_MAX_LOTS:
            break

        try:
            response = safe_get(session, url, timeout=REQUEST_TIMEOUT)
            ids = extract_lot_ids_from_html(response.text) if response.status_code == 200 else set()
            before = len(discovered)
            discovered.update(ids)

            request_log.append({
                "url": url,
                "status": response.status_code,
                "size": len(response.content),
                "ids_on_page": len(ids),
                "new_ids": len(discovered) - before,
                "content_type": response.headers.get("content-type", ""),
            })

            print(
                "UZEX LIST:",
                response.status_code,
                len(response.content),
                "ids:", len(ids),
                "new:", len(discovered) - before,
                url,
            )
        except Exception as exc:
            errors.append(f"{url}: {type(exc).__name__}: {exc}")
            print("UZEX LIST ERROR:", url, repr(exc))

    return list(discovered)[:UZEX_MAX_LOTS], request_log, errors


def select_lot_container_text(soup: BeautifulSoup, lot_id: str) -> str:
    # Ищем ссылку на lot и берём ближайший содержательный контейнер.
    link = soup.find("a", href=re.compile(rf"/lot/{re.escape(lot_id)}(?:\D|$)"))
    if not link:
        return ""

    candidates = []
    node = link
    for _ in range(6):
        node = node.parent
        if not node:
            break
        text = compact_text(node.get_text(" ", strip=True), 4000)
        if 30 <= len(text) <= 4000:
            candidates.append(text)

    if not candidates:
        return compact_text(link.get_text(" ", strip=True), 1000)

    # Ближайший контейнер, в котором уже есть достаточно информации.
    candidates.sort(key=len)
    return candidates[0]


def extract_title_from_detail(soup: BeautifulSoup, full_text: str, lot_id: str) -> str:
    candidates = []

    for tag_name in ["h1", "h2", "h3", "h4"]:
        for tag in soup.find_all(tag_name):
            text = compact_text(tag.get_text(" ", strip=True), 500)
            if len(text) >= 6:
                candidates.append(text)

    for attrs in [
        {"property": "og:title"},
        {"name": "twitter:title"},
    ]:
        tag = soup.find("meta", attrs=attrs)
        if tag and tag.get("content"):
            candidates.append(compact_text(tag.get("content"), 500))

    if soup.title:
        candidates.append(compact_text(soup.title.get_text(" ", strip=True), 500))

    # Убираем общие заголовки.
    ignored = [
        "etender.uzex.uz",
        "o‘zbekiston respublika tovar-xom ashyo birjasi",
        "лот ҳақида маълумот",
        "lot haqida ma",
        "технические требования",
        "texnik talablar",
        "калькулятор",
        "kalkulyator",
    ]

    for candidate in candidates:
        low = normalize_text(candidate)
        if any(part in low for part in ignored):
            continue
        if lot_id and candidate.strip() == lot_id:
            continue
        return candidate[:500]

    # Ищем текст рядом с номером/номи.
    patterns = [
        r"(?:Номи|Nomi|Наименование|Название)\s*[:\-]\s*(.{10,500})",
        r"(?:Предмет закупки|Xarid predmeti)\s*[:\-]\s*(.{10,500})",
    ]
    for pattern in patterns:
        match = re.search(pattern, full_text, re.I)
        if match:
            return compact_text(match.group(1), 500)

    # Последний безопасный вариант.
    return f"UZEX логистический тендер № {lot_id}"


def fetch_uzex_lot(lot_id: str):
    session = make_session()
    url = f"{UZEX_BASE}/lot/{lot_id}"

    try:
        response = safe_get(session, url, timeout=REQUEST_TIMEOUT)
        if response.status_code != 200:
            return None, {
                "lot_id": lot_id,
                "status": response.status_code,
                "size": len(response.content),
                "error": "non_200",
            }

        soup = BeautifulSoup(response.text, "html.parser")
        full_text = compact_text(soup.get_text(" ", strip=True), 25000)

        # Иногда полезный текст находится в JSON/JS и не виден в soup.get_text().
        searchable = f"{full_text} {response.text[:120000]}"
        accepted, reason = logistics_match(searchable)

        if not accepted:
            return None, {
                "lot_id": lot_id,
                "status": response.status_code,
                "size": len(response.content),
                "accepted": False,
                "reason": reason,
            }

        title = extract_title_from_detail(soup, full_text, lot_id)
        item = {
            "site": "UZEX",
            "title": title,
            "url": url,
            "reason": reason,
        }
        return item, {
            "lot_id": lot_id,
            "status": response.status_code,
            "size": len(response.content),
            "accepted": True,
            "reason": reason,
        }

    except Exception as exc:
        return None, {
            "lot_id": lot_id,
            "status": None,
            "size": 0,
            "error": f"{type(exc).__name__}: {exc}",
        }


def parse_uzex():
    started = time.time()
    lot_ids, list_log, discovery_errors = discover_uzex_lot_ids()

    unique = {}
    detail_log = []
    accepted_count = 0
    rejected_count = 0
    failed_count = 0

    if lot_ids:
        with ThreadPoolExecutor(max_workers=UZEX_DETAIL_WORKERS) as executor:
            future_map = {
                executor.submit(fetch_uzex_lot, lot_id): lot_id
                for lot_id in lot_ids
            }

            for future in as_completed(future_map):
                if time.time() - started > MAX_TOTAL_SECONDS - 20:
                    break

                try:
                    item, detail = future.result()
                    detail_log.append(detail)

                    if item:
                        unique[tender_key(item)] = item
                        accepted_count += 1
                    elif detail.get("error"):
                        failed_count += 1
                    else:
                        rejected_count += 1
                except Exception as exc:
                    failed_count += 1
                    detail_log.append({
                        "lot_id": future_map[future],
                        "error": f"{type(exc).__name__}: {exc}",
                    })

    status = "ok"
    warning = None

    if not lot_ids:
        status = "error"
        warning = "UZEX list pages returned no lot IDs"
    elif accepted_count == 0:
        status = "warning"
        warning = "UZEX lots were discovered, but no logistics lots passed the filter"

    diag = {
        "status": status,
        "warning": warning,
        "started_at": datetime.fromtimestamp(started).strftime("%d.%m.%Y %H:%M:%S"),
        "finished_at": now_str(),
        "duration_seconds": round(time.time() - started, 2),
        "list_requests": len(list_log),
        "list_http_200": sum(1 for row in list_log if row["status"] == 200),
        "lot_ids_discovered": len(lot_ids),
        "detail_checked": len(detail_log),
        "accepted": accepted_count,
        "rejected": rejected_count,
        "failed": failed_count,
        "discovery_errors": discovery_errors[:20],
        "list_log": list_log[:80],
        "detail_log": detail_log[:120],
    }
    update_diag("uzex", diag)

    print("UZEX ENGINE RESULT:", json.dumps({
        "lot_ids": len(lot_ids),
        "accepted": accepted_count,
        "rejected": rejected_count,
        "failed": failed_count,
    }, ensure_ascii=False))

    return list(unique.values())


# ============================================================
# TENDERWEEK / XT-XARID — conservative fallback
# ============================================================

def parse_generic_html_source(source_name: str, urls):
    session = make_session()
    unique = {}
    logs = []
    errors = []

    for url in urls:
        try:
            response = safe_get(session, url)
            logs.append({
                "url": url,
                "status": response.status_code,
                "size": len(response.content),
            })
            if response.status_code != 200:
                continue

            soup = BeautifulSoup(response.text, "html.parser")
            for link in soup.find_all("a", href=True):
                href = requests.compat.urljoin(url, link.get("href"))
                anchor_text = compact_text(link.get_text(" ", strip=True), 1000)

                # Проверяем не только текст ссылки, но и ближайший контейнер.
                container_text = anchor_text
                node = link
                for _ in range(4):
                    node = node.parent
                    if not node:
                        break
                    candidate = compact_text(node.get_text(" ", strip=True), 3000)
                    if len(candidate) > len(container_text):
                        container_text = candidate
                    if len(container_text) >= 100:
                        break

                accepted, reason = logistics_match(f"{anchor_text} {container_text} {href}")
                if not accepted:
                    continue

                title = anchor_text if len(anchor_text) >= 8 else container_text[:500]
                if not title:
                    continue

                item = {
                    "site": source_name,
                    "title": title[:500],
                    "url": href,
                    "reason": reason,
                }
                unique[tender_key(item)] = item

        except Exception as exc:
            errors.append(f"{url}: {type(exc).__name__}: {exc}")

    key = "tenderweek" if source_name == "Tenderweek" else "xt_xarid"
    update_diag(key, {
        "status": "ok" if any(row["status"] == 200 for row in logs) else "error",
        "requests": len(logs),
        "accepted": len(unique),
        "errors": errors[:20],
        "logs": logs[:30],
    })
    return list(unique.values())


def parse_tenderweek():
    urls = [
        "https://www.tenderweek.com/",
        "https://www.tenderweek.com/?search=перевозка",
        "https://www.tenderweek.com/?search=логистика",
        "https://www.tenderweek.com/?search=транспортные+услуги",
    ]
    return parse_generic_html_source("Tenderweek", urls)


def parse_xt_xarid():
    urls = [
        "https://xt-xarid.uz/",
        "https://xt-xarid.uz/?search=перевозка",
        "https://xt-xarid.uz/?search=логистика",
        "https://xt-xarid.uz/?search=transport",
    ]
    return parse_generic_html_source("XT-Xarid", urls)


# ============================================================
# SCAN CORE
# ============================================================

def run_scan(trigger="manual"):
    scan_started = time.time()
    result = {
        "status": "success",
        "version": APP_VERSION,
        "sources": {"Tenderweek": 0, "UZEX": 0, "XT-Xarid": 0},
        "found_total": 0,
        "new_total": 0,
        "duplicates": 0,
        "errors": [],
        "warnings": [],
        "duration_seconds": 0,
    }

    all_items = []
    sources = [
        ("UZEX", parse_uzex),
        ("Tenderweek", parse_tenderweek),
        ("XT-Xarid", parse_xt_xarid),
    ]

    print("SCAN STARTED:", APP_VERSION, trigger)

    for source_name, parser in sources:
        if time.time() - scan_started > MAX_TOTAL_SECONDS:
            result["errors"].append("Total scan timeout reached")
            result["status"] = "warning"
            break

        try:
            items = parser()
            result["sources"][source_name] = len(items)
            all_items.extend(items)
            print(source_name, "DONE:", len(items))
        except Exception as exc:
            error_text = f"{source_name}: {type(exc).__name__}: {exc}"
            result["errors"].append(error_text)
            result["status"] = "warning"
            print(error_text)
            print(traceback.format_exc())

    unique = {}
    for item in all_items:
        accepted, _ = logistics_match(
            f"{item.get('title', '')} {item.get('url', '')} {item.get('reason', '')}"
        )
        if accepted:
            unique[tender_key(item)] = item

    all_items = list(unique.values())
    result["found_total"] = len(all_items)

    with DIAG_LOCK:
        uzex_diag = dict(DIAGNOSTICS.get("uzex") or {})

    if uzex_diag.get("status") == "error":
        result["status"] = "warning"
        result["warnings"].append(
            uzex_diag.get("warning") or "UZEX source is unavailable"
        )
    elif uzex_diag.get("status") == "warning":
        result["warnings"].append(
            uzex_diag.get("warning") or "UZEX returned no accepted logistics lots"
        )

    if sum(result["sources"].values()) == 0:
        result["status"] = "warning"
        result["warnings"].append(
            "All sources returned zero. Check /debug_sources and /debug_uzex."
        )

    try:
        sheet = get_sheet()
        existing_keys = get_existing_keys(sheet)

        new_items = []
        duplicates = 0

        for item in all_items:
            if tender_key(item) in existing_keys:
                duplicates += 1
            else:
                new_items.append(item)

        result["new_total"] = save_new_tenders(sheet, new_items)
        result["duplicates"] = duplicates

        for item in new_items[:15]:
            send_telegram(
                "🆕 Новый логистический тендер\n\n"
                f"📌 {item.get('site', '')}\n\n"
                f"{item.get('title', '')}\n\n"
                f"{item.get('url', '')}"
            )

    except Exception as exc:
        result["status"] = "warning"
        result["errors"].append(
            f"Google Sheets: {type(exc).__name__}: {exc}"
        )
        print("GOOGLE SHEETS ERROR:", traceback.format_exc())

    result["duration_seconds"] = round(time.time() - scan_started, 2)

    message = (
        f"📊 AI Tender Agent\n"
        f"{APP_VERSION}\n"
        f"Сканирование завершено\n\n"
        f"Tenderweek: {result['sources']['Tenderweek']}\n"
        f"UZEX: {result['sources']['UZEX']}\n"
        f"XT-Xarid: {result['sources']['XT-Xarid']}\n\n"
        f"Всего найдено: {result['found_total']}\n"
        f"Новых сохранено: {result['new_total']}\n"
        f"Дубликатов: {result['duplicates']}\n"
        f"Время: {result['duration_seconds']} сек.\n"
        f"Статус: {result['status']}"
    )

    if result["warnings"]:
        message += "\n\n⚠️ Предупреждения:\n" + "\n".join(result["warnings"][:4])
    if result["errors"]:
        message += "\n\n❌ Ошибки:\n" + "\n".join(result["errors"][:4])

    send_telegram(message)
    print("SCAN FINISHED:", json.dumps(result, ensure_ascii=False))
    return result


def background_scan_worker(trigger):
    try:
        result = run_scan(trigger)
        with SCAN_LOCK:
            SCAN_STATE["last_result"] = result
            SCAN_STATE["last_error"] = None
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
        print("BACKGROUND WORKER ERROR:", traceback.format_exc())
        with SCAN_LOCK:
            SCAN_STATE["last_error"] = error
        send_telegram(
            f"❌ AI Tender Agent error\n\n"
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
        "uzex_home": False,
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
    except Exception as exc:
        result["errors"].append(f"Telegram: {type(exc).__name__}: {exc}")

    try:
        sheet = get_sheet()
        sheet.row_values(1)
        result["google_sheets"] = True
    except Exception as exc:
        result["errors"].append(f"Google Sheets: {type(exc).__name__}: {exc}")

    try:
        session = make_session()
        response = safe_get(session, UZEX_LIST_URLS[0], timeout=15)
        result["uzex_home"] = response.status_code == 200
        if response.status_code != 200:
            result["errors"].append(f"UZEX HTTP {response.status_code}")
    except Exception as exc:
        result["errors"].append(f"UZEX: {type(exc).__name__}: {exc}")

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


@app.get("/debug_sources")
def debug_sources():
    with DIAG_LOCK:
        return {
            "version": APP_VERSION,
            "diagnostics": DIAGNOSTICS,
        }


@app.get("/debug_uzex")
def debug_uzex(run: bool = Query(default=False)):
    if run:
        items = parse_uzex()
        with DIAG_LOCK:
            diag = dict(DIAGNOSTICS.get("uzex") or {})
        return {
            "version": APP_VERSION,
            "count": len(items),
            "items": items[:100],
            "diagnostics": diag,
        }

    with DIAG_LOCK:
        diag = dict(DIAGNOSTICS.get("uzex") or {})
    return {
        "version": APP_VERSION,
        "message": "Use /debug_uzex?run=true to run UZEX diagnostics.",
        "diagnostics": diag,
    }


@app.get("/debug_items")
def debug_items(run: bool = Query(default=False)):
    if not run:
        with SCAN_LOCK:
            last_result = SCAN_STATE.get("last_result")
        return {
            "version": APP_VERSION,
            "message": "Use /debug_items?run=true to run all parsers.",
            "last_result": last_result,
        }

    items = []
    errors = []
    for name, parser in [
        ("UZEX", parse_uzex),
        ("Tenderweek", parse_tenderweek),
        ("XT-Xarid", parse_xt_xarid),
    ]:
        try:
            items.extend(parser())
        except Exception as exc:
            errors.append(f"{name}: {type(exc).__name__}: {exc}")

    unique = {tender_key(item): item for item in items}
    return {
        "version": APP_VERSION,
        "count": len(unique),
        "items": list(unique.values())[:200],
        "errors": errors,
    }


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
        "Ремонт грузового автомобиля",
        "Поставка автомобильных шин",
    ]
    return {
        text: {
            "accepted": logistics_match(text)[0],
            "reason": logistics_match(text)[1],
        }
        for text in tests
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        app,
        host="0.0.0.0",
        port=int(os.getenv("PORT", "10000")),
    )
