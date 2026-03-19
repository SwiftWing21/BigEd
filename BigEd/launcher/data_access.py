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
