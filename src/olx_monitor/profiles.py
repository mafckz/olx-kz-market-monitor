from __future__ import annotations

import re
import unicodedata
from urllib.parse import urlparse

from olx_monitor.models import ListingCard, ListingDetails, MatchDecision, SearchProfile

OLX_CATEGORY_PATHS: dict[str, tuple[str, str]] = {
    "настольные компьютеры": (
        "/elektronika/kompyutery-i-komplektuyuschie/nastolnye-kompyutery",
        "Компьютеры и комплектующие → Настольные компьютеры",
    ),
    "компьютеры": (
        "/elektronika/kompyutery-i-komplektuyuschie/nastolnye-kompyutery",
        "Компьютеры и комплектующие → Настольные компьютеры",
    ),
    "ноутбуки": ("/elektronika/noutbuki-i-aksesuary", "Электроника → Ноутбуки и аксессуары"),
    "видеокарты": (
        "/elektronika/kompyutery-i-komplektuyuschie/komplektuyuschie-i-aksesuary",
        "Компьютеры и комплектующие → Комплектующие и аксессуары",
    ),
    "комплектующие": (
        "/elektronika/kompyutery-i-komplektuyuschie/komplektuyuschie-i-aksesuary",
        "Компьютеры и комплектующие → Комплектующие и аксессуары",
    ),
    "мониторы": (
        "/elektronika/kompyutery-i-komplektuyuschie/monitory",
        "Компьютеры и комплектующие → Мониторы",
    ),
    "телефоны": ("/elektronika/telefony-i-aksesuary", "Электроника → Телефоны и аксессуары"),
}

_NUMBER = r"\d+(?:[\s\u00a0\u202f.]\d{3})*"
_UNIT = r"(?:тыс(?:яч(?:а|и)?)?\.?|тысяч|к\b|k\b|млн\.?|миллион(?:а|ов)?|₸|тенге)?"
_RANGE_RE = re.compile(
    rf"(?P<low>{_NUMBER})\s*(?P<low_unit>{_UNIT})\s*(?:-|–|—|до)\s*"
    rf"(?P<high>{_NUMBER})\s*(?P<high_unit>{_UNIT})",
    re.IGNORECASE,
)
_SINGLE_PRICE_RE = re.compile(rf"(?P<value>{_NUMBER})\s*(?P<unit>{_UNIT})", re.IGNORECASE)
_RTX_RE = re.compile(r"\brtx\s*[- ]?\s*3080\b", re.IGNORECASE)


def _number_value(raw: str, unit: str | None) -> int:
    number = int(re.sub(r"\D", "", raw))
    unit = (unit or "").casefold()
    if "млн" in unit or "миллион" in unit:
        return number * 1_000_000
    if "тыс" in unit or unit in {"к", "k"}:
        return number * 1_000
    return number


def _price_range(text: str, default: tuple[int, int] | None) -> tuple[int, int] | None:
    match = _RANGE_RE.search(text)
    if match:
        low_unit = match.group("low_unit") or match.group("high_unit")
        high_unit = match.group("high_unit") or low_unit
        low = _number_value(match.group("low"), low_unit)
        high = _number_value(match.group("high"), high_unit)
        return (low, high) if low <= high else (high, low)
    if default is None:
        return None
    low, high = default
    for match in _SINGLE_PRICE_RE.finditer(text):
        unit = (match.group("unit") or "").casefold()
        raw = match.group("value")
        value = _number_value(raw, unit)
        if not unit and value < 10_000:
            continue
        before = text[max(0, match.start() - 24) : match.start()].casefold()
        after = text[match.end() : match.end() + 20].casefold()
        if re.search(r"(?:до|не дороже|дешевле)\s*$", before):
            high = value
        elif re.search(r"(?:от|выше|дороже)\s*$", before):
            low = value
        elif re.match(r"\s*(?:тенге|₸)", after):
            if value >= 10_000:
                high = value
        return low, high
    return default


