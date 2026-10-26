from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from olx_monitor.database import Database
from olx_monitor.models import RunStats, SearchProfile
from olx_monitor.paths import ensure_directories


def price_segment(price: int | None, minimum: int, maximum: int) -> str:
    if price is None or maximum <= minimum:
        return "Неизвестно"
    width = maximum - minimum
    first_cut = minimum + width / 3
    second_cut = minimum + 2 * width / 3
    if price < minimum or price > maximum:
        return "Вне диапазона"
    if price < first_cut:
        return "Нижний"
    if price < second_cut:
        return "Средний"
    return "Верхний"


def _tenge(price: int | None) -> str:
    return f"{price:,} ₸".replace(",", " ") if price is not None else "цена не указана"


def _ai_dict(value: str | None) -> dict[str, Any] | None:
    if not value:
        return None
    try:
        data = json.loads(value)
        return data if isinstance(data, dict) else None
    except json.JSONDecodeError:
        return None


def write_markdown_report(database: Database, home: Path) -> Path:
    profiles = database.profiles()
    sections = [
        "# Сводка OLX Market Monitor",
        "",
        f"Обновлено: {datetime.now().astimezone().isoformat(timespec='minutes')}",
        "",
    ]
    if not profiles:
        sections.append("Пока нет сохранённых профилей поиска.")
    for profile in profiles:
        rows = database.profile_listing_rows(profile.id)
        active = [row for row in rows if row["match_status"] == "matched"]
        uncertain = [row for row in rows if row["match_status"] == "uncertain"]
        lower = profile.min_price + (profile.max_price - profile.min_price) / 3
        upper = profile.min_price + 2 * (profile.max_price - profile.min_price) / 3
        buckets = [
            sum(1 for row in active if row["price"] is not None and row["price"] < lower),
            sum(1 for row in active if row["price"] is not None and lower <= row["price"] < upper),
            sum(1 for row in active if row["price"] is not None and upper <= row["price"] <= profile.max_price),
        ]
        sections.extend(
            [
                f"## {profile.name} {'(пауза)' if not profile.is_active else ''}",
                "",
                f"- Категория и цель: {profile.category_label} · {profile.target}",
                f"- География: {profile.city or 'весь Казахстан'}; диапазон: "
                f"{_tenge(profile.min_price)}–{_tenge(profile.max_price)}",
                f"- Режим: {profile.mode}; найдено активных: {len(active)}, требуют проверки: {len(uncertain)}",
                f"- Сегменты (нижний / средний / верхний): {buckets[0]} / {buckets[1]} / {buckets[2]}",
                "",
            ]
        )
        if not rows:
            sections.extend(["Пока нет объявлений.", ""])
            continue
        for row in rows:
            segment = price_segment(row["price"], profile.min_price, profile.max_price)
            sections.extend(
                [
                    f"### [{row['title'] or row['id']}]({row['url']}) — {_tenge(row['price'])}",
                    "",
                    f"- Сегмент: {segment}; город/район: {row['location'] or 'не указан'}; "
                    f"статус совпадения: {row['match_status']}.",
                    f"- Обоснование совпадения: {row['match_reason'] or 'не указано'}",
                    f"- Обнаружено: {row['first_seen_at']}; опубликовано: {row['published_text'] or 'не указано'}",
                ]
            )
            if row["seller_name"]:
                sections.append(
                    f"- Продавец в карточке: {row['seller_name']} ({row['seller_type'] or 'тип не указан'})."
                )
            if row["seller_since"]:
                sections.append(f"- Регистрация OLX (если показана в объявлении): {row['seller_since']}.")
            if row["seller_rating"]:
                sections.append(f"- Оценка/отзывы (если показаны в объявлении): {row['seller_rating']}.")
            if row["seller_profile_url"]:
                sections.append(f"- Профиль продавца для ручной проверки: {row['seller_profile_url']}")
            if row["description"]:
                desc = row["description"].replace("\n", " ")
                sections.append(f"- Описание: {desc[:700]}{'…' if len(desc) > 700 else ''}")
            attributes = json.loads(row["attributes_json"] or "{}")
            if attributes:
                sections.append("- Характеристики: " + "; ".join(f"{k}: {v}" for k, v in attributes.items()))
            ai = _ai_dict(row["analysis_json"])
            if ai:
                if ai.get("summary"):
                    sections.append(f"- AI-анализ: {ai['summary']}")
                seller_eval = ai.get("seller_assessment")
                evidence = ai.get("seller_evidence") or []
                if seller_eval:
                    sections.append(
                        f"- Предварительная оценка продавца: {seller_eval}. Основания: {'; '.join(evidence)}"
                    )
                negotiation = ai.get("negotiation_points") or []
                if negotiation:
                    sections.append(f"- Возможные основания для торга: {'; '.join(negotiation)}")
                market = ai.get("market") or {}
                if int(market.get("comparable_count", 0)) >= 5 and market.get("median_price"):
                    sections.append(
                        f"- Сравнение: медиана {_tenge(int(market['median_price']))} по "
                        f"{int(market['comparable_count'])} похожим активным объявлениям."
                    )
            sections.append("")
    recent_events = database.event_rows()
    if recent_events:
        sections.extend(["## Последние события", ""])
        for event in recent_events[:50]:
            title = event["title"] or event["listing_id"]
            changes = json.loads(event["changes_json"] or "{}")
            change_text = "; ".join(
                f"{field}: {values.get('before')} → {values.get('after')}" for field, values in changes.items()
            )
            sections.append(
                f"- {event['occurred_at']} · {event['event_type']} · [{title}]({event['url']}) · "
                f"{_tenge(event['price'])}" + (f" · {change_text}" if change_text else "")
            )
        sections.append("")
    root = ensure_directories(home)
    path = root / "reports" / "latest.md"
    path.write_text("\n".join(sections).rstrip() + "\n", encoding="utf-8")
    return path


