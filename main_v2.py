"""
Cargo V31 Enterprise Stable
AI Tender Agent for logistics tenders

Features:
- FastAPI service
- Internal scheduler (no external cron required)
- Background scans
- Protection against duplicate/stuck scans
- UZEX TradeList + GetTrade API support
- Telegram notifications
- Google Sheets storage
- Deduplication
- Health, version, metrics, logs and scan status endpoints
- Automatic startup scan
- Persistent state on disk when writable

Required environment variables:
BOT_TOKEN
CHAT_ID
GOOGLE_SHEET_ID
GOOGLE_SERVICE_ACCOUNT_JSON

Optional:
SCAN_INTERVAL_MINUTES=30
SCAN_ON_STARTUP=true
UZEX_TRADE_LIST_URL=https://etender.uzex.uz/api/Trade/GetTradeList
UZEX_GET_TRADE_URL=https://etender.uzex.uz/api/Trade/GetTrade
UZEX_BASE_URL=https://etender.uzex.uz
UZEX_PAGE_SIZE=100
MAX_PAGES=5
REQUEST_TIMEOUT_SECONDS=25
SCAN_TIMEOUT_SECONDS=240
STUCK_SCAN_MINUTES=10
LOG_LEVEL=INFO
PORT=10000
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import threading
import time
import traceback
from collections import deque
from contextlib import asynccontextmanager
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

import httpx
from fastapi import BackgroundTasks, FastAPI, HTTPException, Query
from fastapi.responses import JSONResponse, PlainTextResponse

try:
    import gspread
    from google.oauth2.service_account import Credentials
except Exception:
    gspread = None
    Credentials = None


VERSION = "cargo_v31_enterprise_stable"
APP_NAME = "AI Tender Agent Cargo V31 Enterprise Stable"
TZ = timezone(timedelta(hours=5))

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = Path(os.getenv("DATA_DIR", str(BASE_DIR / "data")))
DATA_DIR.mkdir(parents=True, exist_ok=True)

STATE_FILE = DATA_DIR / "cargo_v31_state.json"
LOG_FILE = DATA_DIR / "cargo_v31.log"

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
CHAT_ID = os.getenv("CHAT_ID", "").strip()
GOOGLE_SHEET_ID = os.getenv("GOOGLE_SHEET_ID", "").strip()
GOOGLE_SERVICE_ACCOUNT_JSON = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON", "").strip()

UZEX_TRADE_LIST_URL = os.getenv(
    "UZEX_TRADE_LIST_URL",
    "https://etender.uzex.uz/api/Trade/GetTradeList",
).strip()
UZEX_GET_TRADE_URL = os.getenv(
    "UZEX_GET_TRADE_URL",
    "https://etender.uzex.uz/api/Trade/GetTrade",
).strip()
UZEX_BASE_URL = os.getenv("UZEX_BASE_URL", "https://etender.uzex.uz").rstrip("/")

SCAN_INTERVAL_MINUTES = max(5, int(os.getenv("SCAN_INTERVAL_MINUTES", "30")))
SCAN_ON_STARTUP = os.getenv("SCAN_ON_STARTUP", "true").lower() in {"1", "true", "yes", "on"}
UZEX_PAGE_SIZE = max(10, int(os.getenv("UZEX_PAGE_SIZE", "100")))
MAX_PAGES = max(1, int(os.getenv("MAX_PAGES", "5")))
REQUEST_TIMEOUT_SECONDS = max(5, int(os.getenv("REQUEST_TIMEOUT_SECONDS", "25")))
SCAN_TIMEOUT_SECONDS = max(60, int(os.getenv("SCAN_TIMEOUT_SECONDS", "240")))
STUCK_SCAN_MINUTES = max(2, int(os.getenv("STUCK_SCAN_MINUTES", "10")))
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
SHEET_WORKSHEET_NAME = os.getenv("SHEET_WORKSHEET_NAME", "Тендеры").strip()

TRANSPORT_ACCEPT = [
    "перевозка груз",
    "грузоперевоз",
    "транспортные услуги",
    "транспортная услуга",
    "транспортно-экспедицион",
    "экспедиторские услуги",
    "логистические услуги",
    "международная перевозка",
    "автомобильная перевозка",
    "доставка груз",
    "услуги спецтехники с водителем",
    "yuk tashish",
    "yuklarni tashish",
    "transport xizmati",
    "transport xizmatlari",
    "logistika",
    "ekspeditorlik",
    "xalqaro yuk",
    "avtomobil transport",
]

STRONG_REJECT = [
    "приобретение автомобиля",
    "закупка автомобиля",
    "поставка автомобиля",
    "поставка автотранспорт",
    "запасные части",
    "запчасти",
    "ремонт автомобиля",
    "техническое обслуживание автомобиля",
    "автострахование",
    "шины",
    "аккумулятор",
    "yoqilg'i",
    "ehtiyot qismlar",
    "avtomobil sotib olish",
]

SHEET_HEADERS = [
    "ID",
    "Источник",
    "Номер лота",
    "Название",
    "Заказчик",
    "Сумма",
    "Валюта",
    "Дата начала",
    "Срок окончания",
    "Категория",
    "Описание",
    "Маршрут",
    "Тип транспорта",
    "Оплата",
    "Срок оплаты",
    "Срок оказания услуг",
    "Документы нужны",
    "Предупреждения по документам",
    "Ответственные задачи",
    "Приоритет",
    "AI Score",
    "Решение",
    "Статус",
    "Ссылка",
    "Дата добавления",
    "Причина фильтра",
    "Хеш",
]


def now_local() -> datetime:
    return datetime.now(TZ)


def iso_now() -> str:
    return now_local().isoformat(timespec="seconds")


def pretty_now() -> str:
    return now_local().strftime("%d.%m.%Y %H:%M:%S")


def env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.lower() in {"1", "true", "yes", "on"}


class RingBufferHandler(logging.Handler):
    def __init__(self, capacity: int = 500):
        super().__init__()
        self.records: deque[str] = deque(maxlen=capacity)

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.records.append(self.format(record))
        except Exception:
            pass


logger = logging.getLogger("cargo_v31")
logger.setLevel(getattr(logging, LOG_LEVEL, logging.INFO))
logger.propagate = False

formatter = logging.Formatter(
    "%(asctime)s | %(levelname)s | %(message)s",
    "%Y-%m-%d %H:%M:%S",
)

if not logger.handlers:
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    try:
        file_handler = logging.FileHandler(LOG_FILE, encoding="utf-8")
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)
    except Exception:
        pass

    ring_handler = RingBufferHandler(capacity=500)
    ring_handler.setFormatter(formatter)
    logger.addHandler(ring_handler)
else:
    ring_handler = next(
        (h for h in logger.handlers if isinstance(h, RingBufferHandler)),
        RingBufferHandler(),
    )


@dataclass
class ScanResult:
    status: str = "idle"
    version: str = VERSION
    trigger: Optional[str] = None
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    duration_seconds: float = 0.0
    sources: Dict[str, int] = field(default_factory=lambda: {"UZEX": 0})
    found_total: int = 0
    accepted_total: int = 0
    new_total: int = 0
    duplicates: int = 0
    rejected: int = 0
    telegram_sent: int = 0
    sheets_saved: int = 0
    errors: List[str] = field(default_factory=list)


@dataclass
class RuntimeState:
    running: bool = False
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    last_trigger: Optional[str] = None
    last_result: Optional[Dict[str, Any]] = None
    last_error: Optional[str] = None
    next_scheduled_run: Optional[str] = None
    scan_count: int = 0
    successful_scans: int = 0
    failed_scans: int = 0
    total_found: int = 0
    total_new: int = 0
    telegram_sent: int = 0
    sheets_saved: int = 0
    app_started_at: str = field(default_factory=iso_now)


state = RuntimeState()
state_lock = threading.RLock()
scan_lock = asyncio.Lock()
scheduler_task: Optional[asyncio.Task] = None
active_scan_task: Optional[asyncio.Task] = None


def load_state() -> None:
    global state
    if not STATE_FILE.exists():
        return
    try:
        raw = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        with state_lock:
            for key, value in raw.items():
                if hasattr(state, key):
                    setattr(state, key, value)
            state.running = False
            state.started_at = None
            state.next_scheduled_run = None
            state.app_started_at = iso_now()
        logger.info("Persistent state loaded")
    except Exception as exc:
        logger.warning("Could not load state: %s", exc)


def save_state() -> None:
    try:
        payload = asdict(state)
        temp = STATE_FILE.with_suffix(".tmp")
        temp.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temp.replace(STATE_FILE)
    except Exception as exc:
        logger.warning("Could not save state: %s", exc)


def normalize_text(value: Any) -> str:
    text = str(value or "").lower().strip()
    text = re.sub(r"\s+", " ", text)
    return text


def deep_get(obj: Any, *paths: str, default: Any = None) -> Any:
    for path in paths:
        current = obj
        ok = True
        for part in path.split("."):
            if isinstance(current, dict) and part in current:
                current = current[part]
            else:
                ok = False
                break
        if ok and current not in (None, ""):
            return current
    return default


def first_list(obj: Any) -> List[Any]:
    if isinstance(obj, list):
        return obj
    if not isinstance(obj, dict):
        return []
    candidates = [
        "data",
        "result",
        "items",
        "rows",
        "trades",
        "lots",
        "content",
        "Data",
        "Result",
        "Items",
    ]
    for key in candidates:
        value = obj.get(key)
        if isinstance(value, list):
            return value
        if isinstance(value, dict):
            nested = first_list(value)
            if nested:
                return nested
    for value in obj.values():
        if isinstance(value, list) and value:
            return value
    return []


def safe_number(value: Any) -> Any:
    if value is None:
        return ""
    if isinstance(value, (int, float)):
        return value
    text = str(value).strip().replace(" ", "").replace(",", ".")
    try:
        return float(text)
    except Exception:
        return str(value)


def make_hash(item: Dict[str, Any]) -> str:
    raw = "|".join([
        normalize_text(item.get("source")),
        normalize_text(item.get("lot_no")),
        normalize_text(item.get("title")),
        normalize_text(item.get("url")),
    ])
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def is_logistics_tender(title: str, description: str = "", category: str = "") -> Tuple[bool, str]:
    text = normalize_text(" ".join([title, description, category]))
    for phrase in STRONG_REJECT:
        if phrase in text:
            return False, f"rejected:{phrase}"
    for phrase in TRANSPORT_ACCEPT:
        if phrase in text:
            return True, f"accepted:{phrase}"
    return False, "rejected:no_transport_phrase"


def calculate_score(item: Dict[str, Any]) -> int:
    text = normalize_text(" ".join([
        item.get("title", ""),
        item.get("description", ""),
        item.get("category", ""),
    ]))
    score = 45
    if "международ" in text or "xalqaro" in text:
        score += 20
    if "экспед" in text or "ekspeditor" in text or "логист" in text:
        score += 15
    if item.get("amount") not in ("", None, 0):
        score += 10
    if item.get("end_date"):
        score += 5
    if item.get("customer"):
        score += 5
    return min(score, 100)


def priority_from_score(score: int) -> str:
    if score >= 80:
        return "Высокий"
    if score >= 60:
        return "Средний"
    return "Низкий"


def extract_route(text: str) -> str:
    text = str(text or "")
    patterns = [
        r"(?:из|от)\s+([А-ЯA-ZЁЎҚҒҲ][^,.;\n]{2,50})\s+(?:в|до)\s+([А-ЯA-ZЁЎҚҒҲ][^,.;\n]{2,50})",
        r"([A-ZА-ЯЁЎҚҒҲ][\w\- ]{2,40})\s*[–—-]\s*([A-ZА-ЯЁЎҚҒҲ][\w\- ]{2,40})",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            return f"{match.group(1).strip()} → {match.group(2).strip()}"
    return ""


def infer_transport(text: str) -> str:
    t = normalize_text(text)
    mapping = [
        ("рефриж", "Рефрижератор"),
        ("изотерм", "Изотерм"),
        ("тент", "Тент"),
        ("самосвал", "Самосвал"),
        ("цистерн", "Цистерна"),
        ("контейнер", "Контейнеровоз"),
        ("авиаперевоз", "Авиа"),
        ("темир йўл", "Железнодорожный"),
        ("железнодорож", "Железнодорожный"),
    ]
    found = [label for token, label in mapping if token in t]
    return ", ".join(dict.fromkeys(found))


def default_document_checklist(text: str) -> str:
    t = normalize_text(text)
    docs = [
        "Коммерческое предложение",
        "Реквизиты компании",
        "Свидетельство о регистрации",
        "Устав",
        "Доверенность/приказ подписанта",
        "Опыт аналогичных перевозок",
        "Список транспорта",
        "Данные водителей",
    ]
    if "международ" in t or "xalqaro" in t:
        docs.extend(["Лицензии/разрешения", "CMR/TIR документы"])
    return "; ".join(docs)


def document_warnings(item: Dict[str, Any]) -> str:
    warnings: List[str] = []
    if not item.get("end_date"):
        warnings.append("Не определён срок подачи")
    if not item.get("amount"):
        warnings.append("Не определена сумма")
    if not item.get("description"):
        warnings.append("Нужно открыть ТЗ и договор")
    return "; ".join(warnings) or "Нет критических предупреждений"


def responsible_tasks(item: Dict[str, Any]) -> str:
    return (
        "Тендерный менеджер: проверить требования и подать заявку; "
        "Логист: рассчитать маршрут, транспорт и себестоимость; "
        "Бухгалтерия: проверить налоги, обеспечение и оплату; "
        "Директор: утвердить цену и участие"
    )


def parse_trade(raw: Dict[str, Any]) -> Dict[str, Any]:
    lot_id = deep_get(
        raw,
        "id", "tradeId", "lotId", "Id", "TradeId", "LotId",
        "trade.id", "lot.id",
        default="",
    )
    lot_no = deep_get(
        raw,
        "displayNo", "tradeNo", "lotNo", "number", "DisplayNo",
        "TradeNo", "LotNo", "id",
        default=lot_id,
    )
    title = deep_get(
        raw,
        "nameRu", "titleRu", "tradeNameRu", "lotNameRu",
        "name", "title", "Name", "Title",
        "trade.name", "lot.name",
        default="",
    )
    customer = deep_get(
        raw,
        "customerName", "organizationName", "buyerName",
        "CustomerName", "OrganizationName",
        "customer.name", "organization.name",
        default="",
    )
    amount = deep_get(
        raw,
        "startPrice", "amount", "price", "maxPrice",
        "StartPrice", "Amount", "Price",
        default="",
    )
    currency = deep_get(
        raw,
        "currencyName", "currency", "CurrencyName",
        "currency.code", "currency.name",
        default="UZS",
    )
    start_date = deep_get(
        raw,
        "startDate", "publishDate", "createdDate",
        "StartDate", "PublishDate",
        default="",
    )
    end_date = deep_get(
        raw,
        "endDate", "deadline", "finishDate",
        "EndDate", "Deadline",
        default="",
    )
    category = deep_get(
        raw,
        "categoryName", "tradeCategoryName", "classifierName",
        "CategoryName", "category.name",
        default="",
    )
    description = deep_get(
        raw,
        "descriptionRu", "description", "technicalDescription",
        "DescriptionRu", "Description",
        default="",
    )
    payment = deep_get(
        raw,
        "paymentCondition", "paymentTerms", "PaymentCondition",
        default="",
    )
    payment_days = deep_get(
        raw,
        "termPaymentDays", "paymentDays", "TermPaymentDays",
        default="",
    )
    delivery_days = deep_get(
        raw,
        "deliveryTermDays", "termDays", "DeliveryTermDays",
        default="",
    )

    url = deep_get(raw, "url", "link", "Url", "Link", default="")
    if not url and lot_id:
        url = f"{UZEX_BASE_URL}/lot/{lot_id}"

    combined = " ".join([str(title), str(description), str(category)])
    item = {
        "source": "UZEX",
        "lot_id": str(lot_id or ""),
        "lot_no": str(lot_no or ""),
        "title": str(title or "").strip(),
        "customer": str(customer or "").strip(),
        "amount": safe_number(amount),
        "currency": str(currency or "UZS"),
        "start_date": str(start_date or ""),
        "end_date": str(end_date or ""),
        "category": str(category or ""),
        "description": str(description or ""),
        "payment": str(payment or ""),
        "payment_days": str(payment_days or ""),
        "delivery_days": str(delivery_days or ""),
        "route": extract_route(combined),
        "transport_type": infer_transport(combined),
        "url": str(url or ""),
        "raw": raw,
    }
    item["hash"] = make_hash(item)
    return item


async def http_json(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    *,
    params: Optional[Dict[str, Any]] = None,
    json_body: Optional[Dict[str, Any]] = None,
) -> Any:
    last_error: Optional[Exception] = None
    for attempt in range(1, 4):
        try:
            response = await client.request(
                method,
                url,
                params=params,
                json=json_body,
                headers={
                    "Accept": "application/json, text/plain, */*",
                    "User-Agent": "Mozilla/5.0 CargoV31/1.0",
                    "Content-Type": "application/json",
                },
            )
            response.raise_for_status()
            return response.json()
        except Exception as exc:
            last_error = exc
            if attempt < 3:
                await asyncio.sleep(attempt * 1.5)
    raise RuntimeError(f"HTTP request failed: {url}: {last_error}")


def trade_list_payload(page: int) -> Dict[str, Any]:
    return {
        "page": page,
        "pageNumber": page,
        "pageSize": UZEX_PAGE_SIZE,
        "size": UZEX_PAGE_SIZE,
        "search": "",
        "status": 0,
        "sort": "desc",
    }


async def fetch_uzex_list(client: httpx.AsyncClient) -> List[Dict[str, Any]]:
    all_rows: List[Dict[str, Any]] = []
    seen_ids: set[str] = set()

    for page in range(1, MAX_PAGES + 1):
        payload = trade_list_payload(page)
        errors: List[str] = []
        data = None

        for method in ("POST", "GET"):
            try:
                if method == "POST":
                    data = await http_json(
                        client,
                        "POST",
                        UZEX_TRADE_LIST_URL,
                        json_body=payload,
                    )
                else:
                    data = await http_json(
                        client,
                        "GET",
                        UZEX_TRADE_LIST_URL,
                        params=payload,
                    )
                break
            except Exception as exc:
                errors.append(f"{method}: {exc}")

        if data is None:
            raise RuntimeError("; ".join(errors))

        rows = first_list(data)
        logger.info("UZEX page %s: %s rows", page, len(rows))

        if not rows:
            break

        added = 0
        for raw in rows:
            if not isinstance(raw, dict):
                continue
            key = str(
                deep_get(raw, "id", "tradeId", "lotId", "displayNo", default="")
            )
            if not key:
                key = hashlib.md5(
                    json.dumps(raw, ensure_ascii=False, sort_keys=True).encode("utf-8")
                ).hexdigest()
            if key in seen_ids:
                continue
            seen_ids.add(key)
            all_rows.append(raw)
            added += 1

        if added == 0 or len(rows) < UZEX_PAGE_SIZE:
            break

    return all_rows


async def enrich_trade(client: httpx.AsyncClient, item: Dict[str, Any]) -> Dict[str, Any]:
    lot_id = item.get("lot_id")
    if not lot_id:
        return item

    attempts = [
        ("GET", {"id": lot_id}, None),
        ("GET", {"tradeId": lot_id}, None),
        ("POST", None, {"id": lot_id}),
        ("POST", None, {"tradeId": lot_id}),
    ]
    for method, params, body in attempts:
        try:
            data = await http_json(
                client,
                method,
                UZEX_GET_TRADE_URL,
                params=params,
                json_body=body,
            )
            if isinstance(data, dict):
                candidates = [data]
                for key in ("data", "result", "trade", "lot", "Data", "Result"):
                    if isinstance(data.get(key), dict):
                        candidates.insert(0, data[key])
                merged = dict(item["raw"])
                merged.update(candidates[0])
                enriched = parse_trade(merged)
                for key, value in item.items():
                    if key not in enriched or enriched[key] in ("", None):
                        enriched[key] = value
                return enriched
        except Exception:
            continue
    return item


def read_known_hashes_from_state() -> set[str]:
    try:
        file = DATA_DIR / "known_hashes.json"
        if file.exists():
            values = json.loads(file.read_text(encoding="utf-8"))
            return set(map(str, values))
    except Exception:
        pass
    return set()


def save_known_hashes_to_state(values: Iterable[str]) -> None:
    try:
        file = DATA_DIR / "known_hashes.json"
        file.write_text(
            json.dumps(sorted(set(values)), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception as exc:
        logger.warning("Could not save hashes: %s", exc)


def get_gspread_client():
    if not GOOGLE_SHEET_ID or not GOOGLE_SERVICE_ACCOUNT_JSON:
        return None
    if gspread is None or Credentials is None:
        raise RuntimeError("gspread/google-auth are not installed")

    try:
        credentials_info = json.loads(GOOGLE_SERVICE_ACCOUNT_JSON)
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            "GOOGLE_SERVICE_ACCOUNT_JSON must contain complete JSON"
        ) from exc

    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]
    credentials = Credentials.from_service_account_info(
        credentials_info,
        scopes=scopes,
    )
    return gspread.authorize(credentials)


def open_worksheet():
    client = get_gspread_client()
    if client is None:
        return None

    spreadsheet = client.open_by_key(GOOGLE_SHEET_ID)
    try:
        worksheet = spreadsheet.worksheet(SHEET_WORKSHEET_NAME)
    except Exception:
        worksheet = spreadsheet.sheet1

    first_row = worksheet.row_values(1)
    if first_row != SHEET_HEADERS:
        if not first_row:
            worksheet.append_row(SHEET_HEADERS, value_input_option="USER_ENTERED")
        else:
            existing = list(first_row)
            missing = [h for h in SHEET_HEADERS if h not in existing]
            if missing:
                worksheet.update(
                    range_name=f"A1:{column_letter(len(existing) + len(missing))}1",
                    values=[existing + missing],
                )
    return worksheet


def column_letter(number: int) -> str:
    result = ""
    while number:
        number, remainder = divmod(number - 1, 26)
        result = chr(65 + remainder) + result
    return result


def load_known_hashes_from_sheet_sync() -> set[str]:
    worksheet = open_worksheet()
    if worksheet is None:
        return set()

    try:
        headers = worksheet.row_values(1)
        if "Хеш" not in headers:
            return set()
        index = headers.index("Хеш") + 1
        values = worksheet.col_values(index)[1:]
        return {str(v).strip() for v in values if str(v).strip()}
    except Exception as exc:
        logger.warning("Could not read hashes from Google Sheets: %s", exc)
        return set()


def item_to_sheet_row(item: Dict[str, Any]) -> List[Any]:
    score = calculate_score(item)
    combined = " ".join([
        str(item.get("title", "")),
        str(item.get("description", "")),
        str(item.get("category", "")),
    ])
    return [
        item.get("lot_id") or item.get("lot_no"),
        item.get("source"),
        item.get("lot_no"),
        item.get("title"),
        item.get("customer"),
        item.get("amount"),
        item.get("currency"),
        item.get("start_date"),
        item.get("end_date"),
        item.get("category"),
        item.get("description"),
        item.get("route"),
        item.get("transport_type"),
        item.get("payment"),
        item.get("payment_days"),
        item.get("delivery_days"),
        default_document_checklist(combined),
        document_warnings(item),
        responsible_tasks(item),
        priority_from_score(score),
        score,
        "Участвовать" if score >= 60 else "Проверить",
        "Новый",
        item.get("url"),
        pretty_now(),
        item.get("filter_reason"),
        item.get("hash"),
    ]


def append_items_to_sheet_sync(items: List[Dict[str, Any]]) -> int:
    if not items:
        return 0
    worksheet = open_worksheet()
    if worksheet is None:
        return 0

    rows = [item_to_sheet_row(item) for item in items]
    worksheet.append_rows(rows, value_input_option="USER_ENTERED")
    return len(rows)


async def send_telegram_message(client: httpx.AsyncClient, text: str) -> bool:
    if not BOT_TOKEN or not CHAT_ID:
        return False

    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": CHAT_ID,
        "text": text[:4096],
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    try:
        response = await client.post(url, json=payload)
        response.raise_for_status()
        data = response.json()
        return bool(data.get("ok"))
    except Exception as exc:
        logger.error("Telegram send failed: %s", exc)
        return False


def telegram_text(item: Dict[str, Any]) -> str:
    score = calculate_score(item)
    amount = item.get("amount") or "не указана"
    currency = item.get("currency") or "UZS"
    end_date = item.get("end_date") or "не указан"
    customer = item.get("customer") or "не указан"
    route = item.get("route") or "нужно определить"
    transport = item.get("transport_type") or "не указан"

    def esc(value: Any) -> str:
        return (
            str(value)
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
        )

    return (
        "🚚 <b>Новый логистический тендер</b>\n\n"
        f"<b>{esc(item.get('title'))}</b>\n"
        f"Источник: {esc(item.get('source'))}\n"
        f"Заказчик: {esc(customer)}\n"
        f"Сумма: {esc(amount)} {esc(currency)}\n"
        f"Срок подачи: {esc(end_date)}\n"
        f"Маршрут: {esc(route)}\n"
        f"Транспорт: {esc(transport)}\n"
        f"AI Score: {score}/100\n"
        f"Приоритет: {esc(priority_from_score(score))}\n\n"
        f"🔗 {esc(item.get('url'))}"
    )


async def integration_health() -> Dict[str, Any]:
    errors: List[str] = []
    telegram_ok = False
    google_sheets_ok = False
    uzex_trade_list_ok = False
    uzex_get_trade_ok = False

    timeout = httpx.Timeout(REQUEST_TIMEOUT_SECONDS)
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        if BOT_TOKEN:
            try:
                response = await client.get(
                    f"https://api.telegram.org/bot{BOT_TOKEN}/getMe"
                )
                telegram_ok = response.is_success and response.json().get("ok", False)
            except Exception as exc:
                errors.append(f"telegram:{exc}")
        else:
            errors.append("telegram:BOT_TOKEN missing")

        try:
            rows = await fetch_uzex_list(client)
            uzex_trade_list_ok = isinstance(rows, list)
            if rows:
                sample = parse_trade(rows[0])
                enriched = await enrich_trade(client, sample)
                uzex_get_trade_ok = bool(enriched)
            else:
                uzex_get_trade_ok = True
        except Exception as exc:
            errors.append(f"uzex:{exc}")

    if GOOGLE_SHEET_ID and GOOGLE_SERVICE_ACCOUNT_JSON:
        try:
            worksheet = await asyncio.to_thread(open_worksheet)
            google_sheets_ok = worksheet is not None
        except Exception as exc:
            errors.append(f"google_sheets:{exc}")
    else:
        errors.append("google_sheets:variables missing")

    return {
        "status": "ok" if not errors else "degraded",
        "version": VERSION,
        "telegram": telegram_ok,
        "google_sheets": google_sheets_ok,
        "uzex_trade_list": uzex_trade_list_ok,
        "uzex_get_trade": uzex_get_trade_ok,
        "errors": errors,
    }


async def perform_scan(trigger: str) -> Dict[str, Any]:
    global active_scan_task

    if scan_lock.locked():
        return {
            "status": "already_running",
            "version": VERSION,
            "running": True,
            "started_at": state.started_at,
        }

    async with scan_lock:
        result = ScanResult(
            status="running",
            trigger=trigger,
            started_at=pretty_now(),
        )

        with state_lock:
            state.running = True
            state.started_at = result.started_at
            state.finished_at = None
            state.last_trigger = trigger
            state.last_error = None
            state.scan_count += 1
            save_state()

        start_monotonic = time.monotonic()
        logger.info("Scan started | trigger=%s", trigger)

        try:
            timeout = httpx.Timeout(REQUEST_TIMEOUT_SECONDS)
            async with httpx.AsyncClient(
                timeout=timeout,
                follow_redirects=True,
            ) as client:
                raw_rows = await fetch_uzex_list(client)
                result.sources["UZEX"] = len(raw_rows)
                result.found_total = len(raw_rows)

                parsed: List[Dict[str, Any]] = []
                for raw in raw_rows:
                    item = parse_trade(raw)
                    accepted, reason = is_logistics_tender(
                        item["title"],
                        item["description"],
                        item["category"],
                    )
                    item["filter_reason"] = reason
                    if accepted:
                        parsed.append(item)
                    else:
                        result.rejected += 1

                enriched: List[Dict[str, Any]] = []
                semaphore = asyncio.Semaphore(8)

                async def enrich_one(item: Dict[str, Any]) -> Dict[str, Any]:
                    async with semaphore:
                        return await enrich_trade(client, item)

                if parsed:
                    enriched = await asyncio.gather(
                        *(enrich_one(item) for item in parsed)
                    )

                final_items: List[Dict[str, Any]] = []
                for item in enriched:
                    accepted, reason = is_logistics_tender(
                        item["title"],
                        item["description"],
                        item["category"],
                    )
                    item["filter_reason"] = reason
                    item["hash"] = make_hash(item)
                    if accepted:
                        final_items.append(item)

                result.accepted_total = len(final_items)

                known_hashes = read_known_hashes_from_state()
                try:
                    known_hashes |= await asyncio.to_thread(
                        load_known_hashes_from_sheet_sync
                    )
                except Exception as exc:
                    result.errors.append(f"sheet_hashes:{exc}")

                new_items = [
                    item for item in final_items
                    if item["hash"] not in known_hashes
                ]
                result.new_total = len(new_items)
                result.duplicates = len(final_items) - len(new_items)

                if new_items:
                    try:
                        result.sheets_saved = await asyncio.to_thread(
                            append_items_to_sheet_sync,
                            new_items,
                        )
                    except Exception as exc:
                        result.errors.append(f"google_sheets:{exc}")
                        logger.exception("Google Sheets save failed")

                    for item in new_items:
                        sent = await send_telegram_message(
                            client,
                            telegram_text(item),
                        )
                        if sent:
                            result.telegram_sent += 1
                        await asyncio.sleep(0.15)

                    known_hashes.update(item["hash"] for item in new_items)
                    save_known_hashes_to_state(known_hashes)

                result.status = "success" if not result.errors else "partial_success"

        except asyncio.CancelledError:
            result.status = "cancelled"
            result.errors.append("scan_cancelled")
            raise
        except Exception as exc:
            result.status = "error"
            result.errors.append(str(exc))
            logger.error("Scan failed: %s", traceback.format_exc())
        finally:
            result.finished_at = pretty_now()
            result.duration_seconds = round(time.monotonic() - start_monotonic, 2)

            with state_lock:
                state.running = False
                state.finished_at = result.finished_at
                state.last_result = asdict(result)
                state.last_error = "; ".join(result.errors) if result.errors else None
                state.total_found += result.found_total
                state.total_new += result.new_total
                state.telegram_sent += result.telegram_sent
                state.sheets_saved += result.sheets_saved
                if result.status in {"success", "partial_success"}:
                    state.successful_scans += 1
                else:
                    state.failed_scans += 1
                save_state()

            logger.info(
                "Scan finished | status=%s found=%s accepted=%s new=%s duplicates=%s duration=%ss",
                result.status,
                result.found_total,
                result.accepted_total,
                result.new_total,
                result.duplicates,
                result.duration_seconds,
            )
            active_scan_task = None

        return asdict(result)


async def run_scan_with_timeout(trigger: str) -> Dict[str, Any]:
    try:
        return await asyncio.wait_for(
            perform_scan(trigger),
            timeout=SCAN_TIMEOUT_SECONDS,
        )
    except asyncio.TimeoutError:
        with state_lock:
            state.running = False
            state.finished_at = pretty_now()
            state.last_error = f"scan_timeout:{SCAN_TIMEOUT_SECONDS}s"
            state.failed_scans += 1
            save_state()
        logger.error("Scan timed out after %s seconds", SCAN_TIMEOUT_SECONDS)
        return {
            "status": "timeout",
            "version": VERSION,
            "timeout_seconds": SCAN_TIMEOUT_SECONDS,
        }


def start_scan_task(trigger: str) -> bool:
    global active_scan_task
    if active_scan_task and not active_scan_task.done():
        return False
    active_scan_task = asyncio.create_task(run_scan_with_timeout(trigger))
    return True


async def scheduler_loop() -> None:
    logger.info(
        "Internal scheduler started | interval=%s minutes",
        SCAN_INTERVAL_MINUTES,
    )
    while True:
        try:
            next_run = now_local() + timedelta(minutes=SCAN_INTERVAL_MINUTES)
            with state_lock:
                state.next_scheduled_run = next_run.isoformat(timespec="seconds")
                save_state()

            await asyncio.sleep(SCAN_INTERVAL_MINUTES * 60)

            if state.running and state.started_at:
                try:
                    started = datetime.strptime(
                        state.started_at,
                        "%d.%m.%Y %H:%M:%S",
                    ).replace(tzinfo=TZ)
                    if now_local() - started > timedelta(minutes=STUCK_SCAN_MINUTES):
                        logger.warning("Stuck scan flag reset")
                        with state_lock:
                            state.running = False
                            state.last_error = "stuck_scan_flag_reset"
                            save_state()
                except Exception:
                    pass

            if not state.running:
                start_scan_task("internal_scheduler")
            else:
                logger.info("Scheduled scan skipped: another scan is running")

        except asyncio.CancelledError:
            logger.info("Scheduler stopped")
            raise
        except Exception:
            logger.exception("Scheduler loop error")
            await asyncio.sleep(60)


@asynccontextmanager
async def lifespan(app: FastAPI):
    global scheduler_task
    load_state()
    logger.info("%s starting", APP_NAME)

    scheduler_task = asyncio.create_task(scheduler_loop())

    if SCAN_ON_STARTUP:
        await asyncio.sleep(3)
        start_scan_task("startup")

    yield

    if scheduler_task:
        scheduler_task.cancel()
        try:
            await scheduler_task
        except asyncio.CancelledError:
            pass

    if active_scan_task and not active_scan_task.done():
        active_scan_task.cancel()

    logger.info("%s stopped", APP_NAME)


app = FastAPI(
    title=APP_NAME,
    version=VERSION,
    lifespan=lifespan,
)


@app.get("/")
async def root() -> Dict[str, Any]:
    return {
        "status": APP_NAME + " is running",
        "version": VERSION,
        "running": state.running,
        "scan_interval_minutes": SCAN_INTERVAL_MINUTES,
        "endpoints": [
            "/health",
            "/version",
            "/scan",
            "/scan_status",
            "/metrics",
            "/logs",
            "/docs",
        ],
    }


@app.get("/version")
async def version() -> Dict[str, str]:
    return {"version": VERSION}


@app.get("/health")
async def health(deep: bool = Query(False)) -> Dict[str, Any]:
    basic = {
        "status": "ok",
        "version": VERSION,
        "service": APP_NAME,
        "running": state.running,
        "app_started_at": state.app_started_at,
        "time": pretty_now(),
    }
    if not deep:
        return basic
    detailed = await integration_health()
    return {**basic, **detailed}


@app.api_route("/scan", methods=["GET", "POST"])
async def scan(background_tasks: BackgroundTasks) -> Dict[str, Any]:
    started = start_scan_task("manual_http")
    if not started:
        return {
            "status": "already_running",
            "version": VERSION,
            "running": True,
            "started_at": state.started_at,
            "message": "Scan is already running. Check /scan_status.",
        }

    return {
        "status": "accepted",
        "version": VERSION,
        "running": True,
        "started_at": pretty_now(),
        "message": "Scan started. Check /scan_status.",
    }


@app.get("/scan_status")
async def scan_status() -> Dict[str, Any]:
    with state_lock:
        return {
            "status": "ok",
            "version": VERSION,
            "running": state.running,
            "started_at": state.started_at,
            "finished_at": state.finished_at,
            "last_trigger": state.last_trigger,
            "last_result": state.last_result,
            "last_error": state.last_error,
            "next_scheduled_run": state.next_scheduled_run,
        }


@app.get("/metrics")
async def metrics() -> Dict[str, Any]:
    uptime = now_local() - datetime.fromisoformat(state.app_started_at)
    with state_lock:
        return {
            "version": VERSION,
            "uptime_seconds": int(uptime.total_seconds()),
            "running": state.running,
            "scan_interval_minutes": SCAN_INTERVAL_MINUTES,
            "scan_timeout_seconds": SCAN_TIMEOUT_SECONDS,
            "scan_count": state.scan_count,
            "successful_scans": state.successful_scans,
            "failed_scans": state.failed_scans,
            "success_rate_percent": round(
                (state.successful_scans / state.scan_count * 100)
                if state.scan_count else 0,
                2,
            ),
            "total_found": state.total_found,
            "total_new": state.total_new,
            "telegram_sent": state.telegram_sent,
            "sheets_saved": state.sheets_saved,
            "last_started_at": state.started_at,
            "last_finished_at": state.finished_at,
            "next_scheduled_run": state.next_scheduled_run,
            "last_error": state.last_error,
        }


@app.get("/metrics/prometheus", response_class=PlainTextResponse)
async def prometheus_metrics() -> str:
    return "\n".join([
        f'cargo_scan_running {1 if state.running else 0}',
        f'cargo_scan_count_total {state.scan_count}',
        f'cargo_scan_success_total {state.successful_scans}',
        f'cargo_scan_failed_total {state.failed_scans}',
        f'cargo_tenders_found_total {state.total_found}',
        f'cargo_tenders_new_total {state.total_new}',
        f'cargo_telegram_sent_total {state.telegram_sent}',
        f'cargo_sheets_saved_total {state.sheets_saved}',
        "",
    ])


@app.get("/logs")
async def logs(limit: int = Query(100, ge=1, le=500)) -> Dict[str, Any]:
    records = list(ring_handler.records)[-limit:]
    return {
        "version": VERSION,
        "count": len(records),
        "logs": records,
    }


@app.get("/debug/uzex")
async def debug_uzex(limit: int = Query(10, ge=1, le=100)) -> Dict[str, Any]:
    timeout = httpx.Timeout(REQUEST_TIMEOUT_SECONDS)
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        rows = await fetch_uzex_list(client)

    parsed = []
    for raw in rows[:limit]:
        item = parse_trade(raw)
        accepted, reason = is_logistics_tender(
            item["title"],
            item["description"],
            item["category"],
        )
        item["accepted"] = accepted
        item["filter_reason"] = reason
        item.pop("raw", None)
        parsed.append(item)

    return {
        "version": VERSION,
        "count": len(parsed),
        "items": parsed,
    }


@app.post("/admin/reset_stuck_scan")
async def reset_stuck_scan() -> Dict[str, Any]:
    with state_lock:
        if active_scan_task and not active_scan_task.done():
            raise HTTPException(
                status_code=409,
                detail="Active scan task is still running",
            )
        state.running = False
        state.started_at = None
        state.last_error = "manual_stuck_flag_reset"
        save_state()
    return {"status": "ok", "version": VERSION, "running": False}


@app.exception_handler(Exception)
async def unhandled_exception_handler(request, exc: Exception):
    logger.exception("Unhandled request error")
    return JSONResponse(
        status_code=500,
        content={
            "status": "error",
            "version": VERSION,
            "detail": str(exc),
        },
    )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=int(os.getenv("PORT", "10000")),
        reload=False,
    )