def _find_category(text: str) -> tuple[str | None, str | None]:
    for hint, (path, label) in OLX_CATEGORY_PATHS.items():
        if hint in text.casefold():
            return path, label
    if re.search(r"\b(?:пк|системник|игровой компьютер)\b", text, re.I):
        return OLX_CATEGORY_PATHS["настольные компьютеры"]
    return None, None


def _extract_target(text: str) -> tuple[str, list[str], list[str]]:
    rtx = _RTX_RE.search(text)
    if rtx:
        ti = re.search(r"\bRTX\s*[- ]?\s*3080\s*TI\b", text, re.I)
        if ti:
            return "RTX 3080 Ti", ["RTX 3080 Ti", "RTX3080Ti"], []
        return "NVIDIA GeForce RTX 3080", ["RTX 3080", "RTX3080"], ["RTX 3080 Ti", "RTX3080Ti"]
    known_patterns = (
        (r"\bRTX\s*\d{4}(?:\s*TI|\s*SUPER)?\b", "RTX"),
        (r"\bGTX\s*\d{3,4}(?:\s*TI|\s*SUPER)?\b", "GTX"),
        (r"\bRX\s*\d{3,4}(?:\s*XT|\s*XTX)?\b", "RX"),
        (r"\biPhone\s*\d{1,2}(?:\s*(?:Pro\s*)?(?:Max|Plus))?\b", "iPhone"),
        (r"\bMacBook\s*(?:Pro|Air)?\s*(?:M\d)?\b", "MacBook"),
    )
    for pattern, _ in known_patterns:
        match = re.search(pattern, text, re.I)
        if match:
            target = re.sub(r"\s+", " ", match.group(0)).strip()
            return target, [target], []
    cleaned = text
    for pattern in (
        r"https?://\S+",
        r"\b(?:найди|ищи|поиск|ищу|нужен|нужна|нужно|куплю|подержанный|б/у|в хорошем состоянии)\b",
        r"\b(?:настольный компьютер|настольные компьютеры|компьютер|компьютеры|пк|"
        r"ноутбук|ноутбуки|видеокарта|видеокарты|монитор|мониторы|телефон|телефоны|комплектующие)\b",
        rf"{_NUMBER}\s*{_UNIT}",
        r"\b(?:алматы|астана|шымкент|караганда|по казахстану|казахстан|тенге|тысяч|тыс)\b",
    ):
        cleaned = re.sub(pattern, " ", cleaned, flags=re.I)
    cleaned = re.sub(r"[^\w\s.-]", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" .,-")
    if cleaned:
        return cleaned[:80], [cleaned[:80]], []
    return "", [], []


def parse_profile_request(text: str) -> tuple[dict[str, object] | None, list[str]]:
    text = text.strip()
    errors: list[str] = []
    if not text:
        return None, ["Напишите, что искать, например: «настольный ПК с RTX 3080, 200–500 тыс. ₸, Алматы»."]
    category_path, category_label = _find_category(text)
    supplied_url = re.search(r"https?://(?:www\.)?olx\.kz/(\S+)", text, re.I)
    if supplied_url:
        parsed = urlparse(supplied_url.group(0).rstrip(".,);"))
        candidate_path = parsed.path.rstrip("/")
        if (
            parsed.query
            or "/search/" in candidate_path
            or "/list/user/" in candidate_path
            or "/d/" in candidate_path
            or re.search(r"(?:^|/)q-[^/]+", candidate_path)
        ):
            errors.append("Передайте ссылку только на публичную категорию OLX, без поиска или профиля продавца.")
        else:
            category_path = candidate_path
            category_label = f"Категория OLX {candidate_path}"
    if not category_path:
        errors.append("Не понял категорию. Укажите товарную категорию или пришлите ссылку на категорию OLX.")
    target, include_terms, exclude_terms = _extract_target(text)
    if not target:
        errors.append("Не удалось выделить модель или товар для поиска.")
    desktop_path = OLX_CATEGORY_PATHS["настольные компьютеры"][0]
    initial_rtx_3080 = re.search(r"\brtx\s*[- ]?\s*3080\b", text, re.I)
    initial_default = (200_000, 500_000) if category_path == desktop_path and initial_rtx_3080 else None
    prices = _price_range(text, initial_default)
    if prices is None:
        errors.append("Укажите пределы цены, например «200–500 тыс. ₸».")
        prices = (0, 1_000_000_000)
    min_price, max_price = prices
    if min_price < 0 or max_price <= min_price:
        errors.append("Верхняя граница цены должна быть больше нижней.")
    city: str | None = None
    for known_city in ("Алматы", "Астана", "Шымкент", "Караганда"):
        if known_city.casefold() in text.casefold():
            city = known_city
            break
    country_scope = bool(re.search(r"весь казахстан|по казахстану|в казахстане|всей стране", text, re.I))
    if city is None and not country_scope:
        city = "Алматы"
    if country_scope:
        city = None
    if errors:
        return None, errors
    assert category_path and category_label
    name_target = "RTX 3080" if target == "NVIDIA GeForce RTX 3080" else target
    geography = city or "Казахстан"
    return (
        {
            "name": f"{name_target} · {category_label.split('→')[-1].strip()} · {geography}",
            "category_path": category_path,
            "category_label": category_label,
            "target": target,
            "include_terms": include_terms,
            "exclude_terms": exclude_terms,
            "min_price": min_price,
            "max_price": max_price,
            "city": city,
            "condition": "Б/у"
            if re.search(r"\b(?:б/у|подержанн)", text, re.I) or category_path == desktop_path
            else None,
            "mode": "classifier",
        },
        [],
    )


