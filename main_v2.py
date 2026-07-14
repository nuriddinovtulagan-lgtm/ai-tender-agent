
"""
Cargo V32 Enterprise AI
Stable AI Tender Agent for logistics tenders.

Core guarantees:
- Permanent deduplication by stable UZEX lot ID.
- TradeList + GetTrade deep merge.
- Extraction of routes, payment data, product descriptions and documents.
- Google Sheets is the source of truth for permanent deduplication.
- Local state is only a fast cache and may be recreated after a Render restart.
- Internal scheduler resumes automatically after every process restart.
- External cron may call /health or /scan; duplicate runs are protected.

Required environment variables:
BOT_TOKEN
CHAT_ID
GOOGLE_SHEET_ID
GOOGLE_SERVICE_ACCOUNT_JSON

Recommended:
SCAN_INTERVAL_MINUTES=30
SCAN_ON_STARTUP=true
UZEX_TYPE_ID=1
UZEX_SYSTEM_ID=0
"""

from __future__ import annotations

import asyncio
import hashlib
import html
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
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple
from urllib.parse import urljoin

import httpx
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import JSONResponse, PlainTextResponse

try:
    import gspread
    from google.oauth2.service_account import Credentials
except Exception:
    gspread = None
    Credentials = None


VERSION = "cargo_v34_tender_follow_up"
APP_NAME = "AI Tender Agent Cargo V34 Tender Follow-up"
TZ = timezone(timedelta(hours=5))

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = Path(os.getenv("DATA_DIR", str(BASE_DIR / "data")))
DATA_DIR.mkdir(parents=True, exist_ok=True)

STATE_FILE = DATA_DIR / "cargo_v32_state.json"
CACHE_FILE = DATA_DIR / "cargo_v32_seen_lots.json"
LOG_FILE = DATA_DIR / "cargo_v32.log"

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
CHAT_ID = os.getenv("CHAT_ID", "").strip()
GOOGLE_SHEET_ID = os.getenv("GOOGLE_SHEET_ID", "").strip()
GOOGLE_SERVICE_ACCOUNT_JSON = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON", "").strip()
SHEET_WORKSHEET_NAME = os.getenv("SHEET_WORKSHEET_NAME", "Тендеры").strip()

UZEX_API_BASE = os.getenv(
    "UZEX_API_BASE",
    "https://apietender.uzex.uz",
).rstrip("/")
UZEX_SITE_BASE = os.getenv(
    "UZEX_SITE_BASE",
    "https://etender.uzex.uz",
).rstrip("/")
UZEX_TRADE_LIST_URL = os.getenv(
    "UZEX_TRADE_LIST_URL",
    f"{UZEX_API_BASE}/api/common/TradeList",
).strip()
UZEX_GET_TRADE_URL = os.getenv(
    "UZEX_GET_TRADE_URL",
    f"{UZEX_API_BASE}/api/common/GetTrade",
).strip()
UZEX_TYPE_ID = int(os.getenv("UZEX_TYPE_ID", "1"))
UZEX_SYSTEM_ID = int(os.getenv("UZEX_SYSTEM_ID", "0"))

WARMUP_SECONDS = max(0, int(os.getenv("WARMUP_SECONDS", "45")))
SCAN_ON_STARTUP = os.getenv("SCAN_ON_STARTUP", "false").lower() in {"1", "true", "yes", "on"}
UZEX_PAGE_SIZE = max(20, int(os.getenv("UZEX_PAGE_SIZE", "100")))
MAX_PAGES = max(1, int(os.getenv("MAX_PAGES", "5")))
REQUEST_TIMEOUT_SECONDS = max(5, int(os.getenv("REQUEST_TIMEOUT_SECONDS", "30")))
SCAN_TIMEOUT_SECONDS = max(60, int(os.getenv("SCAN_TIMEOUT_SECONDS", "300")))
DETAIL_CONCURRENCY = max(1, min(12, int(os.getenv("DETAIL_CONCURRENCY", "6"))))
DETAIL_RETRIES = max(1, min(5, int(os.getenv("DETAIL_RETRIES", "3"))))
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()

REMINDER_WORKSHEET_NAME = os.getenv("REMINDER_WORKSHEET_NAME", "Системные данные").strip()
MORNING_REMINDER_HOUR = int(os.getenv("MORNING_REMINDER_HOUR", "9"))
EVENING_REMINDER_HOUR = int(os.getenv("EVENING_REMINDER_HOUR", "17"))
REMINDER_WINDOW_MINUTES = max(30, int(os.getenv("REMINDER_WINDOW_MINUTES", "90")))
DEADLINE_ALERT_DAYS = [
    int(value.strip())
    for value in os.getenv("DEADLINE_ALERT_DAYS", "7,3,1,0").split(",")
    if value.strip().lstrip("-").isdigit()
]
MAX_DIGEST_ITEMS = max(5, int(os.getenv("MAX_DIGEST_ITEMS", "20")))

ACCEPT_PHRASES = [
    "перевозка груз", "грузоперевоз", "транспортные услуги",
    "транспортная услуга", "транспортно-экспедицион",
    "экспедиторские услуги", "логистические услуги",
    "международная перевозка", "автомобильная перевозка",
    "доставка груз", "yuk tashish", "yuklarni tashish",
    "transport xizmati", "transport xizmatlari", "logistika",
    "ekspeditorlik", "xalqaro yuk", "avtomobil transport",
    "yetkazib berish bo'yicha transport", "yetkazib berish bo‘yicha transport",
]

STRONG_REJECT = [
    "приобретение автомобиля", "закупка автомобиля",
    "поставка автомобиля", "поставка автотранспорт",
    "запасные части", "запчасти", "ремонт автомобиля",
    "техническое обслуживание автомобиля", "автострахование",
    "шины", "аккумулятор", "ehtiyot qismlar",
    "avtomobil sotib olish",
]


ROUTE_EXCLUDE_PHRASES = [
    "банкрот", "банкротлик", "солиқ", "налог", "миб",
    "инсофсиз", "реестр", "квалифика", "талаб",
    "шартномани бажариш", "жорий этилган",
    "мавжуд эмаслиги", "таомил",
]

DOCUMENT_NOISE = {
    "pdf", "docx", "doc", "xls", "xlsx", "fayl", "file",
    "документ", "document", "attachment",
}


