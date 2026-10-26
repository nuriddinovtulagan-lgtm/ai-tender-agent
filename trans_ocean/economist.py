"""Economist agent (stage 2): cost of a trip and the minimum price for a tender or an offer.

Numbers come from the shared base, filled by the accountant:
- "Параметры машин": one row per truck type (fuel, driver, fixed costs, km per day ...);
- "Параметры цены": margin, VAT, rounding;
- "Расстояния": road distances between cities (seeded with approximate values to check).
Market rates from "Ставки рынка" (filled later by the Price agent) are shown for comparison.

Every calculation is saved to "Расчёты цены" with status "Черновик"; the final price is
decided by a person. In Telegram: /price Ташкент Нукус трал  (see HELP).
"""

from __future__ import annotations

import html
import math
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from .base import Base, new_id, now_local, stamp

AGENT = "Экономист"

VEHICLE_SHEET = "Параметры машин"
PRICE_SHEET = "Параметры цены"
DISTANCE_SHEET = "Расстояния"
RESULT_SHEET = "Расчёты цены"

V_FUEL = "Топливо (л/100 км)"
V_FUEL_PRICE = "Цена топлива (сум/л)"
V_DRIVER_KM = "Водитель (сум/км)"
V_PER_DIEM = "Командировочные (сум/день)"
V_FIXED_MONTH = "Постоянные расходы (сум/мес)"
V_WORK_DAYS = "Рабочих дней в месяц"
V_KM_DAY = "Км в день"
V_OTHER = "Прочие расходы на рейс (сум)"
V_RETURN = "Обратный порожний пробег (%)"
REQUIRED_VEHICLE = [V_FUEL, V_FUEL_PRICE, V_DRIVER_KM, V_KM_DAY]

DEFAULT_VEHICLES = [
    {"Тип машины": "Трал", V_RETURN: "100", "Заметки": "низкорамный, 1,5–50 т"},
    {"Тип машины": "Тент 96 м³", V_RETURN: "100", "Заметки": ""},
    {"Тип машины": "Контейнеровоз", V_RETURN: "100", "Заметки": ""},
    {"Тип машины": "Бортовой", V_RETURN: "100", "Заметки": ""},
]

DEFAULT_PRICE_PARAMS = [
    ("Маржа (%)", "15", "Наценка на себестоимость. Уточнить у руководителя."),
    ("НДС (%)", "12", "Ставка НДС. 0 — если цена без НДС."),
    ("Округление (сум)", "10000", "Минимальная цена округляется вверх до этой суммы."),
]

# Approximate road distances from Tashkent. Check and correct in the sheet.
DEFAULT_DISTANCES = [
    ("Ташкент", "Чирчик", 35), ("Ташкент", "Алмалык", 65), ("Ташкент", "Ангрен", 110),
    ("Ташкент", "Гулистан", 120), ("Ташкент", "Джизак", 200), ("Ташкент", "Самарканд", 300),
    ("Ташкент", "Навои", 470), ("Ташкент", "Бухара", 570), ("Ташкент", "Карши", 520),
    ("Ташкент", "Термез", 700), ("Ташкент", "Коканд", 230), ("Ташкент", "Наманган", 290),
    ("Ташкент", "Фергана", 320), ("Ташкент", "Андижан", 350), ("Ташкент", "Ургенч", 1000),
    ("Ташкент", "Нукус", 1150),
]

CITY_ALIASES = {
    "toshkent": "ташкент", "tashkent": "ташкент", "тошкент": "ташкент",
    "samarqand": "самарканд", "samarkand": "самарканд", "самарқанд": "самарканд",
    "buxoro": "бухара", "bukhara": "бухара", "бухоро": "бухара",
    "navoiy": "навои", "navoi": "навои", "навоий": "навои",
    "qarshi": "карши", "karshi": "карши", "қарши": "карши",
    "termiz": "термез", "termez": "термез", "термиз": "термез",
    "jizzax": "джизак", "jizzakh": "джизак", "жиззах": "джизак", "джиззак": "джизак",
    "guliston": "гулистан", "гулистон": "гулистан",
    "andijon": "андижан", "andijan": "андижан", "андижон": "андижан",
    "namangan": "наманган", "fargona": "фергана", "fergana": "фергана", "фарғона": "фергана",
    "qoqon": "коканд", "kokand": "коканд", "қўқон": "коканд",
    "urganch": "ургенч", "urgench": "ургенч", "урганч": "ургенч",
    "nukus": "нукус", "chirchiq": "чирчик", "olmaliq": "алмалык", "angren": "ангрен",
}

