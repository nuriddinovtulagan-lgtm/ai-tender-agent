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

from .base import Base, new_id, now_local, parse_date, stamp

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
    ("Накладные на рейс (сум)", "0", "Расходы компании на один рейс: диспетчер, связь, документы, погрузка."),
    ("Риск (%)", "0", "Запас на простой, порчу, штрафы — % от себестоимости."),
    ("Отсрочка оплаты (дней)", "0", "Через сколько дней заказчик обычно платит (госзаказчики — часто 30–60)."),
    ("Стоимость денег (% в месяц)", "0", "Во что обходятся деньги, отданные перевозчику до оплаты заказчиком."),
    ("Ставки рынка за (дней)", "90", "Для средней цены найма берутся ставки не старше этого срока."),
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
OWN_WORDS = {"своя", "свой", "собственная", "own"}

HELP = (
    "💰 <b>Экономист — расчёт цены рейса</b>\n\n"
    "<code>/price Ташкент Нукус тент</code> — наёмная машина по средней цене из «Ставок рынка»\n"
    "<code>/price Ташкент Нукус тент наём 4500000</code> — наёмная машина за 4,5 млн\n"
    "<code>/price Ташкент Нукус трал своя</code> — своя машина (нужны «Параметры машин»)\n"
    "<code>/price 450км контейнеровоз своя</code> — по расстоянию\n\n"
    "📝 Записать цену найма: <code>/rate Ташкент Нукус тент 4500000</code>\n\n"
    "Параметры — в листах «Параметры машин», «Параметры цены», «Расстояния»."
)

RATE_HELP = (
    "📝 <b>Записать цену найма машины</b>\n\n"
    "<code>/rate Ташкент Нукус тент 4500000</code>\n"
    "<code>/rate Ташкент Самарканд трал 20т 6000000</code> — с тоннажем\n\n"
    "Записывается в «Ставки рынка». По этим ценам Экономист считает наёмные рейсы."
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
    hired_source: str = ""
    overhead: float = 0
    risk: float = 0
    financing: float = 0
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


def market_rates(rows: List[Dict[str, str]], origin: str, destination: str, vehicle: str,
                 max_age_days: Optional[float] = None) -> List[float]:
    a, b = city_key(origin), city_key(destination)
    v = (vehicle or "").lower()[:4]
    now = now_local()
    out = []
    for row in rows:
        if max_age_days:
            when = parse_date(row.get("Дата"))
            if when is not None and (now - when).days > max_age_days:
                continue
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
            market: Optional[List[float]] = None, hired_source: str = "") -> Calculation:
    """Pure calculation. Raises MissingParams if an own-truck row is not filled."""
    margin = prices.get("Маржа (%)", 15.0)
    vat = prices.get("НДС (%)", 12.0)
    step = prices.get("Округление (сум)", 10000.0)
    calc = Calculation(origin, destination, vehicle.get("Тип машины", ""), km,
                       own=hired_rate is None, margin_pct=margin, vat_pct=vat, market=market or [])
    if hired_rate is not None:
        calc.hired = hired_rate
        calc.hired_source = hired_source or "указана"
        base_cost = hired_rate
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
        base_cost = calc.fuel + calc.driver + calc.fixed + calc.other
    calc.overhead = prices.get("Накладные на рейс (сум)", 0.0)
    calc.risk = (base_cost + calc.overhead) * prices.get("Риск (%)", 0.0) / 100
    calc.financing = (base_cost * prices.get("Стоимость денег (% в месяц)", 0.0) / 100
                      * prices.get("Отсрочка оплаты (дней)", 0.0) / 30)
    calc.cost = base_cost + calc.overhead + calc.risk + calc.financing
    calc.min_price = round_up(calc.cost * (1 + margin / 100), step)
    calc.min_price_vat = round_up(calc.min_price * (1 + vat / 100), step)
    return calc


