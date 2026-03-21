"""
TECH_DEBT 4.4: Data Access Layer for launcher modules.
Centralizes all tools.db SQL behind a clean Python API.
Modules should use this instead of raw _db_conn() + SQL.

Usage:
    dal = DataAccess(db_path)
    dal.ensure_table("contacts", {"name": "TEXT", "email": "TEXT", "phone": "TEXT"})
    dal.insert("contacts", {"name": "Alice", "email": "a@b.com"})
    rows = dal.query("contacts", where={"name": "Alice"})
    dal.update("contacts", {"email": "new@b.com"}, where={"name": "Alice"})
    dal.delete("contacts", where={"name": "Alice"})
"""
import json
import sqlite3
import threading
from pathlib import Path
from typing import Any


class DataAccess:
    """Thread-safe SQLite data access layer for launcher modules."""

    def __init__(self, db_path: str | Path):
        self._db_path = Path(db_path)
        self._local = threading.local()
        self._schemas: dict[str, dict] = {}  # table -> {col: type}

    def _conn(self) -> sqlite3.Connection:
        """Get thread-local connection."""
        if not hasattr(self._local, "conn") or self._local.conn is None:
            self._local.conn = sqlite3.connect(
                str(self._db_path), check_same_thread=False, timeout=10
            )
            self._local.conn.row_factory = sqlite3.Row
            self._local.conn.execute("PRAGMA journal_mode=WAL")
            self._local.conn.execute("PRAGMA busy_timeout=10000")
        return self._local.conn

    def ensure_table(self, table: str, columns: dict[str, str],
                     if_not_exists: bool = True):
        """Create a table if it doesn't exist.

        Args:
            table: Table name (validated against injection)
            columns: {column_name: SQL_TYPE} e.g., {"name": "TEXT NOT NULL", "age": "INTEGER"}
        """
        if not table.isidentifier():
            raise ValueError(f"Invalid table name: {table}")
        cols = ", ".join(f"{k} {v}" for k, v in columns.items() if k.isidentifier())
        maybe = "IF NOT EXISTS" if if_not_exists else ""
        self._conn().execute(
            f"CREATE TABLE {maybe} {table} (id INTEGER PRIMARY KEY AUTOINCREMENT, {cols})"
        )
        self._conn().commit()
        self._schemas[table] = columns

    def insert(self, table: str, data: dict) -> int:
        """Insert a row. Returns the new row ID."""
        if not table.isidentifier():
            raise ValueError(f"Invalid table name: {table}")
        cols = [k for k in data.keys() if k.isidentifier()]
        placeholders = ", ".join("?" * len(cols))
        col_names = ", ".join(cols)
        values = [data[k] for k in cols]
        cur = self._conn().execute(
            f"INSERT INTO {table} ({col_names}) VALUES ({placeholders})", values
        )
        self._conn().commit()
        return cur.lastrowid

    def query(self, table: str, where: dict = None, order_by: str = None,
              limit: int = None) -> list[dict]:
        """Query rows from a table.

        Args:
            where: {column: value} for AND conditions
            order_by: e.g., "name ASC" or "id DESC"
            limit: max rows
        """
        if not table.isidentifier():
            raise ValueError(f"Invalid table name: {table}")
        sql = f"SELECT * FROM {table}"
        params = []
        if where:
            conditions = []
            for k, v in where.items():
                if k.isidentifier():
                    conditions.append(f"{k} = ?")
                    params.append(v)
            if conditions:
                sql += " WHERE " + " AND ".join(conditions)
        if order_by and all(p.split()[0].isidentifier() for p in order_by.split(",")):
            sql += f" ORDER BY {order_by}"
        if limit and isinstance(limit, int):
            sql += f" LIMIT {limit}"
        rows = self._conn().execute(sql, params).fetchall()
        return [dict(r) for r in rows]

    def update(self, table: str, data: dict, where: dict) -> int:
        """Update rows. Returns number of rows affected."""
        if not table.isidentifier():
            raise ValueError(f"Invalid table name: {table}")
        set_cols = [k for k in data.keys() if k.isidentifier()]
        set_clause = ", ".join(f"{k} = ?" for k in set_cols)
        set_values = [data[k] for k in set_cols]
        where_cols = [k for k in where.keys() if k.isidentifier()]
        where_clause = " AND ".join(f"{k} = ?" for k in where_cols)
        where_values = [where[k] for k in where_cols]
        cur = self._conn().execute(
            f"UPDATE {table} SET {set_clause} WHERE {where_clause}",
            set_values + where_values
        )
        self._conn().commit()
        return cur.rowcount

    def delete(self, table: str, where: dict) -> int:
        """Delete rows. Returns number of rows affected."""
        if not table.isidentifier():
            raise ValueError(f"Invalid table name: {table}")
        where_cols = [k for k in where.keys() if k.isidentifier()]
        where_clause = " AND ".join(f"{k} = ?" for k in where_cols)
        where_values = [where[k] for k in where_cols]
        cur = self._conn().execute(
            f"DELETE FROM {table} WHERE {where_clause}", where_values
        )
        self._conn().commit()
        return cur.rowcount

    def count(self, table: str, where: dict = None) -> int:
        """Count rows in a table."""
        if not table.isidentifier():
            raise ValueError(f"Invalid table name: {table}")
        sql = f"SELECT COUNT(*) as n FROM {table}"
        params = []
        if where:
            conditions = []
            for k, v in where.items():
                if k.isidentifier():
                    conditions.append(f"{k} = ?")
                    params.append(v)
            if conditions:
                sql += " WHERE " + " AND ".join(conditions)
        return self._conn().execute(sql, params).fetchone()["n"]

    def raw_query(self, sql: str, params: tuple = ()) -> list[dict]:
        """Execute raw SQL (for complex queries). Use sparingly."""
        rows = self._conn().execute(sql, params).fetchall()
        return [dict(r) for r in rows]

    def execute(self, sql: str, params: tuple = ()):
        """Execute raw SQL mutation (INSERT/UPDATE/DELETE). Use sparingly."""
        self._conn().execute(sql, params)
        self._conn().commit()

    def close(self):
        """Close the thread-local connection."""
        if hasattr(self._local, "conn") and self._local.conn:
            self._local.conn.close()
            self._local.conn = None

    def table_exists(self, table: str) -> bool:
        """Check if a table exists."""
        row = self._conn().execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
            (table,)
        ).fetchone()
        return row is not None

    def get_columns(self, table: str) -> list[str]:
        """Get column names for a table."""
        rows = self._conn().execute(f"PRAGMA table_info({table})").fetchall()
        return [r["name"] for r in rows]

    def init_launcher_db(self):
        """Initialize all launcher module tables. Single source of truth for tools.db schema."""
        schemas = {
            "agents": {
                "name": "TEXT NOT NULL",
                "role": "TEXT",
                "display_name": "TEXT",
                "theme": "TEXT DEFAULT 'default'",
            },
            "crm": {
                "name": "TEXT NOT NULL",
                "company": "TEXT",
                "email": "TEXT",
                "phone": "TEXT",
                "source": "TEXT",
                "status": "TEXT DEFAULT 'prospect'",
                "notes": "TEXT",
                "created_at": "TEXT DEFAULT (datetime('now'))",
            },
            "accounts": {
                "name": "TEXT NOT NULL",
                "type": "TEXT",
                "provider": "TEXT",
                "status": "TEXT DEFAULT 'active'",
                "notes": "TEXT",
                "renewal_date": "TEXT",
                "monthly_cost": "REAL DEFAULT 0",
                "contact": "TEXT",
                "url": "TEXT",
                "username": "TEXT",
                "category": "TEXT",
                "priority": "TEXT DEFAULT 'normal'",
            },
            "onboarding": {
                "customer": "TEXT NOT NULL",
                "step": "TEXT NOT NULL",
                "done": "INTEGER DEFAULT 0",
                "notes": "TEXT",
            },
            "customers": {
                "name": "TEXT NOT NULL",
                "fleet_id": "TEXT",
                "status": "TEXT DEFAULT 'active'",
                "deployed_at": "TEXT",
                "profile": "TEXT DEFAULT 'research'",
                "notes": "TEXT",
            },
        }
        for table, columns in schemas.items():
            self.ensure_table(table, columns)