HIRE_WORDS = {"наём", "наем", "найм", "наёмная", "наемная", "hire", "yollanma"}

HELP = (
    "💰 <b>Экономист — расчёт цены рейса</b>\n\n"
    "<code>/price Ташкент Нукус трал</code> — своя машина\n"
    "<code>/price Ташкент Самарканд тент наём 4500000</code> — наёмная машина за 4,5 млн\n"
    "<code>/price 450км контейнеровоз</code> — по расстоянию\n\n"
    "Параметры — в листах «Параметры машин», «Параметры цены», «Расстояния»."
)


class MissingParams(Exception):
    def __init__(self, missing: List[str]):
        super().__init__(", ".join(missing))
        self.missing = missing


@dataclass
class Calculation:
    origin: str
    destination: str
    vehicle: str
    km: float
    own: bool
    days: int = 0
    total_km: float = 0
    fuel: float = 0
    driver: float = 0
    fixed: float = 0
    other: float = 0
    hired: float = 0
    cost: float = 0
    margin_pct: float = 0
    vat_pct: float = 0
    min_price: float = 0          # without VAT
    min_price_vat: float = 0      # with VAT
    market: List[float] = field(default_factory=list)
    record_id: str = ""

    @property
    def route(self) -> str:
        if self.origin and self.destination:
            return f"{self.origin} — {self.destination}"
        return "по расстоянию"


# ---------------------------------------------------------------- helpers

def number(value: Any) -> Optional[float]:
    text = str(value or "").strip().replace(" ", "").replace(" ", "").replace(",", ".")
    text = re.sub(r"(сум|so'm|uzs|%|км)$", "", text, flags=re.I)
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def city_key(name: str) -> str:
    low = (name or "").strip().lower().replace("ё", "е")
    return CITY_ALIASES.get(low, low)


def round_up(value: float, step: float) -> float:
    if step <= 0:
        return round(value)
    return math.ceil(value / step) * step


def money(value: float) -> str:
    return f"{value:,.0f}".replace(",", " ") + " сум"


def find_vehicle(rows: List[Dict[str, str]], word: str) -> Optional[Dict[str, str]]:
    w = (word or "").strip().lower()
    if len(w) < 3:
        return None
    for row in rows:
        name = (row.get("Тип машины") or "").strip().lower()
        if name and (name.startswith(w[:4]) or w.startswith(name[:4])):
            return row
    return None


def find_distance(rows: List[Dict[str, str]], origin: str, destination: str) -> Optional[float]:
    a, b = city_key(origin), city_key(destination)
    for row in rows:
        x, y = city_key(row.get("Откуда", "")), city_key(row.get("Куда", ""))
        if {x, y} == {a, b}:
            return number(row.get("Км"))
    return None


def market_rates(rows: List[Dict[str, str]], origin: str, destination: str, vehicle: str) -> List[float]:
    a, b = city_key(origin), city_key(destination)
    v = (vehicle or "").lower()[:4]
    out = []
    for row in rows:
        if city_key(row.get("Откуда", "")) != a or city_key(row.get("Куда", "")) != b:
            continue
        if v and v not in (row.get("Тип машины") or "").lower():
            continue
        currency = (row.get("Валюта") or "UZS").strip().lower()
        if currency not in {"", "uzs", "сум", "so'm", "som"}:
            continue
        rate = number(row.get("Ставка"))
        if rate:
            out.append(rate)
    return out


def price_params(rows: List[Dict[str, str]]) -> Dict[str, float]:
    params = {name: float(value) for name, value, _ in DEFAULT_PRICE_PARAMS}
    for row in rows:
        value = number(row.get("Значение"))
        if row.get("Параметр") and value is not None:
            params[row["Параметр"].strip()] = value
    return params


