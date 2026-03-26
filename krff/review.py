"""src/review.py — Visibility queue for the two-tier content model.

SQLite store at 03_Analysis/review_queue.db (gitignored).

Schema (v2):
  corp_code       TEXT PRIMARY KEY  — zero-padded 8-digit DART code
  corp_name       TEXT              — display name (from corp_ticker_map at queue time)
  status          TEXT              — 'pending' | 'reviewed'
  visible         INTEGER           — 0=hidden (default), 1=visible (served to tier)
  visible_reason  TEXT              — 'system_signal' | 'human_review' | NULL
  tier            TEXT              — 'free' | 'paid' | NULL
  flag_assessment TEXT              — methodology feedback (see below)
  queued_at       TEXT              — ISO-8601 UTC datetime
  reviewed_at     TEXT              — ISO-8601 UTC datetime, NULL while pending
  notes           TEXT              — reviewer comments

flag_assessment values:
  true_positive   — system flagged; reviewer confirms anomaly is real
  false_positive  — system flagged; reviewer says company is clean
  false_negative  — system said clean; reviewer found a real anomaly
  clean_confirmed — system said clean; reviewer confirms

Visibility logic:
  visible=0              → 404 for all users (default for unreviewed companies)
  visible=1, tier=free   → free + paid users
  visible=1, tier=paid   → paid users only
  visible=1, tier=NULL   → in-review state (reviewer can see; not published)

Workflow:
  krff report <corp_code>              → generate report + auto-queue as pending
  krff seed-queue                      → bulk-insert all corps from corp_ticker_map
  krff queue [--status pending|reviewed]   → list queue
  krff surface <corp_code> --tier free|paid [--assessment ...] [--notes "..."]
  krff hide    <corp_code> [--assessment false_positive|...] [--notes "..."]
  krff assess  <corp_code> --assessment true_positive|... [--notes "..."]
  krff requeue <corp_code>             → reset back to pending for re-review
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path

QUEUE_DB = Path("03_Analysis/review_queue.db")

_VALID_ASSESSMENTS = frozenset(
    ("true_positive", "false_positive", "false_negative", "clean_confirmed")
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS review_queue (
    corp_code       TEXT PRIMARY KEY,
    corp_name       TEXT    NOT NULL DEFAULT '',
    status          TEXT    NOT NULL DEFAULT 'pending'
                            CHECK(status IN ('pending', 'reviewed')),
    visible         INTEGER NOT NULL DEFAULT 0,
    visible_reason  TEXT    CHECK(visible_reason IN ('system_signal', 'human_review')
                                  OR visible_reason IS NULL),
    tier            TEXT    CHECK(tier IN ('free', 'paid') OR tier IS NULL),
    flag_assessment TEXT    CHECK(flag_assessment IN
                                  ('true_positive', 'false_positive',
                                   'false_negative', 'clean_confirmed')
                                  OR flag_assessment IS NULL),
    queued_at       TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    reviewed_at     TEXT,
    notes           TEXT    NOT NULL DEFAULT ''
);
"""


@contextmanager
def _conn():
    con = sqlite3.connect(QUEUE_DB)
    con.row_factory = sqlite3.Row
    try:
        yield con
        con.commit()
    finally:
        con.close()


def _migrate_db() -> None:
    """Migrate v1 schema (pending/approved/rejected) to v2 (pending/reviewed + visible).

    Detects migration need by checking for absence of the 'visible' column.
    Rebuilds the table in-place: rename old → create new → copy → drop old.
    """
    if not QUEUE_DB.exists():
        return
    with _conn() as con:
        cols = {row[1] for row in con.execute("PRAGMA table_info(review_queue)").fetchall()}
        if not cols:
            return  # table doesn't exist yet
        if "visible" in cols:
            return  # already v2

        # v1 → v2 migration
        con.execute("ALTER TABLE review_queue RENAME TO _review_queue_v1")
        con.execute(_SCHEMA)
        con.execute(
            """
            INSERT INTO review_queue
                (corp_code, corp_name, status, visible, visible_reason, tier,
                 flag_assessment, queued_at, reviewed_at, notes)
            SELECT
                corp_code,
                corp_name,
                CASE WHEN status IN ('approved', 'rejected') THEN 'reviewed'
                     ELSE 'pending' END,
                CASE WHEN status = 'approved' THEN 1 ELSE 0 END,
                CASE WHEN status IN ('approved', 'rejected') THEN 'human_review'
                     ELSE NULL END,
                tier,
                NULL,
                queued_at,
                reviewed_at,
                notes
            FROM _review_queue_v1
            """
        )
        con.execute("DROP TABLE _review_queue_v1")


