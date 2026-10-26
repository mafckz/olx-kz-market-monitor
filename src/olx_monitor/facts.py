from __future__ import annotations

import re
from statistics import median
from typing import Any

from olx_monitor.models import ListingDetails

_DEFECT_HINTS = (
    "не работает",
    "неисправ",
    "нужен ремонт",
    "под ремонт",
    "есть проблема",
    "не запускается",
    "шумит",
    "перегрев",
    "трещин",
    "скол",
    "поврежд",
)


def evidence_analysis(
    details: ListingDetails,
    *,
    comparable_prices: list[int] | None = None,
    comparable_minimum: int = 5,
) -> dict[str, Any]:
    description = details.description or ""
    folded = description.casefold()
    negotiation: list[str] = []
    seller_evidence: list[str] = []
    if re.search(r"\b(?:торг|договорная|договорная цена)\b", folded):
        negotiation.append("В описании указана возможность торга или договорная цена.")
    for hint in _DEFECT_HINTS:
        if hint in folded:
            match = re.search(re.escape(hint) + r"[^.!?\n]{0,100}", description, re.I)
            evidence = match.group(0).strip(" ,;:") if match else hint
            negotiation.append(f"В описании упомянут возможный недостаток: «{evidence}».")
            break
    if re.search(r"\b(?:kaspi|рассроч|кредит|оплата частями)\b", folded):
        seller_evidence.append("В карточке упоминается Kaspi, кредит или рассрочка.")
    if details.seller_type:
        seller_evidence.append(f"На странице объявления указан тип продавца: «{details.seller_type}».")
    if details.seller_since:
        seller_evidence.append(f"На странице объявления показана регистрация: {details.seller_since}.")
    if details.seller_rating:
        seller_evidence.append(f"На странице объявления показаны отзывы/оценка: {details.seller_rating}.")
    if any(word in (details.seller_type or "").casefold() for word in ("бизнес", "магазин")):
        seller_assessment = (
            "Карточка помечает продавца как бизнес/магазин; это признак со страницы, не независимая проверка статуса."
        )
    elif any(
        "Kaspi" in item or "кредит" in item.casefold() or "рассрочка" in item.casefold() for item in seller_evidence
    ):
        seller_assessment = (
            "Есть коммерческий признак в карточке (Kaspi/кредит/рассрочка); "
            "по одному объявлению статус продавца не подтверждён."
        )
    else:
        seller_assessment = (
            "Недостаточно наблюдаемых сведений одной карточки, чтобы оценить частную или коммерческую продажу."
        )
    if not seller_evidence:
        seller_evidence.append("На доступной странице дополнительных признаков продавца не найдено.")

    market: dict[str, Any] = {"comparable_count": 0, "median_price": None}
    if comparable_prices and len(comparable_prices) >= comparable_minimum:
        median_price = int(median(comparable_prices))
        market = {"comparable_count": len(comparable_prices), "median_price": median_price}
        if details.price is not None and details.price > median_price:
            negotiation.append(
                f"Цена выше медианы {_format_price(median_price)} по "
                f"{len(comparable_prices)} активным похожим объявлениям."
            )
    return {
        "source": "rules",
        "seller_assessment": seller_assessment,
        "seller_evidence": seller_evidence,
        "negotiation_points": negotiation,
        "market": market,
        "key_specs": [f"{key}: {value}" for key, value in details.attributes.items()],
    }


def _format_price(price: int) -> str:
    return f"{price:,} ₸".replace(",", " ")