def parse_command(text: str, vehicle_rows: List[Dict[str, str]]) -> Dict[str, Any]:
    """'/price Ташкент - Нукус трал наём 4500000' -> {origin, destination, vehicle, km, hired}."""
    body = re.sub(r"^/price(@\w+)?", "", (text or "").strip(), flags=re.I)
    body = re.sub(r"(\d)\s+(км|km)\b", r"\1км", body, flags=re.I)
    tokens = [t for t in re.split(r"[\s,;→—–\-]+", body) if t]
    result: Dict[str, Any] = {"origin": "", "destination": "", "vehicle": None, "km": None, "hired": None,
                              "own": False}
    cities: List[str] = []
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        low = tok.lower()
        km_match = re.fullmatch(r"(\d+(?:[.,]\d+)?)(км|km)", low)
        if km_match:
            result["km"] = number(km_match.group(1))
        elif low in OWN_WORDS:
            result["own"] = True
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
        lines += ["", f"🚚 Наёмная машина: {money(calc.hired)} ({html.escape(calc.hired_source)})"]
    if calc.overhead:
        lines.append(f"🗂 Накладные на рейс: {money(calc.overhead)}")
    if calc.risk:
        lines.append(f"🛡 Риск: {money(calc.risk)}")
    if calc.financing:
        lines.append(f"⏳ Отсрочка оплаты: {money(calc.financing)}")
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
        known = {(r.get("Параметр") or "").strip() for r in base.records(PRICE_SHEET)}
        new_params = [{"Параметр": n, "Значение": v, "Описание": d}
                      for n, v, d in DEFAULT_PRICE_PARAMS if n not in known]
        if new_params:
            added[PRICE_SHEET] = base.append_many(PRICE_SHEET, new_params)
        if not base.records(DISTANCE_SHEET):
            added[DISTANCE_SHEET] = base.append_many(DISTANCE_SHEET, [
                {"Откуда": a, "Куда": b, "Км": km, "Заметки": "примерно, проверить"} for a, b, km in DEFAULT_DISTANCES])
        if added:
            base.log(AGENT, "Созданы стартовые параметры", ", ".join(f"{k}: {v}" for k, v in added.items()))
        return added

    def calculate(self, origin: str = "", destination: str = "", vehicle: str = "",
                  km: Optional[float] = None, hired_rate: Optional[float] = None,
                  purpose: str = "", save: bool = True, own: bool = False) -> Calculation:
        """hired_rate: a number = hired truck at that price; 0 = hired at the market average;
        None = market average if there are rates (the company mostly hires), else own truck."""
        base = self._base()
        vehicles = base.records(VEHICLE_SHEET)
        row = find_vehicle(vehicles, vehicle)
        if row is None:
            names = ", ".join(v.get("Тип машины", "") for v in vehicles) or "лист пуст"
            raise ValueError(f"Тип машины «{vehicle}» не найден. Есть: {names}")
        if km is None and origin and destination:
            km = find_distance(base.records(DISTANCE_SHEET), origin, destination)
        prices = price_params(base.records(PRICE_SHEET))
        try:
            market = market_rates(base.records("Ставки рынка"), origin, destination, row.get("Тип машины", ""),
                                  prices.get("Ставки рынка за (дней)", 90.0))
        except Exception:
            market = []
        source = ""
        if not own and hired_rate in (None, 0.0):
            if market:
                hired_rate = sum(market) / len(market)
                source = f"средняя по {len(market)} ставкам"
            elif hired_rate == 0.0 or km is None:
                raise ValueError(f"Нет ставок найма по маршруту {origin} — {destination} ({row.get('Тип машины')}). "
                                 "Укажите цену: … наём 4500000, или запишите ставку: /rate …")
            elif any(number(row.get(n)) is None for n in REQUIRED_VEHICLE):
                raise ValueError(f"Нет ставок найма по маршруту {origin} — {destination} ({row.get('Тип машины')}). "
                                 "Укажите цену: … наём 4500000, или запишите ставку: /rate …")
            else:
                hired_rate = None          # no market data, own-truck parameters filled: own truck
        if km is None:
            if hired_rate is None:
                if not (origin and destination):
                    raise ValueError("Укажите два города или расстояние, например 450км")
                raise ValueError(f"Нет расстояния {origin} — {destination}. Добавьте его в лист «Расстояния» или укажите км.")
            km = 0.0
        calc = compute(row, km, prices, origin, destination, hired_rate, market, source)
        if save:
            calc.record_id = new_id("R")
            km_text = f" ({calc.km:g} км)" if calc.km else ""
            base.append(RESULT_SHEET, {
                "ID": calc.record_id, "Дата": stamp(), "Для (тендер / сделка)": purpose,
                "Маршрут": f"{calc.route}{km_text}", "Тип машины": calc.vehicle,
                "Своя / наёмная": "своя" if calc.own else f"наёмная ({calc.hired_source})",
                "Топливо (UZS)": round(calc.fuel), "Водитель (UZS)": round(calc.driver),
                "Прочие расходы (UZS)": round(calc.fixed + calc.other + calc.hired + calc.overhead
                                              + calc.risk + calc.financing),
                "Себестоимость (UZS)": round(calc.cost), "Маржа (%)": calc.margin_pct,
                "Минимальная цена (UZS)": round(calc.min_price), "Итоговая цена (UZS)": "",
                "Статус": "Черновик", "Утвердил": "",
            })
            base.log(AGENT, "Расчёт цены", f"{calc.route}, {calc.vehicle}: себестоимость {round(calc.cost)}, "
                     f"мин. цена {round(calc.min_price)}", calc.record_id)
        return calc

    def add_rate(self, text: str, who: str = "") -> str:
        """/rate Ташкент Нукус тент [20т] 4500000 -> a row in "Ставки рынка"."""
        base = self._base()
        body = re.sub(r"^/rate(@\w+)?", "", (text or "").strip(), flags=re.I)
        tonnage = ""
        t_match = re.search(r"(\d+(?:[.,]\d+)?)\s*(т|тонн\w*|t)\b", body, flags=re.I)
        if t_match:
            tonnage = t_match.group(1).replace(",", ".")
            body = body[:t_match.start()] + body[t_match.end():]
        vehicles = base.records(VEHICLE_SHEET)
        tokens = [t for t in re.split(r"[\s,;→—–\-]+", body) if t]
        cities, vehicle, rate = [], None, None
        for tok in tokens:
            value = number(tok)
            if value is not None and value >= 1000:
                rate = value
            elif vehicle is None and find_vehicle(vehicles, tok):
                vehicle = find_vehicle(vehicles, tok)
            elif value is None:
                cities.append(tok)
        if len(cities) < 2 or not vehicle or not rate:
            return RATE_HELP
        origin, destination = cities[0], cities[1]
        base.append("Ставки рынка", {
            "Дата": stamp(), "Откуда": origin, "Куда": destination,
            "Тип машины": vehicle["Тип машины"], "Тоннаж (т)": tonnage, "Ставка": rate,
            "Валюта": "UZS", "Источник": "диспетчер (Telegram)", "Ссылка": "", "Добавил": who,
        })
        base.log(AGENT, "Записана ставка найма", f"{origin} — {destination}, {vehicle['Тип машины']}: {rate:.0f}", "", who)
        prices = price_params(base.records(PRICE_SHEET))
        rates = market_rates(base.records("Ставки рынка"), origin, destination, vehicle["Тип машины"],
                             prices.get("Ставки рынка за (дней)", 90.0))
        avg = sum(rates) / len(rates) if rates else rate
        return (f"📝 Записано: {html.escape(origin)} — {html.escape(destination)}, "
                f"{html.escape(vehicle['Тип машины'])}: <b>{money(rate)}</b>\n"
                f"Ставок по маршруту: {len(rates)}, средняя {money(avg)}")

    def answer(self, text: str) -> str:
        """Reply to a /price message in Telegram."""
        base = self._base()
        args = parse_command(text, base.records(VEHICLE_SHEET))
        if not args["vehicle"] or not (args["km"] or (args["origin"] and args["destination"])):
            return HELP
        try:
            calc = self.calculate(args["origin"], args["destination"], args["vehicle"]["Тип машины"],
                                  args["km"], args["hired"], purpose="запрос в Telegram", own=args["own"])
        except MissingParams as exc:
            return ("⚠️ Для своей машины не заполнены параметры в листе «Параметры машин»:\n• "
                    + "\n• ".join(html.escape(m) for m in exc.missing)
                    + "\n\nНаёмную машину можно посчитать сразу: <code>… наём 4500000</code>")
        except ValueError as exc:
            return f"⚠️ {html.escape(str(exc))}\n\n{HELP}"
        return format_result(calc)