def _normalize(text: str) -> str:
    value = unicodedata.normalize("NFKC", text).casefold()
    value = re.sub(r"[^\w]+", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def classify_listing(profile: SearchProfile, card: ListingCard, details: ListingDetails | None = None) -> MatchDecision:
    if card.price is None or not profile.min_price <= card.price <= profile.max_price:
        return MatchDecision("rejected", "Цена отсутствует или вне заданного диапазона.")
    text = " ".join(part for part in (card.title, card.raw_text, details.description if details else "") if part)
    normalized = _normalize(text)
    if profile.city and not _normalize(card.location).startswith(_normalize(profile.city)):
        return MatchDecision("rejected", f"Локация не совпадает с городом {profile.city}.")
    if profile.condition == "Б/у" and card.condition == "Новый":
        return MatchDecision("rejected", "В объявлении указано состояние «Новый».")
    if profile.target.casefold() in {"nvidia geforce rtx 3080", "rtx 3080"}:
        if re.search(r"\brtx\s*[- ]?\s*3080\s*ti\b", text, re.I):
            return MatchDecision("rejected", "Указана RTX 3080 Ti, а профиль ищет RTX 3080.")
        if not _RTX_RE.search(text):
            return MatchDecision("uncertain", "Точное упоминание RTX 3080 не найдено в доступном тексте.")
        if re.search(r"\b(?:ноутбук|laptop|notebook)\b", normalized):
            return MatchDecision("rejected", "Объявление похоже на ноутбук, а не настольный ПК.")
        if re.search(r"\b(?:видеокарта|gpu)\b", normalized) and not re.search(
            r"\b(?:компьютер|системный блок|игровой пк|сборка|настольный)\b", normalized
        ):
            return MatchDecision("rejected", "Объявление похоже на отдельную видеокарту.")
        if card.condition is None and profile.condition:
            return MatchDecision("uncertain", "Состояние в карточке не удалось определить.")
        return MatchDecision("matched", "Цена, город и точная RTX 3080 соответствуют профилю.")
    included = [_normalize(term) for term in profile.include_terms if term.strip()]
    excluded = [_normalize(term) for term in profile.exclude_terms if term.strip()]
    if included and not any(term in normalized for term in included):
        return MatchDecision("uncertain", "Целевая модель не найдена с достаточной уверенностью.")
    matched_exclusions = [term for term in excluded if term and term in normalized]
    if matched_exclusions:
        return MatchDecision("rejected", f"Найдено исключающее условие: {matched_exclusions[0]}.")
    return MatchDecision("matched", "Цена и целевые признаки соответствуют профилю.")
