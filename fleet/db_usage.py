"""Model trust and speed stats — split from db.py (TD-04)."""
import logging
from collections import defaultdict

log = logging.getLogger(__name__)


def is_model_trusted(model: str) -> bool:
    """Check if a model is in the trusted_models table."""
    from db import get_conn

    with get_conn() as conn:
        row = conn.execute(
            "SELECT 1 FROM trusted_models WHERE model = ?", (model,)
        ).fetchone()
        return row is not None


def record_model_accept(model: str) -> int:
    """Increment accept count. Returns new count. Trusts at threshold."""
    from db import get_conn, _retry_write

    TRUST_THRESHOLD = 5
    def _do():
        with get_conn() as conn:
            row = conn.execute(
                "SELECT accept_count FROM trusted_models WHERE model = ?",
                (model,),
            ).fetchone()
            if row:
                new_count = row[0] + 1
                conn.execute(
                    "UPDATE trusted_models SET accept_count = ? WHERE model = ?",
                    (new_count, model),
                )
                return new_count
            else:
                conn.execute(
                    "INSERT INTO trusted_models (model, accept_count) VALUES (?, 1)",
                    (model,),
                )
                return 1
    return _retry_write(_do)


def get_registered_models() -> list:
    """Return list of all trusted model names."""
    from db import get_conn

    with get_conn() as conn:
        rows = conn.execute("SELECT model FROM trusted_models").fetchall()
        return [r[0] for r in rows]


def get_model_speed_stats(hours=24):
    """Return avg/p50/p95 tokens_per_sec per model over recent window."""
    from db import get_conn

    with get_conn() as conn:
        rows = conn.execute("""
            SELECT model, tokens_per_sec
            FROM usage
            WHERE tokens_per_sec IS NOT NULL
              AND created_at >= datetime('now', ?)
            ORDER BY model, tokens_per_sec
        """, (f"-{hours} hours",)).fetchall()

    if not rows:
        return []

    by_model = defaultdict(list)
    for r in rows:
        by_model[r["model"]].append(r["tokens_per_sec"])

    results = []
    for model, speeds in sorted(by_model.items()):
        n = len(speeds)
        avg = sum(speeds) / n
        p50 = speeds[n // 2]
        p95_idx = min(int(n * 0.95), n - 1)
        p95 = speeds[p95_idx]
        results.append({
            "model": model,
            "avg_tok_sec": round(avg, 1),
            "p50_tok_sec": round(p50, 1),
            "p95_tok_sec": round(p95, 1),
            "sample_count": n,
        })
    return results