def _init_db() -> None:
    QUEUE_DB.parent.mkdir(parents=True, exist_ok=True)
    _migrate_db()
    with _conn() as con:
        con.execute(_SCHEMA)


def queue_add(corp_code: str, corp_name: str = "", force: bool = False) -> None:
    """Add corp to the pending queue.

    No-op if corp is already reviewed + visible (never downgrades a surfaced entry).
    If already pending, updates corp_name if a non-empty value is provided.
    If already reviewed + hidden and force=True, resets to pending for re-review.
    """
    _init_db()
    with _conn() as con:
        row = con.execute(
            "SELECT status, visible FROM review_queue WHERE corp_code=?",
            (corp_code.zfill(8),),
        ).fetchone()

        if row is None:
            con.execute(
                "INSERT INTO review_queue (corp_code, corp_name) VALUES (?, ?)",
                (corp_code.zfill(8), corp_name),
            )
        elif row["status"] == "pending":
            if corp_name:
                con.execute(
                    "UPDATE review_queue SET corp_name=? WHERE corp_code=?",
                    (corp_name, corp_code.zfill(8)),
                )
        elif row["status"] == "reviewed" and row["visible"] == 0 and force:
            con.execute(
                "UPDATE review_queue "
                "SET status='pending', visible=0, visible_reason=NULL, "
                "tier=NULL, flag_assessment=NULL, reviewed_at=NULL, "
                "corp_name=COALESCE(NULLIF(?, ''), "
                "    (SELECT corp_name FROM review_queue WHERE corp_code=?)) "
                "WHERE corp_code=?",
                (corp_name, corp_code.zfill(8), corp_code.zfill(8)),
            )
        # If reviewed + visible=1: no-op regardless of force — never un-surface


def surface(
    corp_code: str,
    tier: str,
    assessment: str | None = None,
    notes: str = "",
) -> bool:
    """Make corp visible at the given tier (marks as reviewed).

    Returns True if a row was updated, False if corp_code not found.
    Raises ValueError for invalid tier or assessment.
    """
    if tier not in ("free", "paid"):
        raise ValueError(f"tier must be 'free' or 'paid', got {tier!r}")
    if assessment is not None and assessment not in _VALID_ASSESSMENTS:
        raise ValueError(f"assessment must be one of {sorted(_VALID_ASSESSMENTS)}, got {assessment!r}")
    _init_db()
    with _conn() as con:
        cur = con.execute(
            """
            UPDATE review_queue
            SET status          = 'reviewed',
                visible         = 1,
                visible_reason  = 'human_review',
                tier            = ?,
                flag_assessment = COALESCE(?, flag_assessment),
                reviewed_at     = strftime('%Y-%m-%dT%H:%M:%SZ', 'now'),
                notes           = ?
            WHERE corp_code = ?
            """,
            (tier, assessment, notes, corp_code.zfill(8)),
        )
        return cur.rowcount > 0


def hide(
    corp_code: str,
    assessment: str | None = None,
    notes: str = "",
) -> bool:
    """Mark corp as reviewed but hidden (will not be served on any tier).

    Returns True if a row was updated, False if corp_code not found.
    """
    if assessment is not None and assessment not in _VALID_ASSESSMENTS:
        raise ValueError(f"assessment must be one of {sorted(_VALID_ASSESSMENTS)}, got {assessment!r}")
    _init_db()
    with _conn() as con:
        cur = con.execute(
            """
            UPDATE review_queue
            SET status          = 'reviewed',
                visible         = 0,
                visible_reason  = 'human_review',
                flag_assessment = COALESCE(?, flag_assessment),
                reviewed_at     = strftime('%Y-%m-%dT%H:%M:%SZ', 'now'),
                notes           = ?
            WHERE corp_code = ?
            """,
            (assessment, notes, corp_code.zfill(8)),
        )
        return cur.rowcount > 0


