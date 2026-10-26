from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from olx_monitor.ai import AIService
from olx_monitor.database import Database
from olx_monitor.facts import evidence_analysis
from olx_monitor.models import ListingCard, ListingDetails, RunStats, SearchProfile
from olx_monitor.profiles import classify_listing
from olx_monitor.reporting import write_markdown_report
from olx_monitor.settings import Settings
from olx_monitor.sources.olx import (
    AccessRestricted,
    ListingUnavailable,
    MarkupChanged,
    OlxClient,
    OlxSourceError,
    RobotsDenied,
    RobotsUnavailable,
    card_fingerprint,
    city_matches,
    details_fingerprint,
    within_price,
)

_NON_RETRYABLE = (RobotsDenied, RobotsUnavailable, AccessRestricted, ListingUnavailable, MarkupChanged)


async def _retry[T](action: Callable[[], Awaitable[T]], attempts: int) -> T:
    last_error: Exception | None = None
    for attempt in range(max(1, attempts)):
        try:
            return await action()
        except _NON_RETRYABLE:
            raise
        except OlxSourceError as exc:
            last_error = exc
            if attempt + 1 < attempts:
                await asyncio.sleep(min(30, 2**attempt * 2))
    assert last_error is not None
    raise last_error


def _deep_check_due(database: Database, hours: int) -> bool:
    last = database.latest_deep_check()
    if not last:
        return True
    try:
        value = datetime.fromisoformat(last)
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        return datetime.now(UTC) - value >= timedelta(hours=hours)
    except ValueError:
        return True


def _card_from_row(row: object) -> ListingCard:
    return ListingCard(
        id=row["id"],
        url=row["url"],
        title=row["title"],
        price=row["price"],
        location=row["location"],
        published_text=row["published_text"],
        condition=row["condition"],
        source_card_id=None,
        raw_text=row["title"],
    )


def _details_with_card_fallback(details: ListingDetails, card: ListingCard) -> ListingDetails:
    return replace(
        details,
        title=details.title or card.title,
        price=details.price if details.price is not None else card.price,
        location=details.location or card.location,
        published_text=details.published_text or card.published_text,
        condition=details.condition or card.condition,
    )


