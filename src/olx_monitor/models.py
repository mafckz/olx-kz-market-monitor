from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class SearchProfile:
    id: str
    name: str
    category_path: str
    category_label: str
    target: str
    include_terms: tuple[str, ...]
    exclude_terms: tuple[str, ...]
    min_price: int
    max_price: int
    city: str | None = None
    condition: str | None = None
    mode: str = "classifier"
    is_active: bool = True
    one_off: bool = False


@dataclass(frozen=True, slots=True)
class ListingCard:
    id: str
    url: str
    title: str
    price: int | None
    location: str
    published_text: str
    condition: str | None
    source_card_id: str | None = None
    raw_text: str = ""


@dataclass(frozen=True, slots=True)
class ListingDetails:
    id: str
    url: str
    title: str
    price: int | None
    description: str
    location: str
    published_text: str
    condition: str | None
    seller_name: str | None
    seller_type: str | None
    seller_profile_url: str | None
    seller_since: str | None
    seller_rating: str | None
    attributes: dict[str, str] = field(default_factory=dict)
    raw_page_text: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "url": self.url,
            "title": self.title,
            "price": self.price,
            "description": self.description,
            "location": self.location,
            "published_text": self.published_text,
            "condition": self.condition,
            "seller_name": self.seller_name,
            "seller_type": self.seller_type,
            "seller_profile_url": self.seller_profile_url,
            "seller_since": self.seller_since,
            "seller_rating": self.seller_rating,
            "attributes": self.attributes,
        }


@dataclass(frozen=True, slots=True)
class MatchDecision:
    status: str
    reason: str


@dataclass(slots=True)
class RunStats:
    run_id: str
    new_count: int = 0
    changed_count: int = 0
    unchanged_count: int = 0
    uncertain_count: int = 0
    fetched_details: int = 0
    error: str | None = None
    notes: list[str] = field(default_factory=list)
    one_off_count: int = 0
    profile_match_count: int = 0
    notifications: list[dict[str, Any]] = field(default_factory=list)