def assess(
    corp_code: str,
    assessment: str,
    notes: str = "",
) -> bool:
    """Record a flag_assessment without changing visibility.

    Use this to log a methodology verdict (true_positive, false_positive,
    false_negative, clean_confirmed) independently of the publication decision.
    Returns True if a row was updated, False if corp_code not found.
    """
    if assessment not in _VALID_ASSESSMENTS:
        raise ValueError(f"assessment must be one of {sorted(_VALID_ASSESSMENTS)}, got {assessment!r}")
    _init_db()
    with _conn() as con:
        cur = con.execute(
            """
            UPDATE review_queue
            SET flag_assessment = ?,
                notes           = CASE WHEN ? != '' THEN ? ELSE notes END
            WHERE corp_code = ?
            """,
            (assessment, notes, notes, corp_code.zfill(8)),
        )
        return cur.rowcount > 0


def get_visible(tier: str) -> frozenset[str]:
    """Return frozenset of corp_codes currently visible for the given tier.

    'free' returns corps visible to the free tier (tier='free').
    'paid' returns corps visible to any paid-access tier (tier IN ('free','paid')).
    """
    if not QUEUE_DB.exists():
        return frozenset()
    with _conn() as con:
        if tier == "free":
            rows = con.execute(
                "SELECT corp_code FROM review_queue WHERE visible=1 AND tier='free'",
            ).fetchall()
        elif tier == "paid":
            rows = con.execute(
                "SELECT corp_code FROM review_queue WHERE visible=1 AND tier IN ('free','paid')",
            ).fetchall()
        else:
            raise ValueError(f"tier must be 'free' or 'paid', got {tier!r}")
    return frozenset(r["corp_code"] for r in rows)


def list_queue(status: str | None = None) -> list[dict]:
    """Return all queue entries, optionally filtered by status.

    Results ordered by queued_at descending (newest first).
    """
    if not QUEUE_DB.exists():
        return []
    with _conn() as con:
        if status:
            rows = con.execute(
                "SELECT * FROM review_queue WHERE status=? ORDER BY queued_at DESC",
                (status,),
            ).fetchall()
        else:
            rows = con.execute(
                "SELECT * FROM review_queue ORDER BY queued_at DESC"
            ).fetchall()
    return [dict(r) for r in rows]


def get_counts() -> dict[str, int]:
    """Return status counts: pending, visible_free, visible_paid, hidden."""
    if not QUEUE_DB.exists():
        return {"pending": 0, "visible_free": 0, "visible_paid": 0, "hidden": 0}
    with _conn() as con:
        row = con.execute(
            """
            SELECT
                SUM(CASE WHEN status='pending'                          THEN 1 ELSE 0 END) AS pending,
                SUM(CASE WHEN visible=1 AND tier='free'                 THEN 1 ELSE 0 END) AS visible_free,
                SUM(CASE WHEN visible=1 AND tier='paid'                 THEN 1 ELSE 0 END) AS visible_paid,
                SUM(CASE WHEN status='reviewed' AND visible=0           THEN 1 ELSE 0 END) AS hidden
            FROM review_queue
            """
        ).fetchone()
    return {
        "pending":      row["pending"]      or 0,
        "visible_free": row["visible_free"] or 0,
        "visible_paid": row["visible_paid"] or 0,
        "hidden":       row["hidden"]       or 0,
    }


def seed_queue(corps: list[tuple[str, str]]) -> tuple[int, int]:
    """Bulk-insert (corp_code, corp_name) pairs; skip existing entries.

    Returns (inserted, skipped).
    Corps that cross the system signal threshold can be auto-surfaced by the
    caller before seeding by setting visible=1 externally, or by calling
    surface() after seed_queue().
    """
    _init_db()
    inserted = skipped = 0
    with _conn() as con:
        for corp_code, corp_name in corps:
            code = corp_code.zfill(8)
            exists = con.execute(
                "SELECT 1 FROM review_queue WHERE corp_code=?", (code,)
            ).fetchone()
            if exists:
                skipped += 1
            else:
                con.execute(
                    "INSERT INTO review_queue (corp_code, corp_name) VALUES (?, ?)",
                    (code, corp_name),
                )
                inserted += 1
    return inserted, skipped