class Monitor:
    def __init__(self, settings: Settings, database: Database):
        self.settings = settings
        self.database = database
        self.ai = AIService(settings, database)
        self._run_lock = asyncio.Lock()

    async def run(self, profiles: list[SearchProfile] | None = None) -> RunStats:
        async with self._run_lock:
            return await self._run_locked(profiles)

    async def _run_locked(self, profiles: list[SearchProfile] | None) -> RunStats:
        stats = RunStats(run_id=uuid4().hex[:12])
        selected = profiles if profiles is not None else self.database.profiles(active_only=True)
        selected = [profile for profile in selected if profile.is_active or profile.one_off]
        if not selected:
            stats.error = "Нет активных профилей поиска. Добавьте профиль через /add."
            return stats
        deep_due = _deep_check_due(self.database, self.settings.deep_check_hours)
        notification_index: dict[str, dict[str, object]] = {}
        errors: list[str] = []
        async with OlxClient(self.settings) as source:
            for profile in selected:
                run_id = uuid4().hex[:12]
                self.database.create_run(
                    run_id,
                    None if profile.one_off else profile.id,
                    "country" if not profile.city else profile.city,
                )
                result_count = 0
                profile_new = 0
                profile_changed = 0
                profile_error: str | None = None
                error_count_before = len(errors)
                try:
                    cards, _pages, truncated = await _retry(
                        lambda profile=profile: source.search(profile), self.settings.max_retries
                    )
                    filtered = [
                        card
                        for card in cards
                        if within_price(card.price, profile) and city_matches(card.location, profile.city)
                    ]
                    result_count = len(filtered)
                    for card in filtered:
                        decision = classify_listing(profile, card)
                        if decision.status == "rejected":
                            continue
                        is_new, card_changed, new_profile_match, card_changes = self.database.upsert_card(
                            profile, card, card_fingerprint(card)
                        )
                        self.database.set_listing_match(card.id, decision.status, decision.reason)
                        state = self.database.get_listing_state(card.id)
                        if not state:
                            continue
                        old_deep = state["last_deep_checked_at"]
                        deep_stale = deep_due and (
                            not old_deep
                            or datetime.fromisoformat(old_deep)
                            < datetime.now(UTC) - timedelta(hours=self.settings.deep_check_hours)
                        )
                        ai_analysis_stale = (
                            profile.mode == "ai" and state["ai_checked_fingerprint"] != state["detail_fingerprint"]
                        )
                        needs_details = (
                            is_new or card_changed or not state["last_detail_at"] or deep_stale or ai_analysis_stale
                        )
                        details_changed = False
                        final_status = decision.status
                        final_reason = decision.reason
                        detail: ListingDetails | None = None
                        if needs_details:
                            try:
                                detail = await _retry(lambda card=card: source.details(card), self.settings.max_retries)
                                detail = _details_with_card_fallback(detail, card)
                                final_decision = classify_listing(profile, card, detail)
                                final_status, final_reason = (
                                    final_decision.status,
                                    final_decision.reason,
                                )
                                if final_status == "rejected":
                                    self.database.upsert_details(
                                        profile,
                                        detail,
                                        details_fingerprint(detail),
                                        final_status,
                                        final_reason,
                                        deep=deep_due,
                                    )
                                    self.database.reject_profile_match(profile, card.id)
                                    continue
                                comparable_prices = (
                                    self.database.comparable_prices(profile.id, card.id) if not profile.one_off else []
                                )
                                ai_analysis: dict[str, object] | None = evidence_analysis(
                                    detail, comparable_prices=comparable_prices
                                )
                                if profile.mode == "ai":
                                    detail_fp = details_fingerprint(detail)
                                    analysis_needed = state["ai_checked_fingerprint"] != detail_fp
                                    if analysis_needed:
                                        ai_result = await self.ai.analyze_listing(
                                            profile, detail, comparable_prices=comparable_prices
                                        )
                                        if ai_result.data:
                                            ai_analysis.update(ai_result.data)
                                            ai_analysis["source"] = "openai"
                                        if ai_result.note:
                                            stats.notes.append(f"{profile.name}: {ai_result.note}")
                                        self.database.mark_ai_checked(card.id, detail_fp)
                                    else:
                                        ai_analysis = None
                                details_changed = self.database.upsert_details(
                                    profile,
                                    detail,
                                    details_fingerprint(detail),
                                    final_status,
                                    final_reason,
                                    deep=deep_due,
                                    ai_analysis=ai_analysis,
                                )
                            except ListingUnavailable:
                                self.database.mark_listing_inactive(card.id)
                                continue
                            except (OlxSourceError, ValueError) as exc:
                                final_status = "uncertain"
                                final_reason = f"Не удалось прочитать карточку: {exc}"
                                errors.append(f"{card.id}: {exc}")
                                self.database.set_listing_match(card.id, final_status, final_reason)
                        self.database.set_listing_match(card.id, final_status, final_reason)
                        if is_new:
                            stats.new_count += 1
                            profile_new += 1
                        elif card_changed or details_changed:
                            stats.changed_count += 1
                            profile_changed += 1
                        elif new_profile_match:
                            stats.profile_match_count += 1
                            profile_new += 1
                        else:
                            stats.unchanged_count += 1
                        if final_status == "uncertain":
                            stats.uncertain_count += 1
                        if profile.one_off:
                            stats.one_off_count += 1
                        if (
                            card.id in notification_index
                            or is_new
                            or new_profile_match
                            or card_changed
                            or details_changed
                            or profile.one_off
                        ):
                            if final_status in {"matched", "uncertain"}:
                                current_state = self.database.get_listing_state(card.id)
                                stored_specs = {}
                                stored_ai = None
                                if current_state:
                                    try:
                                        stored_specs = json.loads(current_state["attributes_json"] or "{}")
                                        stored_ai = json.loads(current_state["analysis_json"] or "null")
                                    except (ValueError, TypeError):
                                        stored_specs, stored_ai = {}, None
                                chosen_ai = ai_analysis or stored_ai
                                self._add_notification(
                                    stats,
                                    notification_index,
                                    card.id,
                                    {
                                        "kind": "country"
                                        if profile.one_off
                                        else "new"
                                        if is_new or new_profile_match
                                        else "changed",
                                        "id": card.id,
                                        "title": detail.title
                                        if detail
                                        else current_state["title"]
                                        if current_state
                                        else card.title,
                                        "price": detail.price
                                        if detail and detail.price is not None
                                        else current_state["price"]
                                        if current_state
                                        else card.price,
                                        "location": detail.location
                                        if detail and detail.location
                                        else current_state["location"]
                                        if current_state
                                        else card.location,
                                        "url": card.url,
                                        "match_reason": final_reason,
                                        "status": final_status,
                                        "profiles": [profile.name],
                                        "specs": detail.attributes if detail else stored_specs,
                                        "seller_name": detail.seller_name
                                        if detail
                                        else current_state["seller_name"]
                                        if current_state
                                        else None,
                                        "seller_type": detail.seller_type
                                        if detail
                                        else current_state["seller_type"]
                                        if current_state
                                        else None,
                                        "seller_profile_url": detail.seller_profile_url
                                        if detail
                                        else current_state["seller_profile_url"]
                                        if current_state
                                        else None,
                                        "changes": {**card_changes, **details_changed},
                                        "seller_assessment": chosen_ai.get("seller_assessment") if chosen_ai else None,
                                        "negotiation_points": chosen_ai.get("negotiation_points") if chosen_ai else [],
                                        "ai_summary": chosen_ai.get("summary") if chosen_ai else None,
                                        "key_specs": chosen_ai.get("key_specs", []) if chosen_ai else [],
                                    },
                                )
                    if truncated:
                        errors.append(
                            f"{profile.name}: достигнут лимит страниц ({self.settings.max_pages}); "
                            "выдача может быть неполной."
                        )
                    self.database.finish_run(
                        run_id,
                        status="partial" if len(errors) > error_count_before else "completed",
                        result_count=result_count,
                        new_count=profile_new,
                        changed_count=profile_changed,
                    )
                except (OlxSourceError, ValueError) as exc:
                    profile_error = str(exc)
                    errors.append(f"{profile.name}: {exc}")
                    self.database.finish_run(run_id, status="failed", result_count=result_count, error=profile_error)
                except Exception as exc:
                    profile_error = f"Внутренняя ошибка: {exc}"
                    errors.append(f"{profile.name}: {profile_error}")
                    self.database.finish_run(run_id, status="failed", result_count=result_count, error=profile_error)

            if deep_due:
                cutoff = (datetime.now(UTC) - timedelta(hours=self.settings.deep_check_hours)).isoformat(
                    timespec="seconds"
                )
                stale_rows = self.database.active_listings_due_deep_check(cutoff)
                for row in stale_rows:
                    profile = self.database.profile_for_listing(row["id"])
                    if not profile:
                        continue
                    card = _card_from_row(row)
                    try:
                        detail = await _retry(lambda card=card: source.details(card), self.settings.max_retries)
                        detail = _details_with_card_fallback(detail, card)
                        decision = classify_listing(profile, card, detail)
                        if decision.status == "rejected":
                            self.database.reject_profile_match(profile, card.id)
                            continue
                        detail_fp = details_fingerprint(detail)
                        comparable_prices = self.database.comparable_prices(profile.id, card.id)
                        ai_analysis: dict[str, object] | None = evidence_analysis(
                            detail, comparable_prices=comparable_prices
                        )
                        analysis_needed = profile.mode == "ai" and row["ai_checked_fingerprint"] != detail_fp
                        if analysis_needed:
                            ai_result = await self.ai.analyze_listing(
                                profile, detail, comparable_prices=comparable_prices
                            )
                            if ai_result.data:
                                ai_analysis.update(ai_result.data)
                                ai_analysis["source"] = "openai"
                            if ai_result.note:
                                stats.notes.append(f"{profile.name}: {ai_result.note}")
                            self.database.mark_ai_checked(card.id, detail_fp)
                        elif profile.mode == "ai":
                            ai_analysis = None
                        changed = self.database.upsert_details(
                            profile,
                            detail,
                            detail_fp,
                            decision.status,
                            decision.reason,
                            deep=True,
                            ai_analysis=ai_analysis,
                        )
                        if changed:
                            stats.changed_count += 1
                            self._add_notification(
                                stats,
                                notification_index,
                                card.id,
                                {
                                    "kind": "changed",
                                    "id": card.id,
                                    "title": detail.title,
                                    "price": detail.price,
                                    "location": detail.location,
                                    "url": detail.url,
                                    "match_reason": decision.reason,
                                    "status": decision.status,
                                    "profiles": [profile.name],
                                    "specs": detail.attributes,
                                    "seller_name": detail.seller_name,
                                    "seller_type": detail.seller_type,
                                    "seller_profile_url": detail.seller_profile_url,
                                    "changes": changed,
                                    "seller_assessment": ai_analysis.get("seller_assessment") if ai_analysis else None,
                                    "negotiation_points": ai_analysis.get("negotiation_points", [])
                                    if ai_analysis
                                    else [],
                                    "ai_summary": ai_analysis.get("summary") if ai_analysis else None,
                                    "key_specs": ai_analysis.get("key_specs", []) if ai_analysis else [],
                                },
                            )
                    except ListingUnavailable:
                        self.database.mark_listing_inactive(row["id"])
                    except OlxSourceError as exc:
                        errors.append(f"Глубокая проверка {row['id']}: {exc}")

        if errors:
            stats.error = "\n".join(dict.fromkeys(errors))[:3_000]
        write_markdown_report(self.database, self.settings.home)
        return stats

    @staticmethod
    def _add_notification(
        stats: RunStats,
        index: dict[str, dict[str, object]],
        listing_id: str,
        notification: dict[str, object],
    ) -> None:
        existing = index.get(listing_id)
        if existing:
            profiles = existing.setdefault("profiles", [])
            if notification["profiles"][0] not in profiles:
                profiles.append(notification["profiles"][0])
            if notification.get("kind") == "changed":
                existing["kind"] = "changed"
            return
        index[listing_id] = notification
        stats.notifications.append(notification)


def one_off_country_profile(profile: SearchProfile, *, name: str | None = None) -> SearchProfile:
    return replace(
        profile,
        id=f"oneoff-{uuid4().hex[:10]}",
        name=name or f"Разовый поиск по Казахстану · {profile.target}",
        city=None,
        one_off=True,
        is_active=True,
    )


def make_one_off_from_text(data: dict[str, object]) -> SearchProfile:
    return SearchProfile(
        id=f"oneoff-{uuid4().hex[:10]}",
        name=f"Разовый поиск по Казахстану · {data['target']}",
        category_path=str(data["category_path"]),
        category_label=str(data["category_label"]),
        target=str(data["target"]),
        include_terms=tuple(data.get("include_terms", [])),
        exclude_terms=tuple(data.get("exclude_terms", [])),
        min_price=int(data["min_price"]),
        max_price=int(data["max_price"]),
        city=None,
        condition=data.get("condition"),
        mode="classifier",
        is_active=True,
        one_off=True,
    )
