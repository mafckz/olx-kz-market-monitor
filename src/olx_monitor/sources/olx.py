from __future__ import annotations

import asyncio
import hashlib
import re
import time
import urllib.robotparser
from collections.abc import Iterable
from urllib.parse import quote, urlencode, urljoin, urlparse

import httpx
from bs4 import BeautifulSoup, Tag

from olx_monitor.models import ListingCard, ListingDetails, SearchProfile
from olx_monitor.settings import Settings

OLX_ORIGIN = "https://www.olx.kz"
ROBOTS_URL = f"{OLX_ORIGIN}/robots.txt"
USER_AGENT = "OLXMarketMonitor/0.1 (personal-use; respects robots.txt)"
_ID_RE = re.compile(r"-ID([A-Za-z0-9]+)\.html", re.IGNORECASE)
_PRICE_RE = re.compile(r"[0-9][0-9\s\u00a0\u202f.,]*")
_CARD_LINK_RE = re.compile(r"/d/obyavlenie/.+\.html(?:$|\?)")
CITY_SLUGS = {
    "алматы": "alma-ata",
    "астана": "astana",
    "шымкент": "shymkent",
    "караганда": "karaganda",
}


class OlxSourceError(RuntimeError):
    """Ошибка OLX, которую нужно показать владельцу, а не считать пустой выдачей."""


class RobotsDenied(OlxSourceError):
    pass


class RobotsUnavailable(OlxSourceError):
    """Правила OLX не удалось получить; ответ не следует запрашивать повторно автоматически."""


class AccessRestricted(OlxSourceError):
    pass


class MarkupChanged(OlxSourceError):
    pass


class ListingUnavailable(OlxSourceError):
    pass


def stable_fingerprint(*parts: object) -> str:
    raw = "\x1f".join("" if part is None else str(part).strip() for part in parts)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _clean(value: str | None) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def _price(value: str | None) -> int | None:
    if not value:
        return None
    match = _PRICE_RE.search(value)
    if not match:
        return None
    digits = re.sub(r"\D", "", match.group(0))
    try:
        return int(digits) if digits else None
    except ValueError:
        return None


def _condition(text: str) -> str | None:
    folded = text.casefold()
    if "б/у" in folded or "бывший" in folded or "б\u00a0у" in folded:
        return "Б/у"
    if re.search(r"\bновый\b", folded):
        return "Новый"
    return None


def _listing_id(url: str, card: Tag | None = None) -> str | None:
    match = _ID_RE.search(url)
    if match:
        return match.group(1)
    if card and card.get("id"):
        return str(card["id"])
    return None


def _location_and_date(text: str) -> tuple[str, str]:
    text = _clean(text)
    for separator in (" - ", " – ", " — "):
        if separator in text:
            left, right = text.rsplit(separator, 1)
            if any(char.isdigit() for char in right) or any(
                word in right.casefold() for word in ("сегодня", "вчера", "окт", "сен", "янв")
            ):
                return left.strip(), right.strip()
    return text, ""


def _text(tag: Tag | None) -> str:
    return _clean(tag.get_text(" ", strip=True)) if tag else ""


def _pagination_max(soup: BeautifulSoup) -> int:
    pages = [1]
    for anchor in soup.select("a[href*='page=']"):
        match = re.search(r"[?&]page=(\d+)", anchor.get("href", ""))
        if match:
            pages.append(int(match.group(1)))
    return max(pages)


def parse_cards(html: str) -> list[ListingCard]:
    soup = BeautifulSoup(html, "html.parser")
    cards: list[ListingCard] = []
    for node in soup.select("div[data-testid='l-card'], div[data-cy='l-card']"):
        anchor = node.select_one("a[href*='/d/obyavlenie/']")
        if not anchor:
            continue
        relative_url = anchor.get("href", "")
        if not _CARD_LINK_RE.search(relative_url):
            continue
        url = urljoin(OLX_ORIGIN, relative_url)
        listing_id = _listing_id(url, node)
        title = _text(node.select_one("[data-testid='ad-card-title'] h4"))
        if not title:
            title = _text(node.select_one("h4")) or _clean(anchor.get("title"))
        if not listing_id or not title:
            continue
        price = _price(_text(node.select_one("[data-testid='ad-price']")))
        location_date = _text(node.select_one("[data-testid='location-date']"))
        location, published = _location_and_date(location_date)
        card_text = _text(node)
        cards.append(
            ListingCard(
                id=listing_id,
                url=url,
                title=title,
                price=price,
                location=location,
                published_text=published,
                condition=_condition(card_text),
                source_card_id=str(node.get("id")) if node.get("id") else None,
                raw_text=card_text[:2_000],
            )
        )
    return cards


def _detail_attribute_pairs(soup: BeautifulSoup) -> dict[str, str]:
    attrs: dict[str, str] = {}
    for block in soup.select("[data-testid='ad-parameters-container'] li, [data-testid='ad-parameters'] li"):
        parts = [part.strip() for part in block.stripped_strings]
        if len(parts) >= 2:
            attrs[parts[0].rstrip(":")] = " ".join(parts[1:])
    for term in soup.select("[data-testid*='parameter'], [data-cy*='parameter']"):
        parts = [part.strip() for part in term.stripped_strings]
        if len(parts) == 2 and len(parts[0]) < 80:
            attrs.setdefault(parts[0].rstrip(":"), parts[1])
    return attrs


