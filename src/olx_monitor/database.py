from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from olx_monitor.models import ListingCard, ListingDetails, SearchProfile


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def json_dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


class Database:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.initialize()

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path, timeout=20)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA journal_mode=WAL")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def initialize(self) -> None:
        with self.connection() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS profiles (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    category_path TEXT NOT NULL,
                    category_label TEXT NOT NULL,
                    target TEXT NOT NULL,
                    include_terms_json TEXT NOT NULL,
                    exclude_terms_json TEXT NOT NULL,
                    min_price INTEGER NOT NULL,
                    max_price INTEGER NOT NULL,
                    city TEXT,
                    condition TEXT,
                    mode TEXT NOT NULL DEFAULT 'classifier',
                    is_active INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS listings (
                    id TEXT PRIMARY KEY,
                    url TEXT NOT NULL,
                    title TEXT NOT NULL DEFAULT '',
                    price INTEGER,
                    description TEXT NOT NULL DEFAULT '',
                    location TEXT NOT NULL DEFAULT '',
                    published_text TEXT NOT NULL DEFAULT '',
                    condition TEXT,
                    seller_name TEXT,
                    seller_type TEXT,
                    seller_profile_url TEXT,
                    seller_since TEXT,
                    seller_rating TEXT,
                    attributes_json TEXT NOT NULL DEFAULT '{}',
                    analysis_json TEXT,
                    card_fingerprint TEXT NOT NULL DEFAULT '',
                    detail_fingerprint TEXT NOT NULL DEFAULT '',
                    first_seen_at TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL,
                    last_detail_at TEXT,
                    last_deep_checked_at TEXT,
                    ai_checked_fingerprint TEXT,
                    is_active INTEGER NOT NULL DEFAULT 1,
                    match_status TEXT NOT NULL DEFAULT 'uncertain',
                    match_reason TEXT NOT NULL DEFAULT '',
                    source_page_text TEXT NOT NULL DEFAULT ''
                );
                CREATE TABLE IF NOT EXISTS profile_matches (
                    profile_id TEXT NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
                    listing_id TEXT NOT NULL REFERENCES listings(id) ON DELETE CASCADE,
                    first_seen_at TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL,
                    PRIMARY KEY (profile_id, listing_id)
                );
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    listing_id TEXT NOT NULL REFERENCES listings(id) ON DELETE CASCADE,
                    profile_id TEXT REFERENCES profiles(id) ON DELETE SET NULL,
                    event_type TEXT NOT NULL,
                    changes_json TEXT NOT NULL DEFAULT '{}',
                    occurred_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS runs (
                    id TEXT PRIMARY KEY,
                    profile_id TEXT,
                    scope TEXT NOT NULL,
                    status TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    finished_at TEXT,
                    result_count INTEGER NOT NULL DEFAULT 0,
                    new_count INTEGER NOT NULL DEFAULT 0,
                    changed_count INTEGER NOT NULL DEFAULT 0,
                    error_text TEXT
                );
                CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS ai_usage (
                    month TEXT PRIMARY KEY,
                    spent_usd REAL NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS ai_reservations (
                    id TEXT PRIMARY KEY,
                    month TEXT NOT NULL,
                    reserved_usd REAL NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS profile_drafts (
                    id TEXT PRIMARY KEY,
                    chat_id INTEGER NOT NULL,
                    data_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_listings_last_seen ON listings(last_seen_at);
                CREATE INDEX IF NOT EXISTS idx_listings_match_status ON listings(match_status);
                CREATE INDEX IF NOT EXISTS idx_events_occurred ON events(occurred_at);
                CREATE INDEX IF NOT EXISTS idx_profile_matches_listing ON profile_matches(listing_id);
                """
            )
            columns = {row["name"] for row in db.execute("PRAGMA table_info(listings)")}
            if "ai_checked_fingerprint" not in columns:
                db.execute("ALTER TABLE listings ADD COLUMN ai_checked_fingerprint TEXT")

    def seed_initial_profile(self) -> None:
        with self.connection() as db:
            exists = db.execute("SELECT 1 FROM profiles LIMIT 1").fetchone()
            if exists:
                return
            now = utc_now()
            db.execute(
                """INSERT INTO profiles
                   (id,name,category_path,category_label,target,include_terms_json,exclude_terms_json,
                    min_price,max_price,city,condition,mode,is_active,created_at,updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    "rtx3080-desktop-almaty",
                    "RTX 3080 · настольные ПК · Алматы",
                    "/elektronika/kompyutery-i-komplektuyuschie/nastolnye-kompyutery",
                    "Компьютеры и комплектующие → Настольные компьютеры",
                    "NVIDIA GeForce RTX 3080",
                    json_dump(["RTX 3080", "RTX3080"]),
                    json_dump(["RTX 3080 Ti", "RTX 3080Ti", "ноутбук", "видеокарта отдельно"]),
                    200_000,
                    500_000,
                    "Алматы",
                    "Б/у",
                    "classifier",
                    1,
                    now,
                    now,
                ),
            )

    def _profile(self, row: sqlite3.Row, *, one_off: bool = False) -> SearchProfile:
        return SearchProfile(
            id=row["id"],
            name=row["name"],
            category_path=row["category_path"],
            category_label=row["category_label"],
            target=row["target"],
            include_terms=tuple(json.loads(row["include_terms_json"])),
            exclude_terms=tuple(json.loads(row["exclude_terms_json"])),
            min_price=row["min_price"],
            max_price=row["max_price"],
            city=row["city"],
            condition=row["condition"],
            mode=row["mode"],
            is_active=bool(row["is_active"]),
            one_off=one_off,
        )

    def profiles(self, active_only: bool = False) -> list[SearchProfile]:
        with self.connection() as db:
            query = "SELECT * FROM profiles"
            if active_only:
                query += " WHERE is_active=1"
            query += " ORDER BY created_at"
            return [self._profile(row) for row in db.execute(query).fetchall()]

    def get_profile(self, profile_id: str) -> SearchProfile | None:
        with self.connection() as db:
            row = db.execute("SELECT * FROM profiles WHERE id=?", (profile_id,)).fetchone()
            return self._profile(row) if row else None

    def create_profile(self, data: dict[str, Any], profile_id: str | None = None) -> str:
        profile_id = profile_id or f"profile-{uuid4().hex[:10]}"
        now = utc_now()
        with self.connection() as db:
            db.execute(
                """INSERT INTO profiles
                   (id,name,category_path,category_label,target,include_terms_json,exclude_terms_json,
                    min_price,max_price,city,condition,mode,is_active,created_at,updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    profile_id,
                    data["name"],
                    data["category_path"],
                    data["category_label"],
                    data["target"],
                    json_dump(data.get("include_terms", [])),
                    json_dump(data.get("exclude_terms", [])),
                    int(data["min_price"]),
                    int(data["max_price"]),
                    data.get("city"),
                    data.get("condition"),
                    data.get("mode", "classifier"),
                    1,
                    now,
                    now,
                ),
            )
        return profile_id

    def set_profile_active(self, profile_id: str, active: bool) -> bool:
        with self.connection() as db:
            cur = db.execute(
                "UPDATE profiles SET is_active=?, updated_at=? WHERE id=?",
                (int(active), utc_now(), profile_id),
            )
            return cur.rowcount > 0

    def set_profile_mode(self, profile_id: str, mode: str) -> bool:
        with self.connection() as db:
            cur = db.execute(
                "UPDATE profiles SET mode=?, updated_at=? WHERE id=?",
                (mode, utc_now(), profile_id),
            )
            return cur.rowcount > 0

    def reset_ai_checks(self, profile_id: str) -> None:
        with self.connection() as db:
            db.execute(
                """UPDATE listings SET ai_checked_fingerprint=NULL WHERE id IN
                   (SELECT listing_id FROM profile_matches WHERE profile_id=?)""",
                (profile_id,),
            )

    def list_drafts(self, chat_id: int) -> list[sqlite3.Row]:
        with self.connection() as db:
            return db.execute(
                "SELECT * FROM profile_drafts WHERE chat_id=? ORDER BY created_at DESC", (chat_id,)
            ).fetchall()

    def save_draft(self, chat_id: int, data: dict[str, Any]) -> str:
        draft_id = uuid4().hex[:10]
        with self.connection() as db:
            db.execute(
                "INSERT INTO profile_drafts(id,chat_id,data_json,created_at) VALUES(?,?,?,?)",
                (draft_id, chat_id, json_dump(data), utc_now()),
            )
        return draft_id

    def confirm_draft(self, draft_id: str, chat_id: int) -> str | None:
        with self.connection() as db:
            row = db.execute(
                "SELECT data_json FROM profile_drafts WHERE id=? AND chat_id=?",
                (draft_id, chat_id),
            ).fetchone()
            if not row:
                return None
            data = json.loads(row["data_json"])
            now = utc_now()
            profile_id = f"profile-{uuid4().hex[:10]}"
            db.execute(
                """INSERT INTO profiles
                   (id,name,category_path,category_label,target,include_terms_json,exclude_terms_json,
                    min_price,max_price,city,condition,mode,is_active,created_at,updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    profile_id,
                    data["name"],
                    data["category_path"],
                    data["category_label"],
                    data["target"],
                    json_dump(data.get("include_terms", [])),
                    json_dump(data.get("exclude_terms", [])),
                    int(data["min_price"]),
                    int(data["max_price"]),
                    data.get("city"),
                    data.get("condition"),
                    data.get("mode", "classifier"),
                    1,
                    now,
                    now,
                ),
            )
            db.execute("DELETE FROM profile_drafts WHERE id=?", (draft_id,))
            return profile_id

    def create_run(self, run_id: str, profile_id: str | None, scope: str) -> None:
        with self.connection() as db:
            db.execute(
                "INSERT INTO runs(id,profile_id,scope,status,started_at) VALUES(?,?,?,?,?)",
                (run_id, profile_id, scope, "running", utc_now()),
            )

    def finish_run(
        self,
        run_id: str,
        *,
        status: str,
        result_count: int = 0,
        new_count: int = 0,
        changed_count: int = 0,
        error: str | None = None,
    ) -> None:
        with self.connection() as db:
            db.execute(
                """UPDATE runs SET status=?,finished_at=?,result_count=?,new_count=?,changed_count=?,
                   error_text=? WHERE id=?""",
                (status, utc_now(), result_count, new_count, changed_count, error, run_id),
            )

    def latest_deep_check(self) -> str | None:
        with self.connection() as db:
            row = db.execute("SELECT MAX(last_deep_checked_at) AS checked FROM listings WHERE is_active=1").fetchone()
            return row["checked"] if row else None

    def get_listing_state(self, listing_id: str) -> sqlite3.Row | None:
        with self.connection() as db:
            return db.execute("SELECT * FROM listings WHERE id=?", (listing_id,)).fetchone()

    def upsert_card(
        self, profile: SearchProfile, card: ListingCard, fingerprint: str
    ) -> tuple[bool, bool, bool, dict[str, dict[str, Any]]]:
        """Return new/change flags plus field-level card differences."""
        now = utc_now()
        with self.connection() as db:
            row = db.execute("SELECT * FROM listings WHERE id=?", (card.id,)).fetchone()
            is_new = row is None
            changed = row is not None and row["card_fingerprint"] != fingerprint
            card_changes: dict[str, dict[str, Any]] = {}
            if changed:
                for field in ("title", "price", "location", "condition"):
                    before, after = row[field], getattr(card, field)
                    if before != after:
                        card_changes[field] = {"before": before, "after": after}
            if is_new:
                db.execute(
                    """INSERT INTO listings
                       (id,url,title,price,location,published_text,condition,card_fingerprint,
                        first_seen_at,last_seen_at,match_status)
                       VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        card.id,
                        card.url,
                        card.title,
                        card.price,
                        card.location,
                        card.published_text,
                        card.condition,
                        fingerprint,
                        now,
                        now,
                        "matched",
                    ),
                )
            else:
                db.execute(
                    """UPDATE listings SET url=?,title=?,price=?,location=?,published_text=?,condition=?,
                       card_fingerprint=?,last_seen_at=?,is_active=1 WHERE id=?""",
                    (
                        card.url,
                        card.title,
                        card.price,
                        card.location,
                        card.published_text,
                        card.condition,
                        fingerprint,
                        now,
                        card.id,
                    ),
                )
            new_profile_match = False
            if not profile.one_off:
                match = db.execute(
                    "SELECT 1 FROM profile_matches WHERE profile_id=? AND listing_id=?",
                    (profile.id, card.id),
                ).fetchone()
                if match:
                    db.execute(
                        "UPDATE profile_matches SET last_seen_at=? WHERE profile_id=? AND listing_id=?",
                        (now, profile.id, card.id),
                    )
                else:
                    db.execute(
                        "INSERT INTO profile_matches VALUES(?,?,?,?)",
                        (profile.id, card.id, now, now),
                    )
                    new_profile_match = True
                    if not is_new:
                        db.execute(
                            "INSERT INTO events(listing_id,profile_id,event_type,occurred_at) VALUES(?,?,?,?)",
                            (card.id, profile.id, "matched_profile", now),
                        )
            event_profile_id = None if profile.one_off else profile.id
            if changed:
                db.execute(
                    "INSERT INTO events(listing_id,profile_id,event_type,changes_json,occurred_at) VALUES(?,?,?,?,?)",
                    (
                        card.id,
                        event_profile_id,
                        "card_changed",
                        json_dump(card_changes),
                        now,
                    ),
                )
            elif is_new:
                db.execute(
                    "INSERT INTO events(listing_id,profile_id,event_type,occurred_at) VALUES(?,?,?,?)",
                    (card.id, event_profile_id, "new", now),
                )
            return is_new, changed, new_profile_match, card_changes

    def set_listing_match(self, listing_id: str, status: str, reason: str) -> None:
        with self.connection() as db:
            db.execute(
                "UPDATE listings SET match_status=?,match_reason=? WHERE id=?",
                (status, reason, listing_id),
            )

    def mark_ai_checked(self, listing_id: str, detail_fingerprint: str) -> None:
        with self.connection() as db:
            db.execute(
                "UPDATE listings SET ai_checked_fingerprint=? WHERE id=?",
                (detail_fingerprint, listing_id),
            )

    def upsert_details(
        self,
        profile: SearchProfile,
        details: ListingDetails,
        detail_fingerprint: str,
        match_status: str,
        match_reason: str,
        *,
        deep: bool,
        ai_analysis: dict[str, Any] | None = None,
    ) -> dict[str, dict[str, Any]]:
        now = utc_now()
        with self.connection() as db:
            row = db.execute("SELECT detail_fingerprint FROM listings WHERE id=?", (details.id,)).fetchone()
            changed = bool(row and row["detail_fingerprint"] and row["detail_fingerprint"] != detail_fingerprint)
            old = db.execute("SELECT * FROM listings WHERE id=?", (details.id,)).fetchone()
            if old and changed:
                changes: dict[str, dict[str, Any]] = {}
                for field in (
                    "title",
                    "price",
                    "description",
                    "location",
                    "condition",
                    "published_text",
                    "seller_name",
                    "seller_type",
                    "seller_since",
                    "seller_rating",
                    "seller_profile_url",
                ):
                    before, after = old[field], getattr(details, field)
                    if before != after:
                        changes[field] = {"before": before, "after": after}
                before_attributes = json.loads(old["attributes_json"] or "{}")
                if before_attributes != details.attributes:
                    changes["attributes"] = {
                        "before": before_attributes,
                        "after": details.attributes,
                    }
                if changes:
                    db.execute(
                        "INSERT INTO events(listing_id,profile_id,event_type,changes_json,occurred_at) "
                        "VALUES(?,?,?,?,?)",
                        (
                            details.id,
                            None if profile.one_off else profile.id,
                            "details_changed",
                            json_dump(changes),
                            now,
                        ),
                    )
            db.execute(
                """UPDATE listings SET url=?,title=?,price=?,description=?,location=?,published_text=?,
                   condition=?,seller_name=?,seller_type=?,seller_profile_url=?,seller_since=?,seller_rating=?,
                   attributes_json=?,detail_fingerprint=?,last_detail_at=?,
                   last_deep_checked_at=CASE WHEN ? THEN ? ELSE last_deep_checked_at END,
                   match_status=?,match_reason=?,source_page_text=?,
                   analysis_json=COALESCE(?,analysis_json),is_active=1 WHERE id=?""",
                (
                    details.url,
                    details.title,
                    details.price,
                    details.description,
                    details.location,
                    details.published_text,
                    details.condition,
                    details.seller_name,
                    details.seller_type,
                    details.seller_profile_url,
                    details.seller_since,
                    details.seller_rating,
                    json_dump(details.attributes),
                    detail_fingerprint,
                    now,
                    int(deep),
                    now,
                    match_status,
                    match_reason,
                    details.raw_page_text[:20_000],
                    json_dump(ai_analysis) if ai_analysis else None,
                    details.id,
                ),
            )
            return changes if old and changed else {}

    def event_rows(self, since: str | None = None) -> list[sqlite3.Row]:
        with self.connection() as db:
            if since:
                return db.execute(
                    """SELECT e.*,l.title,l.price,l.url,l.location,l.description,l.attributes_json,
                       l.seller_name,l.seller_type,l.seller_profile_url,l.seller_since,l.seller_rating,
                       l.match_status,l.match_reason,l.analysis_json,
                       GROUP_CONCAT(DISTINCT p.name) AS profile_names
                       FROM events e JOIN listings l ON l.id=e.listing_id
                       LEFT JOIN profile_matches pm ON pm.listing_id=l.id
                       LEFT JOIN profiles p ON p.id=pm.profile_id
                       WHERE e.occurred_at>=? AND l.match_status IN ('matched','uncertain')
                       GROUP BY e.id ORDER BY e.occurred_at DESC""",
                    (since,),
                ).fetchall()
            return db.execute(
                """SELECT e.*,l.title,l.price,l.url,l.location,l.description,l.attributes_json,
                   l.seller_name,l.seller_type,l.seller_profile_url,l.seller_since,l.seller_rating,
                   l.match_status,l.match_reason,l.analysis_json,
                   GROUP_CONCAT(DISTINCT p.name) AS profile_names
                   FROM events e JOIN listings l ON l.id=e.listing_id
                   LEFT JOIN profile_matches pm ON pm.listing_id=l.id
                   LEFT JOIN profiles p ON p.id=pm.profile_id
                   WHERE l.match_status IN ('matched','uncertain')
                   GROUP BY e.id ORDER BY e.occurred_at DESC LIMIT 100"""
            ).fetchall()

    def recent_stats(self) -> dict[str, Any]:
        with self.connection() as db:
            count = db.execute("SELECT COUNT(*) AS n FROM listings WHERE is_active=1").fetchone()["n"]
            uncertain = db.execute(
                "SELECT COUNT(*) AS n FROM listings WHERE is_active=1 AND match_status='uncertain'"
            ).fetchone()["n"]
            run = db.execute("SELECT * FROM runs ORDER BY started_at DESC LIMIT 1").fetchone()
            low, mid, high = [
                db.execute(
                    "SELECT COUNT(*) AS n FROM listings WHERE is_active=1 AND price>=? AND price<?",
                    bounds,
                ).fetchone()["n"]
                for bounds in ((200_000, 300_000), (300_000, 400_000), (400_000, 500_001))
            ]
            return {
                "active": count,
                "uncertain": uncertain,
                "last_run": run,
                "segments": (low, mid, high),
            }

    def uncertain_listings(self, limit: int = 30) -> list[sqlite3.Row]:
        with self.connection() as db:
            return db.execute(
                "SELECT id,title,price,location,url,match_reason FROM listings "
                "WHERE is_active=1 AND match_status='uncertain' ORDER BY last_seen_at DESC LIMIT ?",
                (limit,),
            ).fetchall()

    def active_listings_due_deep_check(self, before: str) -> list[sqlite3.Row]:
        with self.connection() as db:
            return db.execute(
                """SELECT l.* FROM listings l WHERE l.is_active=1 AND (
                       l.last_deep_checked_at IS NULL OR l.last_deep_checked_at<?)
                   ORDER BY l.last_deep_checked_at,l.last_seen_at""",
                (before,),
            ).fetchall()

    def profile_for_listing(self, listing_id: str) -> SearchProfile | None:
        with self.connection() as db:
            row = db.execute(
                """SELECT p.* FROM profiles p JOIN profile_matches pm ON pm.profile_id=p.id
                   WHERE pm.listing_id=? AND p.is_active=1 ORDER BY pm.first_seen_at LIMIT 1""",
                (listing_id,),
            ).fetchone()
            return self._profile(row) if row else None

    def profile_listing_rows(self, profile_id: str | None = None) -> list[sqlite3.Row]:
        with self.connection() as db:
            if profile_id:
                return db.execute(
                    """SELECT l.*,p.name AS profile_name FROM listings l
                       JOIN profile_matches pm ON pm.listing_id=l.id
                       JOIN profiles p ON p.id=pm.profile_id
                       WHERE p.id=? AND l.is_active=1 AND l.match_status IN ('matched','uncertain')
                       ORDER BY l.price,l.first_seen_at DESC""",
                    (profile_id,),
                ).fetchall()
            return db.execute(
                """SELECT l.*,GROUP_CONCAT(DISTINCT p.name) AS profile_name FROM listings l
                   LEFT JOIN profile_matches pm ON pm.listing_id=l.id
                   LEFT JOIN profiles p ON p.id=pm.profile_id
                   WHERE l.is_active=1 AND l.match_status IN ('matched','uncertain')
                   GROUP BY l.id ORDER BY l.price,l.first_seen_at DESC"""
            ).fetchall()

    def comparable_prices(self, profile_id: str, exclude_listing_id: str) -> list[int]:
        with self.connection() as db:
            rows = db.execute(
                """SELECT l.price FROM listings l JOIN profile_matches pm ON pm.listing_id=l.id
                   JOIN profiles p ON p.id=pm.profile_id
                   WHERE p.id=? AND l.id<>? AND l.is_active=1 AND l.match_status='matched'
                   AND l.price BETWEEN p.min_price AND p.max_price""",
                (profile_id, exclude_listing_id),
            ).fetchall()
            return [int(row["price"]) for row in rows if row["price"] is not None]

    def mark_listing_inactive(self, listing_id: str) -> None:
        with self.connection() as db:
            db.execute("UPDATE listings SET is_active=0 WHERE id=?", (listing_id,))

    def reject_profile_match(self, profile: SearchProfile, listing_id: str) -> None:
        if profile.one_off:
            with self.connection() as db:
                db.execute(
                    "UPDATE listings SET match_status='rejected',is_active=0 WHERE id=?",
                    (listing_id,),
                )
            return
        with self.connection() as db:
            db.execute(
                "DELETE FROM profile_matches WHERE profile_id=? AND listing_id=?",
                (profile.id, listing_id),
            )
            remaining = db.execute("SELECT 1 FROM profile_matches WHERE listing_id=? LIMIT 1", (listing_id,)).fetchone()
            if not remaining:
                db.execute(
                    "UPDATE listings SET match_status='rejected',is_active=0 WHERE id=?",
                    (listing_id,),
                )

    def ai_month_reserve_total(self) -> float:
        month = datetime.now(UTC).strftime("%Y-%m")
        with self.connection() as db:
            row = db.execute(
                "SELECT COALESCE(SUM(reserved_usd),0) AS amount FROM ai_reservations "
                "WHERE month=? AND status='reserved'",
                (month,),
            ).fetchone()
            return float(row["amount"])

    def save_setting(self, key: str, value: str) -> None:
        with self.connection() as db:
            db.execute(
                "INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, value),
            )

    def get_setting(self, key: str) -> str | None:
        with self.connection() as db:
            row = db.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
            return row["value"] if row else None

    def take_pairing_code(self, expected: str) -> bool:
        with self.connection() as db:
            row = db.execute("SELECT value FROM settings WHERE key='pairing_code'").fetchone()
            if not row or row["value"] != expected:
                return False
            db.execute("DELETE FROM settings WHERE key='pairing_code'")
            return True

    def ai_reserve(self, amount: float, monthly_cap: float) -> str | None:
        month = datetime.now(UTC).strftime("%Y-%m")
        reserve_id = uuid4().hex
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT spent_usd FROM ai_usage WHERE month=?", (month,)).fetchone()
            spent = float(row["spent_usd"]) if row else 0.0
            reserved = db.execute(
                "SELECT COALESCE(SUM(reserved_usd),0) AS amount FROM ai_reservations "
                "WHERE month=? AND status='reserved'",
                (month,),
            ).fetchone()["amount"]
            if spent + float(reserved) + amount > monthly_cap:
                return None
            db.execute(
                "INSERT INTO ai_reservations VALUES(?,?,?,?,?)",
                (reserve_id, month, amount, "reserved", utc_now()),
            )
            return reserve_id

    def ai_settle(self, reservation_id: str, actual_cost: float | None) -> None:
        with self.connection() as db:
            row = db.execute("SELECT month,status FROM ai_reservations WHERE id=?", (reservation_id,)).fetchone()
            if not row or row["status"] != "reserved":
                return
            status = "settled" if actual_cost is not None else "released"
            db.execute("UPDATE ai_reservations SET status=? WHERE id=?", (status, reservation_id))
            if actual_cost is not None:
                db.execute(
                    "INSERT INTO ai_usage(month,spent_usd) VALUES(?,?) "
                    "ON CONFLICT(month) DO UPDATE SET spent_usd=spent_usd+excluded.spent_usd",
                    (row["month"], max(0.0, actual_cost)),
                )

    def ai_month_spend(self) -> float:
        month = datetime.now(UTC).strftime("%Y-%m")
        with self.connection() as db:
            row = db.execute("SELECT spent_usd FROM ai_usage WHERE month=?", (month,)).fetchone()
            return float(row["spent_usd"]) if row else 0.0