# ─── Fleet DB helpers (fleet.db — read-only queries for the launcher UI) ─────

class FleetDB:
    """Static helper methods for querying fleet.db from the launcher.

    All methods accept a db_path argument so they work without global state.
    They open a short-lived connection, run the query, and close.
    """

    @staticmethod
    def _connect(db_path: str | Path, timeout: int = 10):
        """Open a connection to fleet.db with Row factory."""
        conn = sqlite3.connect(str(db_path), timeout=timeout)
        conn.row_factory = sqlite3.Row
        return conn

    @staticmethod
    def count_waiting_human(db_path: str | Path) -> int:
        """Count tasks with status WAITING_HUMAN."""
        try:
            if not Path(db_path).exists():
                return 0
            conn = FleetDB._connect(db_path)
            row = conn.execute(
                "SELECT COUNT(*) as n FROM tasks WHERE status = 'WAITING_HUMAN'"
            ).fetchone()
            conn.close()
            return row["n"] if row else 0
        except Exception:
            return 0

    @staticmethod
    def queued_task_count(db_path: str | Path) -> int:
        """Count tasks in active states (PENDING/RUNNING/WAITING)."""
        try:
            if not Path(db_path).exists():
                return 0
            conn = FleetDB._connect(db_path)
            row = conn.execute(
                "SELECT COUNT(*) as n FROM tasks WHERE status IN ('PENDING','RUNNING','WAITING')"
            ).fetchone()
            conn.close()
            return row["n"] if row else 0
        except Exception:
            return 0

    @staticmethod
    def agent_task_counts(db_path: str | Path) -> dict[str, int]:
        """Return {agent_name: done_task_count} for all agents with completed tasks."""
        result = {}
        try:
            if not Path(db_path).exists():
                return result
            conn = FleetDB._connect(db_path)
            for row in conn.execute(
                "SELECT assigned_to, COUNT(*) as n FROM tasks "
                "WHERE status='DONE' GROUP BY assigned_to"
            ).fetchall():
                if row["assigned_to"]:
                    result[row["assigned_to"]] = row["n"]
            conn.close()
        except Exception:
            pass
        return result

    @staticmethod
    def agent_token_speeds(db_path: str | Path) -> dict[str, float]:
        """Return {agent_name: avg_tok_per_sec} from usage table (last hour)."""
        result = {}
        try:
            if not Path(db_path).exists():
                return result
            conn = FleetDB._connect(db_path)
            try:
                for row in conn.execute(
                    "SELECT agent, AVG(tokens_per_sec) as avg_tps FROM usage "
                    "WHERE tokens_per_sec > 0 AND created_at > datetime('now', '-1 hour') "
                    "GROUP BY agent"
                ).fetchall():
                    if row["agent"]:
                        result[row["agent"]] = round(row["avg_tps"], 1)
            except sqlite3.OperationalError:
                pass  # tokens_per_sec column may not exist yet
            conn.close()
        except Exception:
            pass
        return result

    @staticmethod
    def agent_last_results(db_path: str | Path,
                           agent_names: list[str]) -> dict[str, str]:
        """Return {agent_name: truncated_result_summary} for given agents."""
        result = {}
        try:
            if not Path(db_path).exists():
                return result
            conn = FleetDB._connect(db_path)
            for aname in agent_names:
                if not aname:
                    continue
                row = conn.execute(
                    "SELECT result_json FROM tasks WHERE assigned_to=? AND status='DONE' "
                    "ORDER BY id DESC LIMIT 1", (aname,)
                ).fetchone()
                if row and row["result_json"]:
                    raw = str(row["result_json"]).strip()
                    try:
                        parsed = json.loads(raw)
                        if isinstance(parsed, dict):
                            txt = (parsed.get("summary") or parsed.get("output")
                                   or parsed.get("status") or parsed.get("error") or raw)
                        else:
                            txt = raw
                    except Exception:
                        txt = raw
                    txt = str(txt).strip()
                    if len(txt) > 45:
                        txt = txt[:42] + "..."
                    result[aname] = txt
            conn.close()
        except Exception:
            pass
        return result

    @staticmethod
    def waiting_human_by_agent(db_path: str | Path) -> tuple[int, set[str]]:
        """Return (total_waiting_count, {agent_names_with_waiting_tasks})."""
        total = 0
        agents = set()
        try:
            if not Path(db_path).exists():
                return total, agents
            conn = FleetDB._connect(db_path)
            try:
                for row in conn.execute(
                    "SELECT assigned_to, COUNT(*) as n FROM tasks "
                    "WHERE status='WAITING_HUMAN' GROUP BY assigned_to"
                ).fetchall():
                    total += row["n"]
                    if row["assigned_to"]:
                        agents.add(row["assigned_to"])
            except Exception:
                pass
            conn.close()
        except Exception:
            pass
        return total, agents

    @staticmethod
    def waiting_human_tasks(db_path: str | Path) -> list[dict]:
        """Fetch WAITING_HUMAN tasks with their operator questions.

        Returns list of dicts with keys: id, type, assigned_to, created_at, question.
        """
        waiting = []
        try:
            if not Path(db_path).exists():
                return waiting
            conn = FleetDB._connect(db_path, timeout=5)
            try:
                rows = conn.execute("""
                    SELECT t.id, t.type, t.assigned_to, t.created_at
                    FROM tasks t WHERE t.status = 'WAITING_HUMAN'
                    ORDER BY t.created_at ASC
                """).fetchall()
                for r in rows:
                    item = dict(r)
                    msg = conn.execute("""
                        SELECT body_json FROM messages
                        WHERE to_agent = 'operator'
                        AND body_json LIKE '%human_input_request%'
                        AND body_json LIKE ?
                        ORDER BY id DESC LIMIT 1
                    """, (f'%"task_id": {r["id"]}%',)).fetchone()
                    item["question"] = ""
                    if msg:
                        try:
                            body = json.loads(msg["body_json"])
                            item["question"] = body.get("question", "")
                        except Exception:
                            pass
                    waiting.append(item)
            finally:
                conn.close()
        except Exception:
            pass
        return waiting

    @staticmethod
    def send_human_response(db_path: str | Path, task_id: int,
                            response: str) -> bool:
        """Send an operator response to a WAITING_HUMAN task.

        Updates task status to PENDING and inserts a human_response message.
        Returns True on success.
        """
        try:
            conn = FleetDB._connect(db_path, timeout=5)
            try:
                row = conn.execute(
                    "SELECT assigned_to, payload_json FROM tasks WHERE id=?",
                    (task_id,)
                ).fetchone()
                if not row:
                    return False
                agent = row["assigned_to"]
                try:
                    payload = json.loads(row["payload_json"]) if row["payload_json"] else {}
                except Exception:
                    payload = {}
                payload["_human_response"] = response
                conn.execute("""
                    UPDATE tasks SET status='PENDING', payload_json=?
                    WHERE id=? AND status='WAITING_HUMAN'
                """, (json.dumps(payload), task_id))
                if agent:
                    conn.execute("""
                        INSERT INTO messages (from_agent, to_agent, body_json)
                        VALUES ('operator', ?, ?)
                    """, (agent, json.dumps({
                        "type": "human_response",
                        "task_id": task_id,
                        "response": response,
                    })))
                conn.commit()
            finally:
                conn.close()
            return True
        except Exception:
            return False

    @staticmethod
    def model_performance(db_path: str | Path) -> list[dict]:
        """Get per-model tok/s metrics from the usage table (last hour).

        Returns list of dicts with keys: model, avg_tps, calls, avg_ms, avg_iq.
        """
        try:
            if not Path(db_path).exists():
                return []
            conn = FleetDB._connect(db_path)
            try:
                rows = conn.execute("""
                    SELECT u.model,
                           ROUND(AVG(u.tokens_per_sec), 1) as avg_tps,
                           COUNT(*) as calls,
                           ROUND(AVG(u.eval_duration_ms), 0) as avg_ms,
                           ROUND(AVG(t.intelligence_score), 3) as avg_iq
                    FROM usage u
                    LEFT JOIN tasks t ON u.task_id = t.id
                    WHERE u.created_at > datetime('now', '-1 hour')
                      AND u.tokens_per_sec > 0
                    GROUP BY u.model
                    ORDER BY avg_tps DESC
                """).fetchall()
                return [dict(r) for r in rows]
            except sqlite3.OperationalError:
                return []  # columns don't exist yet
            finally:
                conn.close()
        except Exception:
            return []

    @staticmethod
    def recent_eval_scores(db_path, limit: int = 20) -> list[dict]:
        """Return last N tasks that have an intelligence_score, most recent first.

        Returns list of dicts: id, type, assigned_to, created_at, intelligence_score.
        """
        try:
            if not Path(db_path).exists():
                return []
            conn = FleetDB._connect(db_path)
            try:
                rows = conn.execute("""
                    SELECT id, type, assigned_to, created_at,
                           ROUND(intelligence_score, 3) as intelligence_score
                    FROM tasks
                    WHERE intelligence_score IS NOT NULL
                    ORDER BY id DESC LIMIT ?
                """, (limit,)).fetchall()
                return [dict(r) for r in rows]
            except sqlite3.OperationalError:
                return []
            finally:
                conn.close()
        except Exception:
            return []