def parse_details(html: str, url: str) -> ListingDetails:
    soup = BeautifulSoup(html, "html.parser")
    title = _text(soup.select_one("h1[data-cy='ad_title'], h1[data-testid='ad-title'], h1"))
    if not title:
        meta = soup.select_one("meta[property='og:title']")
        title = _clean(meta.get("content")) if meta else ""
    if not title:
        raise MarkupChanged("На карточке не найден заголовок объявления.")
    listing_id = _listing_id(url)
    if not listing_id:
        raise MarkupChanged(f"Не удалось определить ID объявления из ссылки: {url}")
    description = _text(soup.select_one("[data-cy='ad_description'], [data-testid='ad-description'], #textContent"))
    price_text = _text(soup.select_one("[data-testid='ad-price'], [data-cy='ad-price']"))
    price = _price(price_text)
    location_text = _text(soup.select_one("[data-testid='location-date'], [data-cy='location-date']"))
    location, published = _location_and_date(location_text)
    page_text = _text(soup.body) if soup.body else _text(soup)
    profile_anchor = soup.select_one("a[href*='/list/user/']")
    profile_url = urljoin(OLX_ORIGIN, profile_anchor.get("href", "")) if profile_anchor else None
    seller_name = None
    for selector in (
        "[data-testid='seller-name']",
        "[data-testid='user-name']",
        "[data-cy='seller-name']",
        "[data-cy='user-name']",
    ):
        seller_name = _text(soup.select_one(selector)) or None
        if seller_name:
            break
    if not seller_name and profile_anchor:
        seller_name = _text(profile_anchor) or None
    seller_panel = soup.select_one("[data-testid='seller-card'], [data-testid='user-card'], [data-cy='seller-card']")
    seller_panel_text = _text(seller_panel)
    seller_since_match = re.search(
        r"(?:На OLX с|Зарегистрирован(?:а)? на OLX с|Пользователь с)\s*([^|\n]+)",
        seller_panel_text,
        flags=re.IGNORECASE,
    )
    rating_match = re.search(r"(?:\b\d[.,]\d\s*/\s*5\b|\b\d+\s+(?:отзыв|оцен))", seller_panel_text, re.I)
    seller_type = None
    for selector in (
        "[data-testid='seller-type']",
        "[data-testid='user-type']",
        "[data-cy='seller-type']",
    ):
        seller_type = _text(soup.select_one(selector)) or None
        if seller_type:
            break
    condition_text = _text(
        soup.select_one(
            "[data-testid='ad-parameters-container'], [data-testid='ad-parameters'], [data-testid='ad-condition']"
        )
    )
    return ListingDetails(
        id=listing_id,
        url=url,
        title=title,
        price=price,
        description=description,
        location=location,
        published_text=published,
        condition=_condition(condition_text),
        seller_name=seller_name,
        seller_type=seller_type,
        seller_profile_url=profile_url,
        seller_since=_clean(seller_since_match.group(1)) if seller_since_match else None,
        seller_rating=rating_match.group(0) if rating_match else None,
        attributes=_detail_attribute_pairs(soup),
        raw_page_text=page_text,
    )


def _slug(text: str) -> str:
    value = text.casefold().strip()
    value = re.sub(r"[^\w\s-]", " ", value, flags=re.UNICODE)
    value = re.sub(r"[\s_]+", "-", value)
    return value.strip("-")


