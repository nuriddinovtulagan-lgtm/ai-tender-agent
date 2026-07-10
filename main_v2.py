import os
import re
import json
import time
import threading
import traceback
from datetime import datetime
from urllib.parse import urljoin

import requests
import gspread
from bs4 import BeautifulSoup
from fastapi import FastAPI, Query
from google.oauth2.service_account import Credentials


# ============================================================
# AI TENDER AGENT — CARGO V29.1 UZEX INSPECTOR
# Replace the content of main_v2.py with this file.
#
# Render Start Command:
# uvicorn main_v2:app --host 0.0.0.0 --port $PORT
# ============================================================

APP_VERSION = "cargo_v29_1_uzex_inspector"
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
MAX_SCRIPT_BYTES = int(os.getenv("MAX_SCRIPT_BYTES", "6000000"))
MAX_SCRIPTS = int(os.getenv("MAX_SCRIPTS", "30"))

UZEX_BASE = "https://etender.uzex.uz"
UZEX_ENTRY_URLS = [
    f"{UZEX_BASE}/",
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

INSPECT_LOCK = threading.Lock()
INSPECT_STATE = {
    "running": False,
    "started_at": None,
    "finished_at": None,
    "last_result": None,
    "last_error": None,
}


# ============================================================
# GENERAL HELPERS
# ============================================================

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


def safe_get(session: requests.Session, url: str, *, timeout=None):
    return session.get(
        url,
        timeout=timeout or REQUEST_TIMEOUT,
        allow_redirects=True,
    )


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


# ============================================================
# UZEX INSPECTOR
# ============================================================

ABSOLUTE_URL_PATTERN = re.compile(
    r"""https?://[A-Za-z0-9._~:/?#\[\]@!$&'()*+,;=%-]+""",
    re.IGNORECASE,
)

RELATIVE_API_PATTERN = re.compile(
    r"""(?:"|')((?:/api/|api/)[A-Za-z0-9._~:/?#\[\]@!$&()*+,;=%-]+)(?:"|')""",
    re.IGNORECASE,
)

AXIOS_FETCH_PATTERN = re.compile(
    r"""(?:axios\.(?:get|post|put|delete|patch)|fetch)\s*\(\s*["']([^"']+)["']""",
    re.IGNORECASE,
)

BASE_URL_PATTERN = re.compile(
    r"""(?:baseURL|baseUrl|apiUrl|apiURL|apiBase|API_URL)\s*[:=]\s*["']([^"']+)["']""",
    re.IGNORECASE,
)

ENDPOINT_HINT_PATTERN = re.compile(
    r"""["']([^"']*(?:Trade|Lot|Tender|Auction|Purchase|GetList|GetTrades|GetTrade|GetLots|Search)[^"']*)["']""",
    re.IGNORECASE,
)

METHOD_PATTERN = re.compile(
    r"""method\s*:\s*["'](get|post|put|delete|patch)["']""",
    re.IGNORECASE,
)

SCRIPT_SRC_PATTERN = re.compile(
    r"""<script[^>]+src=["']([^"']+)["']""",
    re.IGNORECASE,
)

LINK_HREF_PATTERN = re.compile(
    r"""<link[^>]+href=["']([^"']+)["']""",
    re.IGNORECASE,
)


def sanitize_url(value: str) -> str:
    value = (value or "").strip()
    value = value.rstrip("\\'\"),;]")
    value = value.replace("\\/", "/")
    return value


def unique_keep_order(values):
    seen = set()
    output = []
    for value in values:
        if not value:
            continue
        if value in seen:
            continue
        seen.add(value)
        output.append(value)
    return output


def extract_asset_urls(html: str, page_url: str):
    soup = BeautifulSoup(html, "html.parser")
    scripts = []
    styles = []

    for tag in soup.find_all("script", src=True):
        scripts.append(urljoin(page_url, tag.get("src")))

    for tag in soup.find_all("link", href=True):
        rel = " ".join(tag.get("rel") or [])
        href = tag.get("href")
        if "stylesheet" in rel.lower() or href.endswith(".css"):
            styles.append(urljoin(page_url, href))

    # Regex fallback for minimal/odd HTML.
    for src in SCRIPT_SRC_PATTERN.findall(html):
        scripts.append(urljoin(page_url, src))

    for href in LINK_HREF_PATTERN.findall(html):
        if href.endswith(".css"):
            styles.append(urljoin(page_url, href))

    return unique_keep_order(scripts), unique_keep_order(styles)


def inspect_text_for_endpoints(text: str, source_url: str):
    absolute_urls = []
    relative_api_paths = []
    call_urls = []
    base_urls = []
    endpoint_hints = []
    methods = []

    for value in ABSOLUTE_URL_PATTERN.findall(text):
        url = sanitize_url(value)
        if "etender.uzex.uz" in url or "/api/" in url or "api." in url:
            absolute_urls.append(url)

    for value in RELATIVE_API_PATTERN.findall(text):
        relative_api_paths.append(sanitize_url(value))

    for value in AXIOS_FETCH_PATTERN.findall(text):
        call_urls.append(sanitize_url(value))

    for value in BASE_URL_PATTERN.findall(text):
        base_urls.append(sanitize_url(value))

    for value in ENDPOINT_HINT_PATTERN.findall(text):
        hint = sanitize_url(value)
        if len(hint) <= 500:
            endpoint_hints.append(hint)

    methods.extend([m.lower() for m in METHOD_PATTERN.findall(text)])

    return {
        "source_url": source_url,
        "absolute_urls": unique_keep_order(absolute_urls)[:300],
        "relative_api_paths": unique_keep_order(relative_api_paths)[:300],
        "call_urls": unique_keep_order(call_urls)[:300],
        "base_urls": unique_keep_order(base_urls)[:100],
        "endpoint_hints": unique_keep_order(endpoint_hints)[:400],
        "methods": unique_keep_order(methods),
    }


def score_endpoint(value: str) -> int:
    v = normalize_text(value)
    score = 0

    if "/api/" in v:
        score += 10
    if "etender.uzex.uz" in v:
        score += 8
    if any(word in v for word in ["trade", "lot", "tender", "purchase", "auction"]):
        score += 6
    if any(word in v for word in ["getlist", "gettrades", "gettrade", "getlots", "search"]):
        score += 5
    if v.startswith("http"):
        score += 2
    if len(v) > 300:
        score -= 4

    return score


def build_candidate_endpoints(records):
    candidates = []

    for record in records:
        for field in [
            "absolute_urls",
            "relative_api_paths",
            "call_urls",
            "base_urls",
            "endpoint_hints",
        ]:
            for value in record.get(field, []):
                candidates.append({
                    "value": value,
                    "source": record.get("source_url"),
                    "kind": field,
                    "score": score_endpoint(value),
                })

    dedup = {}
    for item in candidates:
        key = item["value"]
        if key not in dedup or item["score"] > dedup[key]["score"]:
            dedup[key] = item

    output = sorted(
        dedup.values(),
        key=lambda x: (-x["score"], x["value"]),
    )
    return output[:500]


def try_candidate_endpoint(session: requests.Session, candidate: dict):
    value = candidate["value"]

    # We only probe safe GET candidates.
    if value.startswith("/"):
        url = urljoin(UZEX_BASE, value)
    elif value.startswith("api/"):
        url = urljoin(UZEX_BASE + "/", value)
    elif value.startswith("http://") or value.startswith("https://"):
        url = value
    else:
        return {
            **candidate,
            "probed": False,
            "reason": "not_a_direct_url",
        }

    try:
        response = safe_get(session, url, timeout=15)
        content_type = response.headers.get("content-type", "")
        sample = response.text[:1000]

        json_type = None
        json_keys = []
        list_length = None

        try:
            data = response.json()
            json_type = type(data).__name__
            if isinstance(data, dict):
                json_keys = list(data.keys())[:50]
            elif isinstance(data, list):
                list_length = len(data)
        except Exception:
            pass

        return {
            **candidate,
            "probed": True,
            "url": url,
            "status": response.status_code,
            "size": len(response.content),
            "content_type": content_type,
            "json_type": json_type,
            "json_keys": json_keys,
            "list_length": list_length,
            "sample": compact_text(sample, 1000),
        }
    except Exception as exc:
        return {
            **candidate,
            "probed": True,
            "url": url,
            "error": f"{type(exc).__name__}: {exc}",
        }


def run_uzex_inspection():
    started = time.time()
    session = make_session()

    pages = []
    scripts = []
    styles = []
    extraction_records = []
    errors = []

    # 1. Fetch entry pages and inspect raw HTML.
    for page_url in UZEX_ENTRY_URLS:
        try:
            response = safe_get(session, page_url)
            html = response.text

            page_scripts, page_styles = extract_asset_urls(html, page_url)
            scripts.extend(page_scripts)
            styles.extend(page_styles)

            record = inspect_text_for_endpoints(html, page_url)
            extraction_records.append(record)

            pages.append({
                "url": page_url,
                "status": response.status_code,
                "final_url": response.url,
                "size": len(response.content),
                "content_type": response.headers.get("content-type", ""),
                "scripts_found": len(page_scripts),
                "styles_found": len(page_styles),
                "html_sample": compact_text(html, 2500),
            })

            print(
                "INSPECT PAGE:",
                response.status_code,
                len(response.content),
                page_url,
                "scripts:",
                len(page_scripts),
            )
        except Exception as exc:
            errors.append(f"PAGE {page_url}: {type(exc).__name__}: {exc}")

    scripts = unique_keep_order(scripts)[:MAX_SCRIPTS]
    styles = unique_keep_order(styles)

    # 2. Download JS bundles and inspect strings.
    script_results = []
    for script_url in scripts:
        try:
            response = safe_get(session, script_url)
            raw = response.content[:MAX_SCRIPT_BYTES]
            text = raw.decode("utf-8", errors="ignore")

            record = inspect_text_for_endpoints(text, script_url)
            extraction_records.append(record)

            script_results.append({
                "url": script_url,
                "status": response.status_code,
                "size": len(response.content),
                "scanned_bytes": len(raw),
                "content_type": response.headers.get("content-type", ""),
                "absolute_urls": len(record["absolute_urls"]),
                "relative_api_paths": len(record["relative_api_paths"]),
                "call_urls": len(record["call_urls"]),
                "base_urls": len(record["base_urls"]),
                "endpoint_hints": len(record["endpoint_hints"]),
            })

            print(
                "INSPECT SCRIPT:",
                response.status_code,
                len(response.content),
                script_url,
            )
        except Exception as exc:
            errors.append(f"SCRIPT {script_url}: {type(exc).__name__}: {exc}")

    # 3. Build and probe top candidate endpoints.
    candidates = build_candidate_endpoints(extraction_records)
    probe_targets = [
        item for item in candidates
        if item["score"] >= 8
    ][:80]

    probe_results = []
    for candidate in probe_targets:
        probe_results.append(try_candidate_endpoint(session, candidate))

    useful_probes = [
        item for item in probe_results
        if item.get("status") in {200, 201}
        and (
            item.get("json_type")
            or "json" in normalize_text(item.get("content_type"))
            or "/api/" in normalize_text(item.get("url"))
        )
    ]

    result = {
        "status": "success",
        "version": APP_VERSION,
        "started_at": datetime.fromtimestamp(started).strftime("%d.%m.%Y %H:%M:%S"),
        "finished_at": now_str(),
        "duration_seconds": round(time.time() - started, 2),
        "pages": pages,
        "scripts": script_results,
        "styles": styles,
        "candidate_count": len(candidates),
        "top_candidates": candidates[:150],
        "probe_count": len(probe_results),
        "useful_probes": useful_probes[:100],
        "probe_results": probe_results[:150],
        "errors": errors[:100],
    }

    if not scripts:
        result["status"] = "warning"
        result["warning"] = "No JavaScript bundle URLs were found in UZEX HTML."
    elif not candidates:
        result["status"] = "warning"
        result["warning"] = "JavaScript bundles were downloaded, but no API candidates were extracted."
    elif not useful_probes:
        result["status"] = "warning"
        result["warning"] = (
            "API candidates were found, but no directly usable JSON GET endpoint "
            "was confirmed. The real API may require POST, headers, or a request body."
        )

    return result


def inspector_worker():
    try:
        result = run_uzex_inspection()
        with INSPECT_LOCK:
            INSPECT_STATE["last_result"] = result
            INSPECT_STATE["last_error"] = None

        send_telegram(
            "🧪 Cargo V29.1 UZEX Inspector завершён\n\n"
            f"Статус: {result.get('status')}\n"
            f"JS-файлов: {len(result.get('scripts', []))}\n"
            f"Кандидатов API: {result.get('candidate_count', 0)}\n"
            f"Проверено адресов: {result.get('probe_count', 0)}\n"
            f"Полезных ответов: {len(result.get('useful_probes', []))}\n"
            f"Ошибок: {len(result.get('errors', []))}"
        )
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
        print("INSPECTOR ERROR:", traceback.format_exc())
        with INSPECT_LOCK:
            INSPECT_STATE["last_error"] = error

        send_telegram(
            "❌ Cargo V29.1 UZEX Inspector error\n\n"
            f"{error}"
        )
    finally:
        with INSPECT_LOCK:
            INSPECT_STATE["running"] = False
            INSPECT_STATE["finished_at"] = now_str()


def start_inspector():
    with INSPECT_LOCK:
        if INSPECT_STATE["running"]:
            return {
                "status": "already_running",
                "version": APP_VERSION,
                "running": True,
                "started_at": INSPECT_STATE["started_at"],
            }

        INSPECT_STATE["running"] = True
        INSPECT_STATE["started_at"] = now_str()
        INSPECT_STATE["finished_at"] = None
        INSPECT_STATE["last_result"] = None
        INSPECT_STATE["last_error"] = None

    thread = threading.Thread(target=inspector_worker, daemon=True)
    thread.start()

    return {
        "status": "accepted",
        "version": APP_VERSION,
        "running": True,
        "started_at": INSPECT_STATE["started_at"],
        "message": "UZEX Inspector started. Check /inspect_status.",
    }


# ============================================================
# PLACEHOLDER SCAN
# ============================================================

def run_scan(trigger="manual"):
    result = {
        "status": "warning",
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
        "warnings": [
            "Cargo V29.1 is an inspector build. Run /inspect_uzex first.",
        ],
        "trigger": trigger,
    }

    send_telegram(
        "⚠️ Cargo V29.1 Inspector\n\n"
        "Обычный поиск временно отключён.\n"
        "Сначала запустите /inspect_uzex и пришлите результат /inspect_status."
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
        with SCAN_LOCK:
            SCAN_STATE["last_error"] = error
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
    }


# ============================================================
# ENDPOINTS
# ============================================================

@app.get("/")
def home():
    return {
        "status": "AI Tender Agent is running",
        "version": APP_VERSION,
        "mode": "UZEX Inspector",
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
        response = safe_get(session, UZEX_ENTRY_URLS[1], timeout=15)
        result["uzex_home"] = response.status_code == 200
        result["uzex_response_size"] = len(response.content)

        if response.status_code != 200:
            result["errors"].append(
                f"UZEX HTTP {response.status_code}"
            )
    except Exception as exc:
        result["errors"].append(
            f"UZEX: {type(exc).__name__}: {exc}"
        )

    if result["errors"]:
        result["status"] = "warning"

    return result


@app.get("/inspect_uzex")
def inspect_uzex():
    return start_inspector()


@app.get("/inspect_status")
def inspect_status():
    with INSPECT_LOCK:
        return {
            "status": "ok",
            "version": APP_VERSION,
            **INSPECT_STATE,
        }


@app.get("/inspect_result")
def inspect_result(
    include_probes: bool = Query(default=True),
    include_scripts: bool = Query(default=True),
):
    with INSPECT_LOCK:
        result = INSPECT_STATE.get("last_result")
        error = INSPECT_STATE.get("last_error")

    if not result:
        return {
            "version": APP_VERSION,
            "message": "No inspection result yet. Run /inspect_uzex.",
            "last_error": error,
        }

    output = dict(result)

    if not include_probes:
        output.pop("probe_results", None)
    if not include_scripts:
        output.pop("scripts", None)

    return output


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


@app.get("/test_environment")
def test_environment():
    return {
        "version": APP_VERSION,
        "bot_token_present": bool(BOT_TOKEN),
        "chat_id_present": bool(CHAT_ID),
        "google_sheet_id_present": bool(GOOGLE_SHEET_ID),
        "google_credentials_present": bool(GOOGLE_CREDS_JSON),
        "request_timeout": REQUEST_TIMEOUT,
        "max_script_bytes": MAX_SCRIPT_BYTES,
        "max_scripts": MAX_SCRIPTS,
        "uzex_entry_urls": UZEX_ENTRY_URLS,
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        app,
        host="0.0.0.0",
        port=int(os.getenv("PORT", "10000")),
    )