def compute(vehicle: Dict[str, str], km: float, prices: Dict[str, float],
            origin: str = "", destination: str = "", hired_rate: Optional[float] = None,
            market: Optional[List[float]] = None) -> Calculation:
    """Pure calculation. Raises MissingParams if the truck type row is not filled."""
    margin = prices.get("Маржа (%)", 15.0)
    vat = prices.get("НДС (%)", 12.0)
    step = prices.get("Округление (сум)", 10000.0)
    calc = Calculation(origin, destination, vehicle.get("Тип машины", ""), km,
                       own=hired_rate is None, margin_pct=margin, vat_pct=vat, market=market or [])
    if hired_rate is not None:
        calc.hired = hired_rate
        calc.cost = hired_rate
    else:
        missing = [f"{calc.vehicle}: {name}" for name in REQUIRED_VEHICLE if number(vehicle.get(name)) is None]
        if missing:
            raise MissingParams(missing)
        back = (number(vehicle.get(V_RETURN)) or 0) / 100
        calc.total_km = km * (1 + back)
        calc.days = max(1, math.ceil(calc.total_km / number(vehicle.get(V_KM_DAY))))
        calc.fuel = calc.total_km * number(vehicle.get(V_FUEL)) / 100 * number(vehicle.get(V_FUEL_PRICE))
        calc.driver = calc.total_km * number(vehicle.get(V_DRIVER_KM)) + calc.days * (number(vehicle.get(V_PER_DIEM)) or 0)
        work_days = number(vehicle.get(V_WORK_DAYS)) or 22
        calc.fixed = (number(vehicle.get(V_FIXED_MONTH)) or 0) / work_days * calc.days
        calc.other = number(vehicle.get(V_OTHER)) or 0
        calc.cost = calc.fuel + calc.driver + calc.fixed + calc.other
    calc.min_price = round_up(calc.cost * (1 + margin / 100), step)
    calc.min_price_vat = round_up(calc.min_price * (1 + vat / 100), step)
    return calc


def parse_command(text: str, vehicle_rows: List[Dict[str, str]]) -> Dict[str, Any]:
    """'/price Ташкент - Нукус трал наём 4500000' -> {origin, destination, vehicle, km, hired}."""
    body = re.sub(r"^/price(@\w+)?", "", (text or "").strip(), flags=re.I)
    body = re.sub(r"(\d)\s+(км|km)\b", r"\1км", body, flags=re.I)
    tokens = [t for t in re.split(r"[\s,;→—–\-]+", body) if t]
    result: Dict[str, Any] = {"origin": "", "destination": "", "vehicle": None, "km": None, "hired": None}
    cities: List[str] = []
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        low = tok.lower()
        km_match = re.fullmatch(r"(\d+(?:[.,]\d+)?)(км|km)", low)
        if km_match:
            result["km"] = number(km_match.group(1))
        elif low in HIRE_WORDS:
            if i + 1 < len(tokens) and number(tokens[i + 1]) is not None:
                result["hired"] = number(tokens[i + 1])
                i += 1
            else:
                result["hired"] = 0.0
        elif result["vehicle"] is None and find_vehicle(vehicle_rows, low):
            result["vehicle"] = find_vehicle(vehicle_rows, low)
        elif number(tok) is None:
            cities.append(tok)
        i += 1
    if len(cities) >= 2:
        result["origin"], result["destination"] = cities[0], cities[1]
    return result


def format_result(calc: Calculation) -> str:
    e = html.escape
    lines = [f"💰 <b>Расчёт цены</b> · {e(calc.route)} · {e(calc.vehicle)}", ""]
    lines.append(f"Расстояние: {calc.km:,.0f} км".replace(",", " "))
    if calc.own:
        lines += [
            f"Пробег с обратным: {calc.total_km:,.0f} км, дней: {calc.days}".replace(",", " "),
            "",
            f"⛽ Топливо: {money(calc.fuel)}",
            f"👤 Водитель: {money(calc.driver)}",
            f"🔧 Постоянные расходы: {money(calc.fixed)}",
            f"📎 Прочие: {money(calc.other)}",
        ]
    else:
        lines += ["", f"🚚 Наёмная машина: {money(calc.hired)}"]
    lines += [
        f"<b>Себестоимость: {money(calc.cost)}</b>",
        "",
        f"Маржа {calc.margin_pct:g}% → <b>минимальная цена: {money(calc.min_price)}</b> без НДС",
        f"С НДС {calc.vat_pct:g}%: <b>{money(calc.min_price_vat)}</b>",
    ]
    if calc.market:
        avg = sum(calc.market) / len(calc.market)
        diff = (calc.min_price - avg) / avg * 100
        lines.append(f"\n📊 Рынок: {len(calc.market)} ставок, средняя {money(avg)} ({diff:+.0f}% к нашей мин. цене)")
    else:
        lines.append("\n📊 Ставок рынка по этому маршруту пока нет")
    if calc.record_id:
        lines.append(f"\nЗаписано в «Расчёты цены»: {e(calc.record_id)} (черновик)")
    return "\n".join(lines)


# ---------------------------------------------------------------- agent