class OlxClient:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.client = httpx.AsyncClient(
            timeout=httpx.Timeout(settings.request_timeout_seconds),
            follow_redirects=False,
            headers={
                "User-Agent": USER_AGENT,
                "Accept-Language": "ru-KZ,ru;q=0.9,en;q=0.7",
                "Accept": "text/html,application/xhtml+xml",
            },
        )
        self.robot_parser: urllib.robotparser.RobotFileParser | None = None
        self.last_request_at = 0.0

    async def __aenter__(self) -> OlxClient:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.client.aclose()

    async def _load_robots(self) -> None:
        if self.robot_parser is not None:
            return
        try:
            response = await self.client.get(ROBOTS_URL)
        except httpx.TimeoutException as exc:
            raise OlxSourceError("Таймаут при запросе robots.txt OLX") from exc
        except httpx.HTTPError as exc:
            raise OlxSourceError(f"Сетевая ошибка при запросе robots.txt OLX: {exc}") from exc
        if response.status_code >= 500:
            raise OlxSourceError(f"OLX временно недоступен при проверке robots.txt: HTTP {response.status_code}.")
        if response.status_code != 200:
            raise RobotsUnavailable(f"Не удалось проверить robots.txt OLX: HTTP {response.status_code}.")
        parser = urllib.robotparser.RobotFileParser()
        parser.set_url(ROBOTS_URL)
        parser.parse(response.text.splitlines())
        self.robot_parser = parser

    async def _polite_wait(self) -> None:
        delay = self.settings.request_delay_seconds - (time.monotonic() - self.last_request_at)
        if delay > 0:
            await asyncio.sleep(delay)

    async def get_html(self, url: str) -> str:
        await self._load_robots()
        current = url
        for redirect_count in range(5):
            parsed = urlparse(current)
            if parsed.hostname != "www.olx.kz":
                raise OlxSourceError(f"OLX перенаправил на неожиданный домен: {parsed.hostname}")
            assert self.robot_parser is not None
            if not self.robot_parser.can_fetch(USER_AGENT, current):
                raise RobotsDenied(f"OLX robots.txt запрещает запрашивать {parsed.path}")
            await self._polite_wait()
            try:
                response = await self.client.get(current)
            except httpx.TimeoutException as exc:
                raise OlxSourceError(f"Таймаут при запросе {parsed.path}") from exc
            except httpx.HTTPError as exc:
                raise OlxSourceError(f"Сетевая ошибка при запросе OLX: {exc}") from exc
            self.last_request_at = time.monotonic()
            if response.status_code in (301, 302, 303, 307, 308):
                location = response.headers.get("location")
                if not location or redirect_count == 4:
                    raise OlxSourceError("Слишком много или некорректное перенаправление OLX.")
                current = urljoin(current, location)
                continue
            if response.status_code in (401, 403, 429):
                raise AccessRestricted(f"OLX вернул HTTP {response.status_code}; автоматические запросы остановлены.")
            if response.status_code == 404:
                raise ListingUnavailable(f"Страница объявления недоступна: {parsed.path}")
            if response.status_code >= 500:
                raise OlxSourceError(f"OLX временно недоступен: HTTP {response.status_code}.")
            if response.status_code != 200:
                raise OlxSourceError(f"OLX вернул HTTP {response.status_code} для {parsed.path}.")
            return response.text
        raise OlxSourceError("Не удалось пройти перенаправление OLX.")

    def search_url(self, profile: SearchProfile, page: int) -> str:
        search_term = profile.include_terms[0] if profile.include_terms else profile.target
        target_slug = quote(_slug(search_term), safe="-")
        city_slug = CITY_SLUGS.get((profile.city or "").casefold())
        city_path = f"/{city_slug}" if city_slug else ""
        path = f"{OLX_ORIGIN}{profile.category_path.rstrip('/')}{city_path}/q-{target_slug}/"
        query = urlencode(
            [
                ("search[filter_float_price:from]", str(profile.min_price)),
                ("search[filter_float_price:to]", str(profile.max_price)),
                ("page", str(page)),
            ]
        )
        return f"{path}?{query}"

    async def search(self, profile: SearchProfile) -> tuple[list[ListingCard], int, bool]:
        first_html = await self.get_html(self.search_url(profile, 1))
        first_soup = BeautifulSoup(first_html, "html.parser")
        max_page = min(max(1, _pagination_max(first_soup)), self.settings.max_pages)
        all_cards = parse_cards(first_html)
        for page in range(2, max_page + 1):
            html = await self.get_html(self.search_url(profile, page))
            cards = parse_cards(html)
            if not cards:
                break
            all_cards.extend(cards)
        if not all_cards and first_soup.select_one("div[data-testid='l-card'], div[data-cy='l-card']"):
            raise MarkupChanged("Карточки OLX найдены, но их поля не удалось разобрать.")
        unique: dict[str, ListingCard] = {card.id: card for card in all_cards}
        truncated = _pagination_max(first_soup) >= self.settings.max_pages
        return list(unique.values()), max_page, truncated

    async def details(self, card: ListingCard) -> ListingDetails:
        html = await self.get_html(card.url)
        return parse_details(html, card.url)


def city_matches(location: str, city: str | None) -> bool:
    if not city:
        return True
    return _clean(location).casefold().startswith(city.casefold())


def within_price(price: int | None, profile: SearchProfile) -> bool:
    return price is not None and profile.min_price <= price <= profile.max_price


def summarize_profile_url(profile: SearchProfile) -> str:
    city_slug = CITY_SLUGS.get((profile.city or "").casefold())
    city_path = f"/{city_slug}" if city_slug else ""
    search_term = profile.include_terms[0] if profile.include_terms else profile.target
    return profile.category_path.rstrip("/") + city_path + f"/q-{_slug(search_term)}/"


def details_fingerprint(details: ListingDetails) -> str:
    return stable_fingerprint(
        details.title,
        details.price,
        details.description,
        details.location,
        details.condition,
        details.seller_name,
        details.seller_type,
        details.seller_since,
        details.seller_rating,
        sorted(details.attributes.items()),
    )


def card_fingerprint(card: ListingCard) -> str:
    return stable_fingerprint(card.title, card.price, card.location, card.condition)


def city_filter(cards: Iterable[ListingCard], city: str | None) -> list[ListingCard]:
    return [card for card in cards if city_matches(card.location, city)]