def clean_title(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def extract_best_title(raw: Dict[str, Any]) -> str:
    candidates: List[str] = []
    for obj in flatten_dicts(raw):
        for key in (
            "trade_name", "tradeName", "TradeName", "name", "Name",
            "nameRu", "NameRu", "title", "Title", "titleRu",
            "lotName", "lot_name", "subject", "Subject",
        ):
            value = obj.get(key)
            if isinstance(value, str):
                candidates.append(value)

    scored: List[Tuple[int, int, str]] = []
    for value in candidates:
        title = clean_title(value)
        if len(title) < 12:
            continue
        text = normalize_text(title)
        score = 0
        if any(phrase in text for phrase in ACCEPT_PHRASES):
            score += 100
        if 25 <= len(title) <= 300:
            score += 20
        if not any(phrase in text for phrase in ROUTE_EXCLUDE_PHRASES):
            score += 10
        scored.append((score, len(title), title))

    if not scored:
        return ""
    scored.sort(reverse=True)
    return scored[0][2]


def extract_category(raw: Dict[str, Any]) -> str:
    candidates: List[str] = []
    for obj in flatten_dicts(raw):
        for key in (
            "category_name", "categoryName", "CategoryName",
            "classifier_name", "classifierName",
            "classificationName", "productCategoryName",
            "budget_product_name", "budgetProductName",
        ):
            value = obj.get(key)
            if isinstance(value, str) and len(value.strip()) > 5:
                candidates.append(clean_title(value))
    return candidates[0] if candidates else ""


def is_route_candidate(text: str) -> bool:
    normalized = normalize_text(text)
    if len(normalized) < 12:
        return False
    if any(phrase in normalized for phrase in ROUTE_EXCLUDE_PHRASES):
        return False
    signals = (
        " sh.dan ", " dan ", "дан ", " из ", " до ", " ga ",
        "га ", "yetkazib berish", "достав", "маршрут",
    )
    return any(signal in f" {normalized} " for signal in signals)


SHEET_HEADERS = [
    "ID", "Источник", "Номер лота", "Название", "Заказчик",
    "Сумма", "Валюта", "Дата начала", "Срок окончания",
    "Категория", "Описание", "Маршруты", "Тип транспорта",
    "Оплата", "Срок оплаты", "Срок оказания услуг",
    "Документы", "Документы нужны", "Предупреждения",
    "Ответственные задачи", "Приоритет", "AI Score",
    "Решение", "Статус", "Ссылка", "Дата добавления",
    "Дата обновления", "Причина фильтра", "Stable Key",
]


def now_local() -> datetime:
    return datetime.now(TZ)


def pretty_now() -> str:
    return now_local().strftime("%d.%m.%Y %H:%M:%S")


def iso_now() -> str:
    return now_local().isoformat(timespec="seconds")


def app_uptime_seconds() -> int:
    try:
        started = datetime.fromisoformat(state.app_started_at)
        return max(0, int((now_local() - started).total_seconds()))
    except Exception:
        return 0


def is_warming_up() -> bool:
    return app_uptime_seconds() < WARMUP_SECONDS


class RingBufferHandler(logging.Handler):
    def __init__(self, capacity: int = 500):
        super().__init__()
        self.records: deque[str] = deque(maxlen=capacity)

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.records.append(self.format(record))
        except Exception:
            pass


logger = logging.getLogger("cargo_v32")
logger.setLevel(getattr(logging, LOG_LEVEL, logging.INFO))
logger.propagate = False
formatter = logging.Formatter(
    "%(asctime)s | %(levelname)s | %(message)s",
    "%Y-%m-%d %H:%M:%S",
)
ring_handler = RingBufferHandler()
ring_handler.setFormatter(formatter)

if not logger.handlers:
    console = logging.StreamHandler()
    console.setFormatter(formatter)
    logger.addHandler(console)
    try:
        file_handler = logging.FileHandler(LOG_FILE, encoding="utf-8")
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)
    except Exception:
        pass
    logger.addHandler(ring_handler)


@dataclass
class RuntimeState:
    running: bool = False
    app_started_at: str = field(default_factory=iso_now)
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
    total_accepted: int = 0
    total_new: int = 0
    total_duplicates: int = 0
    telegram_sent: int = 0
    sheets_saved: int = 0


state = RuntimeState()
state_lock = threading.RLock()
scan_lock = asyncio.Lock()
active_scan_task: Optional[asyncio.Task] = None