class Economist:
    def __init__(self, base_factory):
        self.base_factory = base_factory

    def _base(self) -> Base:
        base = self.base_factory()
        if base is None:
            raise RuntimeError("Google Sheets не настроен")
        return base

    def setup(self, base: Optional[Base] = None) -> Dict[str, int]:
        """Fill empty parameter sheets with starting rows. Never overwrites existing rows."""
        base = base or self._base()
        added = {}
        if not base.records(VEHICLE_SHEET):
            added[VEHICLE_SHEET] = base.append_many(VEHICLE_SHEET, DEFAULT_VEHICLES)
        if not base.records(PRICE_SHEET):
            added[PRICE_SHEET] = base.append_many(PRICE_SHEET, [
                {"Параметр": n, "Значение": v, "Описание": d} for n, v, d in DEFAULT_PRICE_PARAMS])
        if not base.records(DISTANCE_SHEET):
            added[DISTANCE_SHEET] = base.append_many(DISTANCE_SHEET, [
                {"Откуда": a, "Куда": b, "Км": km, "Заметки": "примерно, проверить"} for a, b, km in DEFAULT_DISTANCES])
        if added:
            base.log(AGENT, "Созданы стартовые параметры", ", ".join(f"{k}: {v}" for k, v in added.items()))
        return added

    def calculate(self, origin: str = "", destination: str = "", vehicle: str = "",
                  km: Optional[float] = None, hired_rate: Optional[float] = None,
                  purpose: str = "", save: bool = True) -> Calculation:
        base = self._base()
        vehicles = base.records(VEHICLE_SHEET)
        row = find_vehicle(vehicles, vehicle)
        if row is None:
            names = ", ".join(v.get("Тип машины", "") for v in vehicles) or "лист пуст"
            raise ValueError(f"Тип машины «{vehicle}» не найден. Есть: {names}")
        if km is None:
            if not (origin and destination):
                raise ValueError("Укажите два города или расстояние, например 450км")
            km = find_distance(base.records(DISTANCE_SHEET), origin, destination)
            if km is None:
                raise ValueError(f"Нет расстояния {origin} — {destination}. Добавьте его в лист «Расстояния» или укажите км.")
        try:
            market = market_rates(base.records("Ставки рынка"), origin, destination, row.get("Тип машины", ""))
        except Exception:
            market = []
        if hired_rate == 0.0 and market:
            hired_rate = sum(market) / len(market)
        elif hired_rate == 0.0:
            raise ValueError("Для наёмной машины укажите её цену: … наём 4500000")
        calc = compute(row, km, price_params(base.records(PRICE_SHEET)), origin, destination, hired_rate, market)
        if save:
            calc.record_id = new_id("R")
            base.append(RESULT_SHEET, {
                "ID": calc.record_id, "Дата": stamp(), "Для (тендер / сделка)": purpose,
                "Маршрут": f"{calc.route} ({calc.km:g} км)", "Тип машины": calc.vehicle,
                "Своя / наёмная": "своя" if calc.own else "наёмная",
                "Топливо (UZS)": round(calc.fuel), "Водитель (UZS)": round(calc.driver),
                "Прочие расходы (UZS)": round(calc.fixed + calc.other + calc.hired),
                "Себестоимость (UZS)": round(calc.cost), "Маржа (%)": calc.margin_pct,
                "Минимальная цена (UZS)": round(calc.min_price), "Итоговая цена (UZS)": "",
                "Статус": "Черновик", "Утвердил": "",
            })
            base.log(AGENT, "Расчёт цены", f"{calc.route}, {calc.vehicle}: себестоимость {round(calc.cost)}, "
                     f"мин. цена {round(calc.min_price)}", calc.record_id)
        return calc

    def answer(self, text: str) -> str:
        """Reply to a /price message in Telegram."""
        base = self._base()
        args = parse_command(text, base.records(VEHICLE_SHEET))
        if not args["vehicle"] or not (args["km"] or (args["origin"] and args["destination"])):
            return HELP
        try:
            calc = self.calculate(args["origin"], args["destination"], args["vehicle"]["Тип машины"],
                                  args["km"], args["hired"], purpose="запрос в Telegram")
        except MissingParams as exc:
            return ("⚠️ Не заполнены параметры в листе «Параметры машин»:\n• "
                    + "\n• ".join(html.escape(m) for m in exc.missing)
                    + "\n\nПока можно посчитать наёмную машину: <code>… наём 4500000</code>")
        except ValueError as exc:
            return f"⚠️ {html.escape(str(exc))}\n\n{HELP}"
        return format_result(calc)