def build_digest(stats: RunStats, profiles: dict[str, SearchProfile]) -> str | None:
    if not stats.notifications and not stats.error and not stats.notes:
        return None
    lines = ["OLX: итоги проверки"]
    if stats.new_count or stats.changed_count:
        lines.append(f"Новых: {stats.new_count}; изменилось: {stats.changed_count}.")
    if stats.profile_match_count:
        lines.append(f"Новых совпадений в профилях: {stats.profile_match_count}.")
    if stats.one_off_count:
        lines.append(f"Разовый поиск по Казахстану: результатов {stats.one_off_count}.")
    if stats.uncertain_count:
        lines.append(f"Требуют ручной проверки: {stats.uncertain_count}.")
    if stats.error:
        lines.append(f"Ошибка запуска: {stats.error}")
    for note in stats.notes:
        lines.append(f"Примечание: {note}")
    for item in stats.notifications[:25]:
        price = _tenge(item.get("price"))
        kind = {"new": "Новое", "changed": "Изменение"}.get(item.get("kind"), "Найдено")
        lines.extend(
            [
                "",
                f"{kind} · {item.get('title') or item.get('id')}",
                f"Цена: {price}; {item.get('location') or 'город не указан'}.",
            ]
        )
        profile_names = item.get("profiles") or []
        if profile_names:
            lines.append("Профили: " + ", ".join(profile_names))
        if item.get("match_reason"):
            lines.append(f"Совпадение: {item['match_reason']}")
        changes = item.get("changes") or {}
        if changes:
            lines.append(
                "Изменилось: "
                + "; ".join(
                    f"{field}: {value.get('before')} → {value.get('after')}" for field, value in changes.items()
                )
            )
        if item.get("ai_summary"):
            lines.append(f"AI-анализ: {item['ai_summary']}")
        if item.get("key_specs"):
            lines.append("AI выделил характеристики: " + "; ".join(item["key_specs"][:8]))
        specs = item.get("specs") or {}
        if specs:
            lines.append("Характеристики: " + "; ".join(f"{key}: {value}" for key, value in list(specs.items())[:8]))
        if item.get("seller_name"):
            lines.append(f"Продавец в карточке: {item['seller_name']} ({item.get('seller_type') or 'тип не указан'}).")
        if item.get("seller_profile_url"):
            lines.append(f"Профиль для ручной проверки: {item['seller_profile_url']}")
        if item.get("seller_assessment"):
            lines.append(f"Предварительная оценка: {item['seller_assessment']}")
        if item.get("negotiation_points"):
            lines.append("Торг: " + "; ".join(item["negotiation_points"][:3]))
        lines.append(item.get("url", ""))
    return "\n".join(lines)


def split_telegram(text: str, limit: int = 3900) -> list[str]:
    if len(text) <= limit:
        return [text]
    result: list[str] = []
    current = ""
    for line in text.splitlines(keepends=True):
        if len(current) + len(line) > limit and current:
            result.append(current)
            current = ""
        if len(line) > limit:
            while line:
                result.append(line[:limit])
                line = line[limit:]
        else:
            current += line
    if current:
        result.append(current)
    return result
