"""Layered Inter-Agent Communication (CM-1 through CM-4)."""


def _get_conn():
    """Lazy import to avoid circular dependency with db.py."""
    import db
    return db.get_conn()


def _retry_write(fn, retries=8):
    """Lazy import to avoid circular dependency with db.py."""
    import db
    return db._retry_write(fn, retries)


# ── Channel Constants ─────────────────────────────────────────────────────────
CH_SUP   = "sup"    # Layer 1: supervisor-to-supervisor
CH_AGENT = "agent"  # Layer 2: agent-to-agent
CH_FLEET = "fleet"  # Layer 3: cross-layer (default)
CH_POOL  = "pool"   # Layer 4: supervisor → agent pool


def post_message(from_agent, to_agent, body_json, channel="fleet"):
    def _do():
        with _get_conn() as conn:
            conn.execute("""
                INSERT INTO messages (from_agent, to_agent, body_json, channel)
                VALUES (?, ?, ?, ?)
            """, (from_agent, to_agent, body_json, channel))
    _retry_write(_do)


def get_messages(agent_name, unread_only=True, limit=20, channels=None):
    """Retrieve messages for an agent. Marks them read on fetch.

    Args:
        channels: optional list of channel strings to filter on.
                  None = no filter (backward compat).
    """
    with _get_conn() as conn:
        where = "WHERE to_agent=?"
        params = [agent_name]
        if unread_only:
            where += " AND read_at IS NULL"
        if channels:
            placeholders = ','.join('?' * len(channels))
            where += f" AND channel IN ({placeholders})"
            params.extend(channels)
        rows = conn.execute(f"""
            SELECT id, from_agent, to_agent, created_at, body_json, channel
            FROM messages {where}
            ORDER BY created_at DESC LIMIT ?
        """, (*params, limit)).fetchall()
        if rows:
            ids = [r['id'] for r in rows]
            conn.execute(
                f"UPDATE messages SET read_at=datetime('now') WHERE id IN ({','.join('?' * len(ids))})",
                ids
            )
        return [dict(r) for r in rows]


def broadcast_message(from_agent, body_json, channel="fleet"):
    """Send a message to agents appropriate for the channel.

    channel="fleet": all agents (existing behavior)
    channel="sup":   only supervisors (role='supervisor')
    channel="agent" or "pool": only non-supervisors (role != 'supervisor')
    """
    def _do():
        with _get_conn() as conn:
            if channel == CH_SUP:
                agents = conn.execute(
                    "SELECT name FROM agents WHERE role='supervisor'"
                ).fetchall()
            elif channel in (CH_AGENT, CH_POOL):
                agents = conn.execute(
                    "SELECT name FROM agents WHERE role != 'supervisor'"
                ).fetchall()
            else:
                agents = conn.execute("SELECT name FROM agents").fetchall()
            for a in agents:
                conn.execute("""
                    INSERT INTO messages (from_agent, to_agent, body_json, channel)
                    VALUES (?, ?, ?, ?)
                """, (from_agent, a['name'], body_json, channel))
            return len(agents)
    return _retry_write(_do)


# ── Notes (persistent channel scratchpad) ─────────────────────────────────────

def post_note(channel, from_agent, body_json):
    """Append a note to a channel scratchpad. Returns note id."""
    result = [None]
    def _do():
        with _get_conn() as conn:
            cur = conn.execute("""
                INSERT INTO notes (channel, from_agent, body_json)
                VALUES (?, ?, ?)
            """, (channel, from_agent, body_json))
            result[0] = cur.lastrowid
    _retry_write(_do)
    return result[0]


def get_notes(channel, since=None, limit=50):
    """Read notes from a channel. since: ISO datetime string, returns newer notes only."""
    with _get_conn() as conn:
        if since:
            rows = conn.execute("""
                SELECT id, channel, from_agent, created_at, body_json
                FROM notes WHERE channel=? AND created_at > ?
                ORDER BY created_at ASC LIMIT ?
            """, (channel, since, limit)).fetchall()
        else:
            rows = conn.execute("""
                SELECT id, channel, from_agent, created_at, body_json
                FROM notes WHERE channel=?
                ORDER BY created_at DESC LIMIT ?
            """, (channel, limit)).fetchall()
        return [dict(r) for r in rows]


def get_note_count(channel, since=None):
    """Fast count of notes since timestamp. For lightweight polling."""
    with _get_conn() as conn:
        if since:
            return conn.execute(
                "SELECT COUNT(*) as n FROM notes WHERE channel=? AND created_at > ?",
                (channel, since)
            ).fetchone()['n']
        return conn.execute(
            "SELECT COUNT(*) as n FROM notes WHERE channel=?", (channel,)
        ).fetchone()['n']
