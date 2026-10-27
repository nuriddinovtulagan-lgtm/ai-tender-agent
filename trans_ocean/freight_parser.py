"""Parser for freight ads in Uzbek Telegram groups (Russian, Uzbek Latin and Uzbek Cyrillic).

Typical ads:
  "Toshkent - Buxoro fura tent 20t yuk bor 5 mln +998 90 123 45 67"
  "Тошкентдан Самарқандга трал керак 4 500 000 сўм"
  "Фура бўш Бухорода, Тошкентга юк керак 90 1234567"
  "Ташкент → Нукус, реф 20 т, 9,5 млн, тел. 99 765 43 21"

parse_ad(text) -> Ad with: kind ("Груз" = cargo needs a truck, "Машина" = a free truck needs cargo),
origin, destination (canonical Russian names, the same the Economist uses), vehicle type,
tonnage, price + currency, phones. Pure functions, no network.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

KIND_CARGO = "Груз"
KIND_TRUCK = "Машина"

# Canonical name -> word stems (lower-case, apostrophes normalised to "'").
PLACES: List[Tuple[str, Tuple[str, ...]]] = [
    ("Ташкент", ("toshkent", "tashkent", "ташкент", "тошкент", "toshkentdan", "tashken")),
    ("Самарканд", ("samarq", "samark", "самарк", "самарқ")),
    ("Бухара", ("buxor", "bukhar", "бухар", "бухор")),
    ("Навои", ("navoi", "навои", "навоий")),
    ("Карши", ("qarshi", "karshi", "карши", "қарши")),
    ("Термез", ("termiz", "termez", "термез", "термиз")),
    ("Джизак", ("jizzax", "jizzak", "jizax", "джизак", "джиззак", "жиззах", "жизах")),
    ("Гулистан", ("gulist", "гулист")),
    ("Андижан", ("andijon", "andijan", "андижан", "андижон")),
    ("Наманган", ("namangan", "наманган")),
    ("Фергана", ("farg'on", "fargon", "fergan", "фергана", "фарғон", "фаргон")),
    ("Коканд", ("qo'qon", "qoqon", "kokand", "коканд", "қўқон", "кукон")),
    ("Ургенч", ("urganch", "urgench", "ургенч", "урганч")),
    ("Нукус", ("nukus", "нукус")),
    ("Хива", ("xiva", "khiva", "хива")),
    ("Чирчик", ("chirchiq", "chirchik", "чирчик", "чирчиқ")),
    ("Алмалык", ("olmaliq", "almalyk", "алмалык", "олмалиқ", "олмалик")),
    ("Ангрен", ("angren", "ангрен")),
    ("Бекабад", ("bekobod", "bekabad", "бекабад", "бекобод")),
    ("Шахрисабз", ("shahrisabz", "шахрисабз", "шаҳрисабз")),
    ("Денау", ("denov", "denau", "денов", "денау")),
    ("Зарафшан", ("zarafshon", "zarafshan", "зарафшан", "зарафшон")),
    ("Маргилан", ("marg'ilon", "margilon", "маргилан", "марғилон")),
    ("Кунград", ("qo'ng'irot", "qongirot", "kungrad", "кунград", "қўнғирот")),
    ("Беруни", ("beruniy", "beruni", "беруни")),
    ("Янгиюль", ("yangiyo'l", "yangiyol", "янгиюль", "янгийўл")),
    # neighbours and frequent international points
    ("Сарыагаш", ("sariag", "saryag", "сариаг", "сарыаг")),
    ("Жибек Жолы", ("jibek", "жибек")),
    ("Москва", ("moskva", "moscow", "москв", "маскв")),
    ("Алматы", ("almaty", "almati", "алматы", "алмата")),
    ("Бишкек", ("bishkek", "бишкек")),
    ("Душанбе", ("dushanbe", "душанбе")),
    ("Казахстан", ("qozog'iston", "qozogiston", "kazakhstan", "казахстан", "қозоғистон")),
    ("Кыргызстан", ("qirg'iziston", "qirgiziston", "кыргызстан", "киргизия", "қирғизистон")),
    ("Россия", ("rossiya", "russia", "россия", "россию", "росси", "русия")),
    ("Китай", ("xitoy", "china", "китай", "хитой")),
    ("Турция", ("turkiya", "turkey", "турция", "турцию", "туркия")),
    ("Литва", ("litva", "литв")),
    ("Европа", ("yevropa", "evropa", "европ", "европ")),
]

# (vehicle type, regex). The first match wins, so special trailers go before "fura".
VEHICLES: List[Tuple[str, str]] = [
    ("Трал", r"\bтрал\w*|\btral\w*|низкорам|\bpanelovoz"),
    ("Рефрижератор", r"\bреф\b|\bref\b|рефриж|refrij|refrig|\bреф\w*жератор|muzlatgich"),
    ("Изотерм", r"изотерм|izoterm"),
    ("Контейнеровоз", r"контейнер|konteyner|kontainer|\b(20|40)\s*(фут|fut|ft)"),
    ("Самосвал", r"самосвал|samosval"),
    ("Бортовой", r"\bборт\w*|\bbort\w*|шаланд|shaland|площадк|ploshadk"),
    ("Тент (фура)", r"\bтент\w*|\btent\w*|фура|fura|еврофур|yevrofur|\bmega\b|\bмега\b"),
    ("Малотоннажный", r"isuzu|исузу|\blabo\b|\bлабо\b|damas|дамас|porter|портер|газел|gazel"),
    ("Камаз", r"kamaz|камаз"),
]

TRUCK_WORDS = re.compile(
    r"bo'sh|бўш|bosh\s+(mashina|moshina|fura)|свобод|mashina\s+bor|moshina\s+bor|машина\s+бор|"
    r"мошина\s+бор|машина\s+есть|yuk\s+kerak|юк\s+керак|груз\s+нуж|ищу\s+груз|ищем\s+груз|yuk\s+qidir|"
    r"юк\s+қидир|загружусь|возьму\s+груз|попутн|poputn", re.I)
CARGO_WORDS = re.compile(
    r"yuk\s+bor|юк\s+бор|груз\s+есть|есть\s+груз|mashina\s+kerak|moshina\s+kerak|машина\s+керак|"
    r"мошина\s+керак|нужна\s+машин|нужен\s+транспорт|требуется\s+машин|kerak\s+(fura|tral|ref|tent|isuzu)|"
    r"(фура|трал|реф|тент|исузу)\s+керак|(fura|tral|ref|tent|isuzu|labo)\s+kerak|погрузк|pogruzk|yuklash|юклаш|yuk:|груз:", re.I)

PHONE_RE = re.compile(r"(?<!\d)(?:\+?\s*998[\s\-().]*)?\(?\d{2}\)?[\s\-.]*\d{3}[\s\-.]*\d{2}[\s\-.]*\d{2}(?!\d)")
MOBILE_CODES = {"20", "33", "50", "55", "61", "62", "65", "66", "67", "69", "70", "71", "72", "73", "74", "75",
                "76", "77", "78", "79", "88", "90", "91", "93", "94", "95", "97", "98", "99"}
TONNAGE_RE = re.compile(r"(?<![\d.,])(\d{1,2}(?:[.,]\d)?)\s*(?:т|тн|тонн\w*|тона\w*|t|tn|tonna\w*|тонна)(?![a-zа-я])",
                        re.I)
MLN_RE = re.compile(r"(?<![\d.,])(\d{1,3}(?:[.,]\d{1,3})?)\s*(?:mln|млн|million|миллион|mil\b|милл)", re.I)
MING_RE = re.compile(r"(?<![\d.,])(\d{2,4}(?:[.,]\d{1,3})?)\s*(?:ming|минг|тыс|k\b|к\b)", re.I)
USD_RE = re.compile(r"(?:\$\s*(\d{2,5}(?:[ .,]\d{3})?)|(\d{2,5}(?:[ .,]\d{3})?)\s*(?:\$|usd|dollar|доллар|долл|у\.?е))",
                    re.I)
BIG_NUMBER_RE = re.compile(r"(?<![\d.,])(\d{1,3}(?:[ .,]\d{3}){1,3}|\d{6,9})(?![\d.,]*\d)")

UZS_MIN, UZS_MAX = 200_000, 150_000_000
USD_MIN, USD_MAX = 100, 20_000


@dataclass
class Ad:
    text: str
    kind: str = ""
    origin: str = ""
    destination: str = ""
    vehicle: str = ""
    tonnage: str = ""
    price: float = 0
    currency: str = ""
    phones: List[str] = field(default_factory=list)

    @property
    def useful(self) -> bool:
        """Worth saving: a route (two places) and a way to act on it (price or phone);
        a free truck needs only where it stands and a phone."""
        if self.kind == KIND_TRUCK and self.origin and self.phones:
            return True
        return bool(self.origin and self.destination and (self.price or self.phones))

    @property
    def is_rate(self) -> bool:
        return bool(self.origin and self.destination and self.price and self.vehicle)


def normalize(text: str) -> str:
    t = (text or "").lower().replace("ё", "е")
    t = re.sub(r"[‘’ʻʼ`´]", "'", t)
    return t


def ad_key(text: str) -> str:
    """Same ad reposted in many groups -> same key (digits and letters only)."""
    core = re.sub(r"[^0-9a-zа-яўқғҳ]+", "", normalize(text))
    return hashlib.sha1(core[:400].encode("utf-8")).hexdigest()[:16]


def find_phones(text: str) -> Tuple[List[str], str]:
    """Uzbek phone numbers in +998 XX XXX XX XX form, and the text with them removed."""
    phones: List[str] = []
    spans = []
    for m in PHONE_RE.finditer(text):
        digits = re.sub(r"\D", "", m.group(0))
        if len(digits) == 12 and digits.startswith("998"):
            digits = digits[3:]
        if len(digits) != 9 or digits[:2] not in MOBILE_CODES:
            continue
        phone = f"+998 {digits[:2]} {digits[2:5]} {digits[5:7]} {digits[7:]}"
        if phone not in phones:
            phones.append(phone)
        spans.append(m.span())
    cleaned = text
    for start, end in reversed(spans):
        cleaned = cleaned[:start] + " " + cleaned[end:]
    return phones, cleaned


def _to_float(raw: str) -> Optional[float]:
    try:
        return float(raw.replace(",", ".").replace(" ", ""))
    except ValueError:
        return None


def find_price(text: str) -> Tuple[float, str]:
    """(price, currency) or (0, ""). Phones must be removed from text first."""
    low = normalize(text)
    for m in USD_RE.finditer(low):
        raw = (m.group(1) or m.group(2) or "").replace(" ", "").replace(",", "").replace(".", "")
        value = _to_float(raw)
        if value and USD_MIN <= value <= USD_MAX:
            return value, "USD"
    for m in MLN_RE.finditer(low):
        value = _to_float(m.group(1))
        if value and UZS_MIN <= value * 1_000_000 <= UZS_MAX:
            return value * 1_000_000, "UZS"
    for m in MING_RE.finditer(low):
        value = _to_float(m.group(1))
        if value and UZS_MIN <= value * 1000 <= UZS_MAX:
            return value * 1000, "UZS"
    for m in BIG_NUMBER_RE.finditer(low):
        value = _to_float(re.sub(r"[ .,]", "", m.group(1)))
        if value and UZS_MIN <= value <= UZS_MAX:
            return value, "UZS"
    return 0, ""


def find_places(text: str) -> List[Tuple[str, str]]:
    """Places in order of appearance as (canonical name, the word as written)."""
    found: List[Tuple[int, str, str]] = []
    low = normalize(text)
    for m in re.finditer(r"[a-zа-яўқғҳ']+", low):
        word = m.group(0).strip("'")
        if len(word) < 3:
            continue
        for name, stems in PLACES:
            if any(word.startswith(stem) for stem in stems):
                if not any(f[1] == name for f in found):
                    found.append((m.start(), name, word))
                break
    found.sort()
    return [(name, word) for _, name, word in found]


def order_route(places: List[Tuple[str, str]], text: str) -> Tuple[str, str]:
    """Origin and destination. Uzbek -dan/-дан marks "from", -ga/-ка/-га "to";
    Russian "из X" / "в X"; otherwise the order of appearance."""
    if len(places) < 2:
        return (places[0][0] if places else ""), ""
    (a, wa), (b, wb) = places[0], places[1]
    from_suffix = ("dan", "дан", "ден", "tan", "тан")
    to_suffix = ("ga", "ka", "qa", "га", "ка", "қа", "гa")
    if (wa.endswith(to_suffix) and wb.endswith(from_suffix)):
        return b, a
    low = normalize(text)
    if re.search(rf"\bиз\s+{re.escape(wb[:5])}", low) and re.search(rf"\b(в|во|до|на)\s+{re.escape(wa[:5])}", low):
        return b, a
    return a, b


def find_vehicle(text: str) -> str:
    low = normalize(text)
    for name, pattern in VEHICLES:
        if re.search(pattern, low):
            return name
    return ""


def find_tonnage(text: str) -> str:
    m = TONNAGE_RE.search(normalize(text))
    if not m:
        return ""
    value = _to_float(m.group(1))
    if value is None or not 0.5 <= value <= 80:
        return ""
    return f"{value:g}"


def find_kind(text: str) -> str:
    low = normalize(text)
    truck, cargo = bool(TRUCK_WORDS.search(low)), bool(CARGO_WORDS.search(low))
    if truck and not cargo:
        return KIND_TRUCK
    if cargo and not truck:
        return KIND_CARGO
    if truck and cargo:
        # "bo'sh fura ... yuk kerak" is a truck; "yuk bor ... mashina kerak" is cargo
        return KIND_TRUCK if re.search(r"bo'sh|бўш|свобод", low) else KIND_CARGO
    return KIND_CARGO   # most ads in these groups are cargo looking for a truck


def parse_ad(text: str) -> Ad:
    ad = Ad(text=(text or "").strip())
    if not ad.text:
        return ad
    ad.phones, rest = find_phones(ad.text)
    ad.vehicle = find_vehicle(rest)
    ad.tonnage = find_tonnage(rest)
    rest_no_tons = TONNAGE_RE.sub(" ", normalize(rest))
    ad.price, ad.currency = find_price(rest_no_tons)
    ad.origin, ad.destination = order_route(find_places(rest), rest)
    ad.kind = find_kind(rest)
    return ad


def split_ads(text: str) -> List[str]:
    """Some posts list several loads, one per line or block. Split when two or more lines
    each have their own route; otherwise keep the post whole."""
    lines = [line for line in re.split(r"\n\s*\n|\n(?=\s*(?:\d+[.)]|[-•▪️🔹🔸✅➡️📍🚛🚚]))", text or "") if line.strip()]
    if len(lines) < 2:
        return [text]
    with_route = [line for line in lines if len(find_places(line)) >= 2]
    return with_route if len(with_route) >= 2 else [text]