def save_state() -> None:
    try:
        tmp = STATE_FILE.with_suffix(".tmp")
        tmp.write_text(
            json.dumps(asdict(state), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        tmp.replace(STATE_FILE)
    except Exception as exc:
        logger.warning("State save failed: %s", exc)


def load_state() -> None:
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
        logger.info("Runtime state restored")
    except Exception as exc:
        logger.warning("State restore failed: %s", exc)


def normalize_text(value: Any) -> str:
    text = str(value or "").replace("\u00a0", " ").strip().lower()
    return re.sub(r"\s+", " ", text)


def first_nonempty(*values: Any, default: Any = "") -> Any:
    for value in values:
        if value not in (None, "", [], {}):
            return value
    return default


def deep_get(obj: Any, *paths: str, default: Any = None) -> Any:
    for path in paths:
        cur = obj
        ok = True
        for part in path.split("."):
            if isinstance(cur, dict) and part in cur:
                cur = cur[part]
            else:
                ok = False
                break
        if ok and cur not in (None, "", [], {}):
            return cur
    return default


def find_first_list(obj: Any, preferred: Sequence[str] = ()) -> List[Any]:
    if isinstance(obj, list):
        return obj
    if not isinstance(obj, dict):
        return []

    for key in preferred:
        value = obj.get(key)
        if isinstance(value, list):
            return value
        if isinstance(value, dict):
            nested = find_first_list(value, preferred)
            if nested:
                return nested

    for key in ("data", "result", "items", "rows", "trades", "lots", "content", "Data", "Result", "Items"):
        value = obj.get(key)
        if isinstance(value, list):
            return value
        if isinstance(value, dict):
            nested = find_first_list(value, preferred)
            if nested:
                return nested

    return []


def flatten_dicts(obj: Any) -> Iterable[Dict[str, Any]]:
    if isinstance(obj, dict):
        yield obj
        for value in obj.values():
            yield from flatten_dicts(value)
    elif isinstance(obj, list):
        for item in obj:
            yield from flatten_dicts(item)


def safe_number(value: Any) -> Any:
    if isinstance(value, (int, float)):
        return value
    if value in (None, ""):
        return ""
    text = re.sub(r"[^\d,.\-]", "", str(value))
    if not text:
        return str(value)
    text = text.replace(",", ".")
    try:
        return float(text)
    except Exception:
        return str(value)


def stable_key(item: Dict[str, Any]) -> str:
    lot_id = str(item.get("lot_id") or "").strip()
    lot_no = str(item.get("lot_no") or "").strip()
    if lot_id:
        return f"uzex:{lot_id}"
    if lot_no:
        return f"uzex-no:{lot_no}"
    url = str(item.get("url") or "")
    match = re.search(r"/lot/(\d+)", url)
    if match:
        return f"uzex:{match.group(1)}"
    raw = normalize_text(item.get("title"))
    return "fallback:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def merge_value(old: Any, new: Any) -> Any:
    if new in (None, "", [], {}):
        return old
    if old in (None, "", [], {}):
        return new
    if isinstance(old, list) and isinstance(new, list):
        result = []
        seen = set()
        for value in old + new:
            marker = json.dumps(value, ensure_ascii=False, sort_keys=True) if isinstance(value, (dict, list)) else str(value)
            if marker not in seen:
                seen.add(marker)
                result.append(value)
        return result
    if isinstance(old, dict) and isinstance(new, dict):
        return deep_merge(old, new)
    return new


def deep_merge(base: Dict[str, Any], detail: Dict[str, Any]) -> Dict[str, Any]:
    result = dict(base)
    for key, value in detail.items():
        result[key] = merge_value(result.get(key), value)
    return result


def detect_logistics(title: str, description: str, category: str) -> Tuple[bool, str]:
    text = normalize_text(" ".join([title, description, category]))
    for phrase in STRONG_REJECT:
        if phrase in text:
            return False, f"rejected:{phrase}"
    for phrase in ACCEPT_PHRASES:
        if phrase in text:
            return True, f"accepted:{phrase}"
    return False, "rejected:no_transport_phrase"


def extract_lot_identity(raw: Dict[str, Any]) -> Tuple[str, str]:
    lot_id = first_nonempty(
        deep_get(raw, "id", "Id", "tradeId", "TradeId", "lotId", "LotId"),
        deep_get(raw, "trade.id", "lot.id"),
    )
    lot_no = first_nonempty(
        deep_get(raw, "display_no", "displayNo", "DisplayNo"),
        deep_get(raw, "trade_no", "tradeNo", "TradeNo"),
        deep_get(raw, "lot_no", "lotNo", "LotNo"),
        lot_id,
    )
    return str(lot_id or ""), str(lot_no or "")


def extract_documents(raw: Dict[str, Any]) -> List[Dict[str, str]]:
    docs: List[Dict[str, str]] = []
    seen_names: Set[str] = set()
    seen_urls: Set[str] = set()

    def canonical_url(url: Any) -> str:
        value = str(url or "").strip()
        if not value:
            return ""
        if not value.startswith("http"):
            value = urljoin(UZEX_API_BASE + "/", value.lstrip("/"))
        return value

    def add_doc(name: Any, url: Any) -> None:
        url_s = canonical_url(url)
        name_s = str(name or "").strip()
        if not name_s and url_s:
            name_s = url_s.rsplit("/", 1)[-1]

        real_name = name_s.rsplit("/", 1)[-1].strip()
        name_key = normalize_text(real_name)
        if not real_name or name_key in DOCUMENT_NOISE:
            return
        if "." not in real_name and not re.search(r"\.(pdf|docx?|xlsx?)$", url_s, re.I):
            return
        if url_s.rstrip("/").rsplit("/", 1)[-1].lower() in DOCUMENT_NOISE:
            return
        if url_s and url_s in seen_urls:
            return
        if real_name.lower() in seen_names and not url_s:
            return

        seen_names.add(real_name.lower())
        if url_s:
            seen_urls.add(url_s)
        docs.append({"name": real_name, "url": url_s})

    tokens = (
        "tech_file", "tech_doc_file", "additional_file",
        "contract_proform_file", "contract_file",
        "expertise_file", "attachment", "document_file",
    )

    for obj in flatten_dicts(raw):
        for key, value in obj.items():
            if not any(token in str(key).lower() for token in tokens):
                continue
            if isinstance(value, str):
                add_doc(value.rsplit("/", 1)[-1], value)
            elif isinstance(value, dict):
                add_doc(
                    first_nonempty(
                        value.get("name"), value.get("fileName"),
                        value.get("filename"), value.get("originalName"),
                    ),
                    first_nonempty(
                        value.get("url"), value.get("path"),
                        value.get("filePath"), value.get("downloadUrl"),
                    ),
                )
            elif isinstance(value, list):
                for item in value:
                    if isinstance(item, str):
                        add_doc(item.rsplit("/", 1)[-1], item)
                    elif isinstance(item, dict):
                        add_doc(
                            first_nonempty(
                                item.get("name"), item.get("fileName"),
                                item.get("filename"), item.get("originalName"),
                            ),
                            first_nonempty(
                                item.get("url"), item.get("path"),
                                item.get("filePath"), item.get("downloadUrl"),
                            ),
                        )

    best: Dict[str, Dict[str, str]] = {}
    for doc in docs:
        key = doc["name"].lower()
        current = best.get(key)
        if current is None or len(doc.get("url", "")) > len(current.get("url", "")):
            best[key] = doc
    return list(best.values())



def extract_product_texts(raw: Dict[str, Any]) -> List[str]:
    texts: List[str] = []
    seen: Set[str] = set()
    for obj in flatten_dicts(raw):
        for key in ("description", "Description", "descriptionRu", "name", "Name", "productName", "address", "deliveryAddress"):
            value = obj.get(key)
            if isinstance(value, str):
                text = re.sub(r"\s+", " ", value).strip()
                if len(text) >= 8 and text not in seen:
                    seen.add(text)
                    texts.append(text)
    return texts


def extract_routes(texts: Sequence[str]) -> List[str]:
    routes: List[str] = []
    seen: Set[str] = set()

    patterns = [
        r"(?P<from>[A-ZА-ЯЁЎҚҒҲO‘ʻ'][^.;\n]{2,80}?)\s+(?:sh\.dan|дан|dan|из)\s+(?P<to>[A-ZА-ЯЁЎҚҒҲO‘ʻ'][^.;\n]{2,100}?)(?:\s+(?:sh\.ga|ga|га|до|в)\b|[,.;])",
        r"(?P<from>[A-ZА-ЯЁЎҚҒҲO‘ʻ'][^.;\n]{2,60})\s*[–—-]\s*(?P<to>[A-ZА-ЯЁЎҚҒҲO‘ʻ'][^.;\n]{2,60})",
    ]

    for text in texts:
        cleaned = clean_title(text)
        if not is_route_candidate(cleaned):
            continue

        found_here = False
        for pattern in patterns:
            for match in re.finditer(pattern, cleaned, flags=re.IGNORECASE):
                origin = match.group("from").strip(" ,.;:-")
                destination = match.group("to").strip(" ,.;:-")
                route = f"{origin} → {destination}"
                if any(p in normalize_text(route) for p in ROUTE_EXCLUDE_PHRASES):
                    continue
                if len(origin) < 2 or len(destination) < 2:
                    continue
                if route not in seen:
                    seen.add(route)
                    routes.append(route[:240])
                    found_here = True

        if not found_here and "yetkazib berish" in normalize_text(cleaned):
            if cleaned not in seen:
                seen.add(cleaned)
                routes.append(cleaned[:320])

    return routes[:20]



def infer_transport(text: str) -> List[str]:
    mapping = [
        ("рефриж", "Рефрижератор"), ("изотерм", "Изотерм"),
        ("тент", "Тент"), ("самосвал", "Самосвал"),
        ("цистерн", "Цистерна"), ("контейнер", "Контейнеровоз"),
        ("авиа", "Авиа"), ("железнодорож", "Железнодорожный"),
        ("темир йўл", "Железнодорожный"),
        ("трактор", "Трал/низкорамный транспорт"),
        ("техника", "Трал/низкорамный транспорт"),
        ("tentli fura", "Тентованная фура"),
    ]
    t = normalize_text(text)
    found: List[str] = []
    for token, name in mapping:
        if token in t and name not in found:
            found.append(name)
    return found


def parse_trade(raw: Dict[str, Any]) -> Dict[str, Any]:
    lot_id, lot_no = extract_lot_identity(raw)
    title = extract_best_title(raw)

    customer = first_nonempty(
        deep_get(raw, "customer_name", "customerName", "CustomerName"),
        deep_get(raw, "organization_name", "organizationName"),
        deep_get(raw, "buyer_name", "buyerName"),
        deep_get(raw, "customer.name", "organization.name"),
    )
    amount = first_nonempty(
        deep_get(raw, "start_cost", "startCost", "StartCost"),
        deep_get(raw, "start_price", "startPrice", "StartPrice"),
        deep_get(raw, "amount", "Amount", "price", "Price"),
    )
    currency = first_nonempty(
        deep_get(raw, "currency_name", "currencyName", "CurrencyName"),
        deep_get(raw, "currency.code", "currency.name", "currency"),
        default="UZS",
    )
    start_date = first_nonempty(
        deep_get(raw, "start_date", "startDate", "StartDate"),
        deep_get(raw, "publish_date", "publishDate", "createdDate"),
    )
    end_date = first_nonempty(
        deep_get(raw, "end_date", "endDate", "EndDate"),
        deep_get(raw, "deadline", "Deadline", "finishDate"),
    )
    payment = first_nonempty(
        deep_get(raw, "payment_type_name", "paymentTypeName"),
        deep_get(raw, "payment_condition", "paymentCondition"),
        deep_get(raw, "payment_terms", "paymentTerms"),
    )
    payment_days = first_nonempty(
        deep_get(raw, "term_payment_days", "termPaymentDays", "paymentDays"),
    )
    delivery_days = first_nonempty(
        deep_get(raw, "delivery_term_days", "deliveryTermDays", "termDays"),
    )
    category = extract_category(raw)

    product_texts = extract_product_texts(raw)
    description = ""
    for value in [
        deep_get(raw, "description", "Description", "descriptionRu"),
        deep_get(raw, "technical_description", "technicalDescription"),
        *product_texts,
    ]:
        candidate = clean_title(value)
        if len(candidate) >= 10 and not any(
            p in normalize_text(candidate) for p in ROUTE_EXCLUDE_PHRASES
        ):
            description = candidate
            break

    documents = extract_documents(raw)
    routes = extract_routes([t for t in product_texts if is_route_candidate(t)])

    combined = " ".join([title, description] + product_texts)
    transport_types = infer_transport(combined)
    if any(token in normalize_text(combined) for token in (
        "qishloq xo'jaligi texnika", "qishloq xo‘jaligi texnika",
        "трактор", "сельскохозяйственной техник",
    )):
        for name in ("Трал/низкорамный транспорт", "Тентованная фура"):
            if name not in transport_types:
                transport_types.append(name)

    url = first_nonempty(deep_get(raw, "url", "Url", "link", "Link"))
    if not url and lot_id:
        url = f"{UZEX_SITE_BASE}/lot/{lot_id}"

    item = {
        "source": "UZEX",
        "lot_id": lot_id,
        "lot_no": lot_no,
        "title": title,
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
        "routes": routes,
        "transport_types": transport_types,
        "documents": documents,
        "url": str(url or ""),
        "raw": raw,
    }
    item["stable_key"] = stable_key(item)
    return item



def completeness_score(item: Dict[str, Any]) -> int:
    important = ("title", "customer", "amount", "end_date", "category", "description")
    return sum(1 for key in important if item.get(key))


def ai_score(item: Dict[str, Any]) -> int:
    accepted, _ = detect_logistics(
        item.get("title", ""),
        item.get("description", ""),
        item.get("category", ""),
    )
    if not accepted:
        return 0

    score = 35
    text = normalize_text(" ".join([
        item.get("title", ""), item.get("description", ""), item.get("category", "")
    ]))
    if item.get("customer"): score += 10
    if item.get("amount") not in ("", None, 0): score += 10
    if item.get("end_date"): score += 10
    if item.get("routes"): score += 15
    if item.get("documents"): score += 10
    if "международ" in text or "xalqaro" in text: score += 10
    return min(score, 100)



def priority(score: int) -> str:
    if score >= 80:
        return "Высокий"
    if score >= 60:
        return "Средний"
    return "Низкий"


def document_checklist(item: Dict[str, Any]) -> str:
    text = normalize_text(item.get("title", "") + " " + item.get("description", ""))
    docs = [
        "Коммерческое предложение", "Реквизиты",
        "Свидетельство о регистрации", "Устав",
        "Доверенность/приказ подписанта", "Опыт аналогичных перевозок",
        "Список транспорта", "Данные водителей",
    ]
    if "международ" in text or "xalqaro" in text:
        docs.extend(["CMR/TIR", "Международные разрешения"])
    if "трактор" in text or "техника" in text:
        docs.extend(["Документы на трал", "Разрешение на негабарит при необходимости"])
    return "; ".join(docs)


def warnings_for(item: Dict[str, Any]) -> str:
    warnings = []
    if not item.get("customer"): warnings.append("Не определён заказчик")
    if not item.get("amount"): warnings.append("Не определена сумма")
    if not item.get("end_date"): warnings.append("Не определён срок подачи")
    if not item.get("routes"): warnings.append("Маршрут требует ручной проверки")
    if not item.get("documents"): warnings.append("Документы UZEX не обнаружены")
    return "; ".join(warnings) or "Нет критических предупреждений"


def responsible_tasks() -> str:
    return (
        "Тендерный менеджер: изучить ТЗ и договор; "
        "Логист: проверить маршруты, транспорт и себестоимость; "
        "Бухгалтерия: проверить оплату, налоги и обеспечение; "
        "Директор: утвердить цену и участие"
    )



def parse_deadline(value: Any) -> Optional[datetime]:
    text = str(value or "").strip()
    if not text:
        return None
    cleaned = text.replace("Z", "").split("+")[0]
    for fmt in (
        "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S",
        "%d.%m.%Y %H:%M:%S", "%d.%m.%Y %H:%M",
        "%Y-%m-%d", "%d.%m.%Y",
    ):
        try:
            return datetime.strptime(cleaned, fmt).replace(tzinfo=TZ)
        except ValueError:
            pass
    try:
        parsed = datetime.fromisoformat(cleaned)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=TZ)
        return parsed.astimezone(TZ)
    except Exception:
        return None


def format_amount(value: Any) -> str:
    if value in (None, ""):
        return "не указана"
    try:
        number = float(str(value).replace(" ", "").replace(",", "."))
        return f"{number:,.0f}".replace(",", " ")
    except Exception:
        return str(value)


def reminder_window_open(target_hour: int) -> bool:
    now = now_local()
    target = now.replace(hour=target_hour, minute=0, second=0, microsecond=0)
    delta_minutes = (now - target).total_seconds() / 60
    return 0 <= delta_minutes <= REMINDER_WINDOW_MINUTES


def get_or_create_reminder_worksheet():
    client = get_gspread_client()
    if client is None:
        return None
    spreadsheet = client.open_by_key(GOOGLE_SHEET_ID)
    try:
        worksheet = spreadsheet.worksheet(REMINDER_WORKSHEET_NAME)
    except Exception:
        worksheet = spreadsheet.add_worksheet(
            title=REMINDER_WORKSHEET_NAME,
            rows=2000,
            cols=4,
        )
        worksheet.append_row(
            ["Ключ", "Значение", "Дата обновления", "Описание"],
            value_input_option="USER_ENTERED",
        )
    return worksheet


def load_reminder_registry_sync() -> Dict[str, str]:
    worksheet = get_or_create_reminder_worksheet()
    if worksheet is None:
        return {}
    values = worksheet.get_all_values()
    registry: Dict[str, str] = {}
    for row in values[1:]:
        key = row[0].strip() if len(row) > 0 else ""
        value = row[1].strip() if len(row) > 1 else ""
        if key:
            registry[key] = value
    return registry


def save_reminder_registry_entry_sync(key: str, value: str, description: str) -> None:
    worksheet = get_or_create_reminder_worksheet()
    if worksheet is None:
        return
    values = worksheet.get_all_values()
    for row_number, row in enumerate(values[1:], start=2):
        if row and row[0].strip() == key:
            worksheet.update(
                range_name=f"A{row_number}:D{row_number}",
                values=[[key, value, pretty_now(), description]],
            )
            return
    worksheet.append_row(
        [key, value, pretty_now(), description],
        value_input_option="USER_ENTERED",
    )


def load_active_tenders_sync() -> List[Dict[str, Any]]:
    worksheet = open_worksheet()
    if worksheet is None:
        return []
    values = worksheet.get_all_values()
    if not values:
        return []

    headers = values[0]
    index = {header: i for i, header in enumerate(headers)}

    def cell(row: List[str], *names: str) -> str:
        for name in names:
            position = index.get(name)
            if position is not None and position < len(row):
                value = row[position].strip()
                if value:
                    return value
        return ""

    active: List[Dict[str, Any]] = []
    now = now_local()

    for row_number, row in enumerate(values[1:], start=2):
        status = normalize_text(cell(row, "Статус"))
        if any(token in status for token in ("заверш", "закрыт", "отказ", "архив", "проигран")):
            continue

        deadline_raw = cell(row, "Срок окончания", "Окончание")
        deadline = parse_deadline(deadline_raw)
        if deadline is None or deadline < now - timedelta(hours=12):
            continue

        lot_id = cell(row, "ID", "Номер лота")
        url = cell(row, "Ссылка")
        stable = cell(row, "Stable Key")
        if not stable:
            lot_from_any = normalize_sheet_lot_id(lot_id) or normalize_sheet_lot_id(url)
            stable = f"uzex:{lot_from_any}" if lot_from_any else f"sheet-row:{row_number}"

        active.append({
            "stable_key": stable,
            "lot_id": lot_id,
            "lot_no": cell(row, "Номер лота"),
            "title": cell(row, "Название"),
            "customer": cell(row, "Заказчик"),
            "amount": cell(row, "Сумма"),
            "currency": cell(row, "Валюта") or "UZS",
            "deadline": deadline,
            "priority": cell(row, "Приоритет"),
            "status": cell(row, "Статус") or "Новый",
            "url": url,
        })

    active.sort(key=lambda item: item["deadline"])
    return active


async def send_plain_telegram(client: httpx.AsyncClient, text: str) -> bool:
    if not BOT_TOKEN or not CHAT_ID:
        return False
    response = await client.post(
        f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
        json={
            "chat_id": CHAT_ID,
            "text": text[:4096],
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        },
    )
    response.raise_for_status()
    return bool(response.json().get("ok"))


def tender_digest_text(tenders: List[Dict[str, Any]], period_name: str) -> str:
    now = now_local()
    lines = [
        f"📊 <b>{period_name}</b>",
        "",
        f"Активных тендеров: <b>{len(tenders)}</b>",
        "",
    ]
    for number, tender in enumerate(tenders[:MAX_DIGEST_ITEMS], start=1):
        days_left = max(0, (tender["deadline"].date() - now.date()).days)
        lines.extend([
            f"{number}️⃣ <b>{html.escape(tender.get('title') or 'Без названия')}</b>",
            f"🏢 {html.escape(tender.get('customer') or 'Заказчик не указан')}",
            f"💰 {html.escape(format_amount(tender.get('amount')))} {html.escape(tender.get('currency') or 'UZS')}",
            f"⏳ Осталось дней: <b>{days_left}</b>",
            f"📅 Окончание: {tender['deadline'].strftime('%d.%m.%Y %H:%M')}",
            f"🎯 Приоритет: {html.escape(tender.get('priority') or 'Не указан')}",
            f"📌 Статус: {html.escape(tender.get('status') or 'Новый')}",
        ])
        if tender.get("url"):
            lines.append(f"🔗 {html.escape(tender['url'])}")
        lines.append("")
    if not tenders:
        lines.append("Активных тендеров с будущим сроком окончания нет.")
    return "\n".join(lines)


def deadline_alert_text(tender: Dict[str, Any], days_left: int) -> str:
    if days_left == 0:
        heading = "⛔ <b>ПОСЛЕДНИЙ ДЕНЬ ПОДАЧИ</b>"
    elif days_left == 1:
        heading = "🚨 <b>СРОЧНО: ОСТАЛСЯ 1 ДЕНЬ</b>"
    else:
        heading = f"⏰ <b>До окончания осталось {days_left} дня/дней</b>"

    lines = [
        heading,
        "",
        f"<b>{html.escape(tender.get('title') or 'Без названия')}</b>",
        f"🏢 {html.escape(tender.get('customer') or 'Заказчик не указан')}",
        f"💰 {html.escape(format_amount(tender.get('amount')))} {html.escape(tender.get('currency') or 'UZS')}",
        f"📅 Окончание: {tender['deadline'].strftime('%d.%m.%Y %H:%M')}",
        "",
        "Проверьте:",
        "• коммерческое предложение;",
        "• квалификационные документы;",
        "• транспорт и маршрут;",
        "• окончательную цену;",
        "• подачу заявки на UZEX.",
    ]
    if tender.get("url"):
        lines.extend(["", f"🔗 {html.escape(tender['url'])}"])
    return "\n".join(lines)


async def process_follow_up_reminders(client: httpx.AsyncClient) -> Dict[str, Any]:
    result = {
        "morning_digest_sent": False,
        "evening_digest_sent": False,
        "deadline_alerts_sent": 0,
        "active_tenders": 0,
        "errors": [],
    }
    try:
        tenders = await asyncio.to_thread(load_active_tenders_sync)
        registry = await asyncio.to_thread(load_reminder_registry_sync)
        result["active_tenders"] = len(tenders)
        today = now_local().strftime("%Y-%m-%d")

        if reminder_window_open(MORNING_REMINDER_HOUR):
            key = f"digest:morning:{today}"
            if key not in registry and await send_plain_telegram(
                client,
                tender_digest_text(tenders, "Утренний отчёт AI Tender Agent"),
            ):
                await asyncio.to_thread(
                    save_reminder_registry_entry_sync,
                    key, "sent", "Утренняя сводка активных тендеров",
                )
                registry[key] = "sent"
                result["morning_digest_sent"] = True

        if reminder_window_open(EVENING_REMINDER_HOUR):
            key = f"digest:evening:{today}"
            if key not in registry and await send_plain_telegram(
                client,
                tender_digest_text(tenders, "Вечерний отчёт AI Tender Agent"),
            ):
                await asyncio.to_thread(
                    save_reminder_registry_entry_sync,
                    key, "sent", "Вечерняя сводка активных тендеров",
                )
                registry[key] = "sent"
                result["evening_digest_sent"] = True

        current_date = now_local().date()
        for tender in tenders:
            days_left = (tender["deadline"].date() - current_date).days
            if days_left not in DEADLINE_ALERT_DAYS:
                continue
            key = f"deadline:{tender['stable_key']}:{tender['deadline'].date().isoformat()}:{days_left}"
            if key in registry:
                continue
            if await send_plain_telegram(client, deadline_alert_text(tender, days_left)):
                await asyncio.to_thread(
                    save_reminder_registry_entry_sync,
                    key, "sent", f"Напоминание за {days_left} дней",
                )
                registry[key] = "sent"
                result["deadline_alerts_sent"] += 1
                await asyncio.sleep(0.2)
    except Exception as exc:
        result["errors"].append(str(exc))
        logger.exception("Follow-up reminder processing failed")
    return result


async def http_json(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    *,
    params: Optional[Dict[str, Any]] = None,
    json_body: Optional[Dict[str, Any]] = None,
    retries: int = 3,
) -> Any:
    last_error: Optional[Exception] = None
    for attempt in range(1, retries + 1):
        try:
            response = await client.request(
                method,
                url,
                params=params,
                json=json_body,
                headers={
                    "Accept": "application/json, text/plain, */*",
                    "Content-Type": "application/json",
                    "User-Agent": "Mozilla/5.0 CargoV32/1.0",
                },
            )
            response.raise_for_status()
            return response.json()
        except Exception as exc:
            last_error = exc
            if attempt < retries:
                await asyncio.sleep(1.2 * attempt)
    raise RuntimeError(f"{method} {url} failed: {last_error}")


def trade_list_payload(page: int) -> Dict[str, Any]:
    start_row = ((page - 1) * UZEX_PAGE_SIZE) + 1
    end_row = start_row + UZEX_PAGE_SIZE - 1
    return {
        "TypeId": UZEX_TYPE_ID,
        "From": start_row,
        "To": end_row,
        "System_Id": UZEX_SYSTEM_ID,
    }


async def fetch_trade_list(client: httpx.AsyncClient) -> List[Dict[str, Any]]:
    rows_all: List[Dict[str, Any]] = []
    seen: Set[str] = set()

    for page in range(1, MAX_PAGES + 1):
        data = await http_json(
            client,
            "POST",
            UZEX_TRADE_LIST_URL,
            json_body=trade_list_payload(page),
        )
        rows = find_first_list(data)
        logger.info("UZEX page %s: %s rows", page, len(rows))
        if not rows:
            break

        added = 0
        for row in rows:
            if not isinstance(row, dict):
                continue
            lot_id, lot_no = extract_lot_identity(row)
            marker = lot_id or lot_no or hashlib.md5(
                json.dumps(row, ensure_ascii=False, sort_keys=True).encode()
            ).hexdigest()
            if marker in seen:
                continue
            seen.add(marker)
            rows_all.append(row)
            added += 1

        if added == 0 or len(rows) < UZEX_PAGE_SIZE:
            break

    return rows_all


async def fetch_trade_detail(
    client: httpx.AsyncClient,
    list_item: Dict[str, Any],
) -> Dict[str, Any]:
    lot_id = list_item.get("lot_id")
    if not lot_id:
        return list_item

    url = f"{UZEX_GET_TRADE_URL}/{lot_id}/{UZEX_SYSTEM_ID}"
    last_error = None

    for attempt in range(1, DETAIL_RETRIES + 1):
        try:
            data = await http_json(client, "GET", url, retries=1)
            if not isinstance(data, dict):
                return list_item

            detail = data
            for key in ("data", "result", "trade", "lot", "Data", "Result"):
                if isinstance(data.get(key), dict):
                    detail = data[key]
                    break

            merged_raw = deep_merge(list_item.get("raw") or {}, detail)
            merged = parse_trade(merged_raw)

            for key in (
                "title", "customer", "amount", "currency",
                "start_date", "end_date", "category", "url", "lot_no",
            ):
                if merged.get(key) in (None, "", [], {}):
                    merged[key] = list_item.get(key)

            list_title = str(list_item.get("title") or "")
            merged_title = str(merged.get("title") or "")
            list_is_logistics = any(p in normalize_text(list_title) for p in ACCEPT_PHRASES)
            merged_is_logistics = any(p in normalize_text(merged_title) for p in ACCEPT_PHRASES)
            if list_is_logistics and not merged_is_logistics:
                merged["title"] = list_title

            merged["routes"] = merge_value(
                list_item.get("routes") or [],
                merged.get("routes") or [],
            )
            merged["documents"] = merge_value(
                list_item.get("documents") or [],
                merged.get("documents") or [],
            )
            merged["transport_types"] = merge_value(
                list_item.get("transport_types") or [],
                merged.get("transport_types") or [],
            )

            merged["raw"] = merged_raw
            merged["stable_key"] = stable_key(merged)
            return merged
        except Exception as exc:
            last_error = exc
            if attempt < DETAIL_RETRIES:
                await asyncio.sleep(attempt * 1.5)

    logger.warning("GetTrade failed for %s: %s", lot_id, last_error)
    return list_item



def column_letter(number: int) -> str:
    result = ""
    while number:
        number, remainder = divmod(number - 1, 26)
        result = chr(65 + remainder) + result
    return result


def get_gspread_client():
    if not GOOGLE_SHEET_ID or not GOOGLE_SERVICE_ACCOUNT_JSON:
        return None
    if gspread is None or Credentials is None:
        raise RuntimeError("gspread/google-auth not installed")

    info = json.loads(GOOGLE_SERVICE_ACCOUNT_JSON)
    credentials = Credentials.from_service_account_info(
        info,
        scopes=[
            "https://www.googleapis.com/auth/spreadsheets",
            "https://www.googleapis.com/auth/drive",
        ],
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
    if not first_row:
        worksheet.append_row(SHEET_HEADERS, value_input_option="USER_ENTERED")
    else:
        missing = [header for header in SHEET_HEADERS if header not in first_row]
        if missing:
            new_headers = first_row + missing
            worksheet.update(
                range_name=f"A1:{column_letter(len(new_headers))}1",
                values=[new_headers],
            )
    return worksheet


def normalize_sheet_lot_id(value: Any) -> Optional[str]:
    text = str(value or "").strip()
    match = re.search(r"(?:uzex:|/lot/)?(\d{4,})", text)
    return match.group(1) if match else None


def load_permanent_seen_keys_sync() -> Set[str]:
    """
    Google Sheets is the permanent source of truth.
    Reads Stable Key, ID, Number and URL columns to remain compatible
    with all older Cargo versions.
    """
    worksheet = open_worksheet()
    if worksheet is None:
        return set()

    values = worksheet.get_all_values()
    if not values:
        return set()

    headers = values[0]
    rows = values[1:]
    index = {name: i for i, name in enumerate(headers)}
    keys: Set[str] = set()

    candidate_headers = [
        "Stable Key", "ID", "Номер лота", "Ссылка", "Хеш",
    ]

    for row in rows:
        for header in candidate_headers:
            i = index.get(header)
            if i is None or i >= len(row):
                continue
            value = row[i].strip()
            if not value:
                continue
            if header == "Stable Key" and value.startswith(("uzex:", "uzex-no:", "fallback:")):
                keys.add(value)
            lot_id = normalize_sheet_lot_id(value)
            if lot_id:
                keys.add(f"uzex:{lot_id}")
    return keys


def load_local_seen_keys() -> Set[str]:
    try:
        if CACHE_FILE.exists():
            return set(json.loads(CACHE_FILE.read_text(encoding="utf-8")))
    except Exception:
        pass
    return set()


def save_local_seen_keys(keys: Iterable[str]) -> None:
    try:
        CACHE_FILE.write_text(
            json.dumps(sorted(set(keys)), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception as exc:
        logger.warning("Local seen cache save failed: %s", exc)


def document_text(item: Dict[str, Any]) -> str:
    docs = item.get("documents") or []
    return "\n".join(
        f"{doc.get('name', '')} {doc.get('url', '')}".strip()
        for doc in docs
    )


def sheet_row(item: Dict[str, Any]) -> List[Any]:
    score = ai_score(item)
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
        "\n".join(item.get("routes") or []),
        ", ".join(item.get("transport_types") or []),
        item.get("payment"),
        item.get("payment_days"),
        item.get("delivery_days"),
        document_text(item),
        document_checklist(item),
        warnings_for(item),
        responsible_tasks(),
        priority(score),
        score,
        "Участвовать" if score >= 60 else "Проверить",
        "Новый",
        item.get("url"),
        pretty_now(),
        pretty_now(),
        item.get("filter_reason"),
        item.get("stable_key"),
    ]


def append_items_sync(items: List[Dict[str, Any]]) -> int:
    if not items:
        return 0
    worksheet = open_worksheet()
    if worksheet is None:
        return 0
    worksheet.append_rows(
        [sheet_row(item) for item in items],
        value_input_option="USER_ENTERED",
    )
    return len(items)


async def send_telegram(client: httpx.AsyncClient, item: Dict[str, Any]) -> bool:
    if not BOT_TOKEN or not CHAT_ID:
        return False

    routes = item.get("routes") or []
    docs = item.get("documents") or []
    score = ai_score(item)

    def esc(value: Any) -> str:
        return html.escape(str(value or ""))

    route_block = "\n".join(f"• {esc(route)}" for route in routes[:10]) or "• нужно определить"
    docs_block = "\n".join(f"• {esc(doc.get('name'))}" for doc in docs[:10]) or "• не обнаружены"

    text = (
        "🚚 <b>Новый логистический тендер</b>\n\n"
        f"<b>{esc(item.get('title'))}</b>\n"
        f"🔢 №: {esc(item.get('lot_no') or item.get('lot_id'))}\n"
        f"🏢 Заказчик: {esc(item.get('customer') or 'не указан')}\n"
        f"💰 Сумма: {esc(item.get('amount') or 'не указана')} {esc(item.get('currency') or 'UZS')}\n"
        f"📅 Окончание: {esc(item.get('end_date') or 'не указано')}\n"
        f"💳 Оплата: {esc(item.get('payment') or 'не указана')}\n"
        f"🗂 Категория: {esc(item.get('category') or 'не указана')}\n\n"
        f"🚛 <b>Маршруты:</b>\n{route_block}\n\n"
        f"📎 <b>Документы:</b>\n{docs_block}\n\n"
        f"AI Score: {score}/100\n"
        f"Приоритет: {priority(score)}\n\n"
        f"🔗 {esc(item.get('url'))}"
    )

    response = await client.post(
        f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
        json={
            "chat_id": CHAT_ID,
            "text": text[:4096],
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        },
    )
    response.raise_for_status()
    return bool(response.json().get("ok"))


async def integrations_health() -> Dict[str, Any]:
    errors = []
    result = {
        "telegram": False,
        "google_sheets": False,
        "uzex_trade_list": False,
        "uzex_get_trade": False,
    }

    timeout = httpx.Timeout(REQUEST_TIMEOUT_SECONDS)
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        try:
            if BOT_TOKEN:
                response = await client.get(f"https://api.telegram.org/bot{BOT_TOKEN}/getMe")
                result["telegram"] = response.is_success and response.json().get("ok", False)
            else:
                errors.append("telegram:BOT_TOKEN missing")
        except Exception as exc:
            errors.append(f"telegram:{exc}")

        try:
            rows = await fetch_trade_list(client)
            result["uzex_trade_list"] = True
            if rows:
                item = parse_trade(rows[0])
                detail = await fetch_trade_detail(client, item)
                result["uzex_get_trade"] = bool(detail)
            else:
                result["uzex_get_trade"] = True
        except Exception as exc:
            errors.append(f"uzex:{exc}")

    try:
        worksheet = await asyncio.to_thread(open_worksheet)
        result["google_sheets"] = worksheet is not None
    except Exception as exc:
        errors.append(f"google_sheets:{exc}")

    return {
        "status": "ok" if not errors else "degraded",
        **result,
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
        started_at = pretty_now()
        started_mono = time.monotonic()
        result = {
            "status": "running",
            "version": VERSION,
            "trigger": trigger,
            "started_at": started_at,
            "finished_at": None,
            "duration_seconds": 0,
            "sources": {"UZEX": 0},
            "found_total": 0,
            "accepted_total": 0,
            "new_total": 0,
            "duplicates": 0,
            "rejected": 0,
            "detail_complete": 0,
            "detail_incomplete": 0,
            "telegram_sent": 0,
            "sheets_saved": 0,
            "reminders": {
                "morning_digest_sent": False,
                "evening_digest_sent": False,
                "deadline_alerts_sent": 0,
                "active_tenders": 0,
                "errors": [],
            },
            "errors": [],
        }

        with state_lock:
            state.running = True
            state.started_at = started_at
            state.finished_at = None
            state.last_trigger = trigger
            state.last_error = None
            state.scan_count += 1
            save_state()

        logger.info("Scan started | trigger=%s", trigger)

        try:
            timeout = httpx.Timeout(REQUEST_TIMEOUT_SECONDS)
            async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
                raw_rows = await fetch_trade_list(client)
                result["sources"]["UZEX"] = len(raw_rows)
                result["found_total"] = len(raw_rows)

                accepted_list: List[Dict[str, Any]] = []
                for raw in raw_rows:
                    item = parse_trade(raw)
                    accepted, reason = detect_logistics(
                        item["title"], item["description"], item["category"]
                    )
                    item["filter_reason"] = reason
                    if accepted:
                        accepted_list.append(item)
                    else:
                        result["rejected"] += 1

                semaphore = asyncio.Semaphore(DETAIL_CONCURRENCY)

                async def enrich(item: Dict[str, Any]) -> Dict[str, Any]:
                    async with semaphore:
                        return await fetch_trade_detail(client, item)

                enriched = await asyncio.gather(*(enrich(item) for item in accepted_list))

                final_items: List[Dict[str, Any]] = []
                for item in enriched:
                    accepted, reason = detect_logistics(
                        item["title"], item["description"], item["category"]
                    )
                    item["filter_reason"] = reason
                    item["stable_key"] = stable_key(item)
                    if not accepted:
                        result["rejected"] += 1
                        continue

                    if completeness_score(item) >= 3:
                        result["detail_complete"] += 1
                    else:
                        result["detail_incomplete"] += 1
                        logger.warning(
                            "Incomplete lot skipped from notification: %s",
                            item.get("stable_key"),
                        )
                    final_items.append(item)

                result["accepted_total"] = len(final_items)

                local_seen = load_local_seen_keys()
                permanent_seen: Set[str] = set()
                try:
                    permanent_seen = await asyncio.to_thread(load_permanent_seen_keys_sync)
                except Exception as exc:
                    result["errors"].append(f"dedup_sheet:{exc}")
                    logger.exception("Permanent dedup read failed")

                all_seen = local_seen | permanent_seen
                new_items = [
                    item for item in final_items
                    if item["stable_key"] not in all_seen
                    and completeness_score(item) >= 3
                ]

                result["new_total"] = len(new_items)
                result["duplicates"] = len(final_items) - len(new_items)

                # Save first. Telegram is sent only after Google Sheets accepted the rows.
                if new_items:
                    try:
                        result["sheets_saved"] = await asyncio.to_thread(
                            append_items_sync,
                            new_items,
                        )
                    except Exception as exc:
                        result["errors"].append(f"google_sheets:{exc}")
                        logger.exception("Google Sheets save failed")

                    if result["sheets_saved"] == len(new_items):
                        for item in new_items:
                            try:
                                if await send_telegram(client, item):
                                    result["telegram_sent"] += 1
                            except Exception as exc:
                                result["errors"].append(
                                    f"telegram:{item.get('stable_key')}:{exc}"
                                )

                        all_seen.update(item["stable_key"] for item in new_items)
                        save_local_seen_keys(all_seen)
                    else:
                        result["errors"].append(
                            "telegram_skipped:rows_not_confirmed_in_google_sheets"
                        )

                reminder_result = await process_follow_up_reminders(client)
                result["reminders"] = reminder_result
                if reminder_result.get("errors"):
                    result["errors"].extend(
                        f"reminders:{error}" for error in reminder_result["errors"]
                    )
                result["status"] = "success" if not result["errors"] else "partial_success"

        except asyncio.CancelledError:
            result["status"] = "cancelled"
            result["errors"].append("scan_cancelled")
            raise
        except Exception as exc:
            result["status"] = "error"
            result["errors"].append(str(exc))
            logger.error("Scan failed:\n%s", traceback.format_exc())
        finally:
            result["finished_at"] = pretty_now()
            result["duration_seconds"] = round(time.monotonic() - started_mono, 2)

            with state_lock:
                state.running = False
                state.finished_at = result["finished_at"]
                state.last_result = result
                state.last_error = "; ".join(result["errors"]) if result["errors"] else None
                state.total_found += result["found_total"]
                state.total_accepted += result["accepted_total"]
                state.total_new += result["new_total"]
                state.total_duplicates += result["duplicates"]
                state.telegram_sent += result["telegram_sent"]
                state.sheets_saved += result["sheets_saved"]
                if result["status"] in {"success", "partial_success"}:
                    state.successful_scans += 1
                else:
                    state.failed_scans += 1
                save_state()

            logger.info(
                "Scan finished | status=%s found=%s accepted=%s new=%s duplicates=%s duration=%ss",
                result["status"], result["found_total"], result["accepted_total"],
                result["new_total"], result["duplicates"], result["duration_seconds"],
            )
            active_scan_task = None

        return result


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




@asynccontextmanager
async def lifespan(app: FastAPI):
    load_state()
    logger.info("%s starting", APP_NAME)

    if SCAN_ON_STARTUP:
        await asyncio.sleep(min(3, WARMUP_SECONDS))
        start_scan_task("startup")

    yield

    if active_scan_task and not active_scan_task.done():
        active_scan_task.cancel()
    logger.info("%s stopped", APP_NAME)


app = FastAPI(title=APP_NAME, version=VERSION, lifespan=lifespan)


@app.get("/")
async def root() -> Dict[str, Any]:
    return {
        "status": f"{APP_NAME} is running",
        "version": VERSION,
        "running": state.running,
        "warming_up": is_warming_up(),
        "warmup_seconds": WARMUP_SECONDS,
        "trigger_mode": "external_cron_only",
        "deduplication": "Google Sheets Stable Key + ID + URL",
        "endpoints": [
            "/health", "/health/production", "/version", "/scan",
            "/scan_status", "/metrics", "/logs", "/debug/uzex",
            "/debug/lot/{lot_id}", "/reminders/run", "/reminders/status", "/admin/rebuild_dedup_cache", "/docs",
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
    return {**basic, **(await integrations_health())}


@app.get("/health/production")
async def health_production() -> Dict[str, Any]:
    deep = await integrations_health()
    status = "ok"
    if deep.get("errors") or state.last_error:
        status = "degraded"

    return {
        "status": status,
        "version": VERSION,
        "service": APP_NAME,
        "trigger_mode": "external_cron_only",
        "uptime_seconds": app_uptime_seconds(),
        "warming_up": is_warming_up(),
        "warmup_seconds": WARMUP_SECONDS,
        "running": state.running,
        "last_trigger": state.last_trigger,
        "last_started_at": state.started_at,
        "last_finished_at": state.finished_at,
        "last_result_status": (
            state.last_result.get("status")
            if isinstance(state.last_result, dict)
            else None
        ),
        "last_error": state.last_error,
        "scan_count": state.scan_count,
        "successful_scans": state.successful_scans,
        "failed_scans": state.failed_scans,
        "telegram": deep.get("telegram"),
        "google_sheets": deep.get("google_sheets"),
        "uzex_trade_list": deep.get("uzex_trade_list"),
        "uzex_get_trade": deep.get("uzex_get_trade"),
        "errors": deep.get("errors", []),
        "time": pretty_now(),
    }


async def manual_scan_response() -> Dict[str, Any]:
    if is_warming_up():
        return {
            "status": "warming_up",
            "version": VERSION,
            "running": False,
            "uptime_seconds": app_uptime_seconds(),
            "retry_after_seconds": max(0, WARMUP_SECONDS - app_uptime_seconds()),
            "message": "Service is warming up. Retry later.",
        }

    if not start_scan_task("cron_http"):
        return {
            "status": "already_running",
            "version": VERSION,
            "running": True,
            "started_at": state.started_at,
            "message": "Scan already running. Check /scan_status.",
        }

    return {
        "status": "accepted",
        "version": VERSION,
        "running": True,
        "started_at": pretty_now(),
        "message": "Scan started. Check /scan_status.",
    }


@app.get("/scan", operation_id="scan_get")
async def scan_get() -> Dict[str, Any]:
    return await manual_scan_response()


@app.post("/scan", operation_id="scan_post")
async def scan_post() -> Dict[str, Any]:
    return await manual_scan_response()


@app.get("/scan_status")
async def scan_status() -> Dict[str, Any]:
    with state_lock:
        return {
            "status": "ok",
            "version": VERSION,
            "running": state.running,
            "warming_up": is_warming_up(),
            "started_at": state.started_at,
            "finished_at": state.finished_at,
            "last_trigger": state.last_trigger,
            "last_result": state.last_result,
            "last_error": state.last_error,
            "trigger_mode": "external_cron_only",
            "next_scheduled_run": None,
        }


@app.get("/metrics")
async def metrics() -> Dict[str, Any]:
    return {
        "version": VERSION,
        "uptime_seconds": app_uptime_seconds(),
        "running": state.running,
        "warming_up": is_warming_up(),
        "warmup_seconds": WARMUP_SECONDS,
        "trigger_mode": "external_cron_only",
        "scan_count": state.scan_count,
        "successful_scans": state.successful_scans,
        "failed_scans": state.failed_scans,
        "success_rate_percent": round(
            (state.successful_scans / state.scan_count * 100)
            if state.scan_count else 0,
            2,
        ),
        "total_found": state.total_found,
        "total_accepted": state.total_accepted,
        "total_new": state.total_new,
        "total_duplicates": state.total_duplicates,
        "telegram_sent": state.telegram_sent,
        "sheets_saved": state.sheets_saved,
        "last_started_at": state.started_at,
        "last_finished_at": state.finished_at,
        "last_error": state.last_error,
    }


@app.get("/metrics/prometheus", response_class=PlainTextResponse)
async def prometheus() -> str:
    return "\n".join([
        f"cargo_scan_running {1 if state.running else 0}",
        f"cargo_scan_count_total {state.scan_count}",
        f"cargo_scan_success_total {state.successful_scans}",
        f"cargo_scan_failed_total {state.failed_scans}",
        f"cargo_tenders_found_total {state.total_found}",
        f"cargo_tenders_accepted_total {state.total_accepted}",
        f"cargo_tenders_new_total {state.total_new}",
        f"cargo_tenders_duplicates_total {state.total_duplicates}",
        f"cargo_telegram_sent_total {state.telegram_sent}",
        f"cargo_sheets_saved_total {state.sheets_saved}",
        "",
    ])


@app.get("/logs")
async def logs(limit: int = Query(100, ge=1, le=500)) -> Dict[str, Any]:
    records = list(ring_handler.records)[-limit:]
    return {"version": VERSION, "count": len(records), "logs": records}


@app.get("/debug/uzex")
async def debug_uzex(limit: int = Query(10, ge=1, le=100)) -> Dict[str, Any]:
    timeout = httpx.Timeout(REQUEST_TIMEOUT_SECONDS)
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        rows = await fetch_trade_list(client)

    items = []
    for raw in rows[:limit]:
        item = parse_trade(raw)
        accepted, reason = detect_logistics(
            item["title"], item["description"], item["category"]
        )
        item["accepted"] = accepted
        item["filter_reason"] = reason
        item.pop("raw", None)
        items.append(item)
    return {"version": VERSION, "count": len(items), "items": items}


@app.get("/debug/lot/{lot_id}")
async def debug_lot(lot_id: int) -> Dict[str, Any]:
    timeout = httpx.Timeout(REQUEST_TIMEOUT_SECONDS)
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        seed = {
            "source": "UZEX",
            "lot_id": str(lot_id),
            "lot_no": str(lot_id),
            "title": "",
            "customer": "",
            "amount": "",
            "currency": "UZS",
            "start_date": "",
            "end_date": "",
            "category": "",
            "description": "",
            "payment": "",
            "payment_days": "",
            "delivery_days": "",
            "routes": [],
            "transport_types": [],
            "documents": [],
            "url": f"{UZEX_SITE_BASE}/lot/{lot_id}",
            "raw": {"id": lot_id},
            "stable_key": f"uzex:{lot_id}",
        }
        item = await fetch_trade_detail(client, seed)
        accepted, reason = detect_logistics(
            item["title"], item["description"], item["category"]
        )
        item["accepted"] = accepted
        item["filter_reason"] = reason
        item["completeness_score"] = completeness_score(item)
        item["ai_score"] = ai_score(item)
        item.pop("raw", None)
        return {"version": VERSION, "item": item}


@app.post("/reminders/run")
async def run_reminders_now() -> Dict[str, Any]:
    timeout = httpx.Timeout(REQUEST_TIMEOUT_SECONDS)
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        result = await process_follow_up_reminders(client)
    return {
        "status": "ok" if not result.get("errors") else "partial_success",
        "version": VERSION,
        "result": result,
    }


@app.get("/reminders/status")
async def reminders_status() -> Dict[str, Any]:
    tenders = await asyncio.to_thread(load_active_tenders_sync)
    registry = await asyncio.to_thread(load_reminder_registry_sync)
    today = now_local().strftime("%Y-%m-%d")
    return {
        "status": "ok",
        "version": VERSION,
        "active_tenders": len(tenders),
        "morning_sent_today": f"digest:morning:{today}" in registry,
        "evening_sent_today": f"digest:evening:{today}" in registry,
        "morning_hour": MORNING_REMINDER_HOUR,
        "evening_hour": EVENING_REMINDER_HOUR,
        "deadline_alert_days": DEADLINE_ALERT_DAYS,
        "registry_entries": len(registry),
    }


@app.post("/admin/rebuild_dedup_cache")
async def rebuild_dedup_cache() -> Dict[str, Any]:
    try:
        keys = await asyncio.to_thread(load_permanent_seen_keys_sync)
        save_local_seen_keys(keys)
        return {
            "status": "ok",
            "version": VERSION,
            "keys_loaded": len(keys),
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.post("/admin/reset_stuck_scan")
async def reset_stuck_scan() -> Dict[str, Any]:
    if active_scan_task and not active_scan_task.done():
        raise HTTPException(status_code=409, detail="Active scan task is still running")
    with state_lock:
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
        content={"status": "error", "version": VERSION, "detail": str(exc)},
    )


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "main_v2:app",
        host="0.0.0.0",
        port=int(os.getenv("PORT", "10000")),
        reload=False,
    )
