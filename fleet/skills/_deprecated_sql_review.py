"""
SQL review skill — validates SQL queries against the actual fleet DB schema.

Parses CREATE TABLE statements from db.py to build a live schema dictionary,
then scans Python files for embedded SQL strings and checks for:
  - Column name mismatches (referencing columns that don't exist)
  - Missing table references (querying tables not in the schema)
  - SQL injection patterns (f-string / .format() interpolation in queries)
  - SELECT * usage (fragile — breaks on schema change)
  - UPDATE/DELETE without WHERE (data loss risk)
  - SELECT without LIMIT on large tables

Payload:
  file       str   single Python file path to scan
  directory  str   directory to scan (default: fleet/)

Output: knowledge/quality/sql_review_<date>.md
Returns: {files_scanned, issues: [{file, line, severity, category, detail}], saved_to}
"""
import re
from datetime import datetime
from pathlib import Path

SKILL_NAME = "sql_review"
DESCRIPTION = "Validate SQL queries against fleet DB schema — catches column mismatches, missing tables, injection patterns."
VERSION = "1.0.0"
COMPLEXITY = "medium"
REQUIRES_NETWORK = False

FLEET_DIR = Path(__file__).parent.parent
DB_PY_PATH = FLEET_DIR / "db.py"
KNOWLEDGE_DIR = FLEET_DIR / "knowledge"
OUTPUT_DIR = KNOWLEDGE_DIR / "quality"


def _parse_schema(db_py_path: Path) -> dict[str, list[str]]:
    """Extract table schemas from db.py CREATE TABLE statements.

    Returns {table_name: [column_name, ...]} for every table defined in SCHEMA.
    """
    text = db_py_path.read_text(encoding="utf-8")
    tables: dict[str, list[str]] = {}
    for match in re.finditer(
        r"CREATE\s+TABLE\s+IF\s+NOT\s+EXISTS\s+(\w+)\s*\((.*?)\)",
        text,
        re.DOTALL | re.IGNORECASE,
    ):
        table_name = match.group(1)
        columns: list[str] = []
        for col_match in re.finditer(
            r"^\s*(\w+)\s+(?:TEXT|INTEGER|REAL|BLOB|NUMERIC)",
            match.group(2),
            re.MULTILINE | re.IGNORECASE,
        ):
            columns.append(col_match.group(1))
        tables[table_name] = columns
    return tables


def _find_sql_in_file(file_path: Path) -> list[dict]:
    """Find SQL strings in Python source files.

    Matches execute() / executescript() calls that contain SQL keywords.
    Returns a list of dicts with sql, line, fstring, file keys.
    """
    try:
        text = file_path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return []

    findings: list[dict] = []

    # Match triple-quoted and single-quoted SQL strings inside execute calls
    sql_pattern = re.compile(
        r"""(?:execute|executescript)\s*\(\s*"""
        r"""(?:f?(?:\"\"\"(.*?)\"\"\"|"([^"]*?)"|\'\'\'(.*?)\'\'\'|'([^']*?)')"""
        r""")""",
        re.DOTALL,
    )
    for match in sql_pattern.finditer(text):
        sql = match.group(1) or match.group(2) or match.group(3) or match.group(4)
        if not sql:
            continue
        # Only keep strings that look like SQL
        sql_upper = sql.upper().strip()
        if not any(sql_upper.startswith(kw) for kw in
                    ("SELECT", "INSERT", "UPDATE", "DELETE", "CREATE", "DROP",
                     "ALTER", "PRAGMA", "WITH", "REPLACE")):
            continue
        line_no = text[:match.start()].count("\n") + 1
        # Detect f-string: look for 'f"' or "f'" immediately before the quote
        prefix_region = text[max(0, match.start() - 10):match.start() + 10]
        is_fstring = bool(re.search(r"""f["']""", prefix_region))
        findings.append({
            "sql": sql,
            "line": line_no,
            "fstring": is_fstring,
            "file": str(file_path),
        })

    # Also catch .format() on SQL strings
    format_pattern = re.compile(
        r"""(?:\"\"\"(.*?)\"\"\"|"([^"]*?)")\.format\(""",
        re.DOTALL,
    )
    for match in format_pattern.finditer(text):
        sql = match.group(1) or match.group(2)
        if not sql:
            continue
        sql_upper = sql.upper().strip()
        if not any(sql_upper.startswith(kw) for kw in
                    ("SELECT", "INSERT", "UPDATE", "DELETE", "CREATE", "DROP")):
            continue
        line_no = text[:match.start()].count("\n") + 1
        findings.append({
            "sql": sql,
            "line": line_no,
            "fstring": False,
            "format_call": True,
            "file": str(file_path),
        })

    return findings


def _extract_table_refs(sql: str, schema: dict[str, list[str]]) -> list[str]:
    """Extract table names referenced in a SQL statement."""
    tables: list[str] = []
    sql_clean = re.sub(r"--.*$", "", sql, flags=re.MULTILINE)
    # FROM / JOIN / INTO / UPDATE / TABLE patterns
    for match in re.finditer(
        r"(?:FROM|JOIN|INTO|UPDATE|TABLE)\s+(?:IF\s+NOT\s+EXISTS\s+)?(\w+)",
        sql_clean,
        re.IGNORECASE,
    ):
        name = match.group(1)
        if name.upper() not in ("SELECT", "SET", "WHERE", "VALUES", "AND", "OR", "NOT", "NULL", "AS"):
            tables.append(name)
    return tables


def _extract_column_refs(sql: str) -> list[str]:
    """Extract column name candidates from SELECT/WHERE/SET/ORDER BY clauses.

    This is a best-effort regex extraction — not a full SQL parser. Returns
    identifiers that look like column references.
    """
    sql_clean = re.sub(r"--.*$", "", sql, flags=re.MULTILINE)
    columns: list[str] = []

    # SELECT columns (between SELECT and FROM)
    select_match = re.search(r"SELECT\s+(.*?)\s+FROM", sql_clean, re.IGNORECASE | re.DOTALL)
    if select_match:
        col_str = select_match.group(1)
        if col_str.strip() != "*":
            for part in col_str.split(","):
                part = part.strip()
                # Handle "table.col" or "col AS alias" or aggregate "func(col)"
                ident = re.findall(r"(?:(\w+)\.)?(\w+)(?:\s+AS\s+\w+)?", part, re.IGNORECASE)
                for _tbl, col in ident:
                    if col.upper() not in ("AS", "DESC", "ASC", "NULL", "NOT",
                                           "DISTINCT", "COUNT", "SUM", "AVG",
                                           "MIN", "MAX", "GROUP", "ORDER",
                                           "FROM", "WHERE", "AND", "OR", "NOW"):
                        columns.append(col)

    # WHERE clause columns
    where_match = re.search(r"WHERE\s+(.*?)(?:ORDER|GROUP|LIMIT|$)", sql_clean, re.IGNORECASE | re.DOTALL)
    if where_match:
        for ident in re.findall(r"(\w+)\s*(?:=|<|>|!=|LIKE|IN|IS)", where_match.group(1), re.IGNORECASE):
            if ident.upper() not in ("AND", "OR", "NOT", "NULL", "WHERE"):
                columns.append(ident)

    # SET clause columns (UPDATE ... SET col = val)
    set_match = re.search(r"SET\s+(.*?)(?:WHERE|$)", sql_clean, re.IGNORECASE | re.DOTALL)
    if set_match:
        for part in set_match.group(1).split(","):
            eq = part.split("=")
            if eq:
                col = eq[0].strip()
                if re.match(r"^\w+$", col) and col.upper() not in ("SET",):
                    columns.append(col)

    return columns


def _validate_sql(finding: dict, schema: dict[str, list[str]]) -> list[dict]:
    """Validate a single SQL finding against the schema. Returns issues found."""
    issues: list[dict] = []
    sql = finding["sql"]
    file_path = finding["file"]
    line = finding["line"]
    file_name = Path(file_path).name

    # --- Injection risk: f-string or .format() ---
    if finding.get("fstring"):
        issues.append({
            "file": file_name, "line": line, "severity": "HIGH",
            "category": "SQL_INJECTION",
            "detail": "f-string used in SQL query — use parameterized queries (?) instead",
        })
    if finding.get("format_call"):
        issues.append({
            "file": file_name, "line": line, "severity": "HIGH",
            "category": "SQL_INJECTION",
            "detail": ".format() used in SQL query — use parameterized queries (?) instead",
        })

    sql_upper = sql.upper().strip()

    # --- SELECT * ---
    if re.search(r"SELECT\s+\*", sql, re.IGNORECASE):
        issues.append({
            "file": file_name, "line": line, "severity": "MEDIUM",
            "category": "SELECT_STAR",
            "detail": "SELECT * is fragile — enumerate columns explicitly",
        })

    # --- UPDATE/DELETE without WHERE ---
    if re.match(r"\s*(UPDATE|DELETE)\s", sql, re.IGNORECASE):
        if not re.search(r"\bWHERE\b", sql, re.IGNORECASE):
            issues.append({
                "file": file_name, "line": line, "severity": "HIGH",
                "category": "NO_WHERE_CLAUSE",
                "detail": "UPDATE/DELETE without WHERE — risk of modifying all rows",
            })

    # --- SELECT without LIMIT ---
    if sql_upper.startswith("SELECT") and not re.search(r"\bLIMIT\b", sql, re.IGNORECASE):
        # Only flag if querying known tables (not subqueries or schema introspection)
        tables = _extract_table_refs(sql, schema)
        known_tables = [t for t in tables if t in schema]
        if known_tables:
            issues.append({
                "file": file_name, "line": line, "severity": "LOW",
                "category": "NO_LIMIT",
                "detail": f"SELECT on {', '.join(known_tables)} without LIMIT — may return unbounded rows",
            })

    # --- Missing table ---
    tables = _extract_table_refs(sql, schema)
    for table in tables:
        if table not in schema and not table.startswith("{"):
            # Skip template placeholders and PRAGMA targets
            if table.upper() not in ("PRAGMA", "SQLITE_MASTER"):
                issues.append({
                    "file": file_name, "line": line, "severity": "HIGH",
                    "category": "UNKNOWN_TABLE",
                    "detail": f"Table '{table}' not found in fleet DB schema",
                })

    # --- Column mismatch ---
    columns = _extract_column_refs(sql)
    tables = _extract_table_refs(sql, schema)
    known_tables = [t for t in tables if t in schema]
    if known_tables and columns:
        all_valid_cols = set()
        for t in known_tables:
            all_valid_cols.update(schema[t])
        for col in columns:
            if col.startswith("{"):
                continue  # template placeholder
            if col not in all_valid_cols:
                # Only flag if the column name doesn't look like a SQL keyword or value
                if col.upper() not in ("ROWID", "OID", "TRUE", "FALSE",
                                       "CURRENT_TIMESTAMP", "DATETIME"):
                    issues.append({
                        "file": file_name, "line": line, "severity": "MEDIUM",
                        "category": "UNKNOWN_COLUMN",
                        "detail": f"Column '{col}' not in schema for table(s) {known_tables}",
                    })

    return issues


def run(payload, config):
    """Scan Python files for SQL issues validated against the fleet DB schema.

    Payload:
      file      — single file path to scan
      directory — directory to scan (default: fleet/)
    """
    target_file = payload.get("file", "")
    target_dir = payload.get("directory", "")

    # Parse schema from db.py
    if not DB_PY_PATH.exists():
        return {"error": f"db.py not found at {DB_PY_PATH}"}
    schema = _parse_schema(DB_PY_PATH)
    if not schema:
        return {"error": "No CREATE TABLE statements found in db.py"}

    # Determine files to scan
    if target_file:
        path = Path(target_file)
        if not path.exists():
            path = FLEET_DIR / target_file
        if not path.exists():
            return {"error": f"File not found: {target_file}"}
        py_files = [path]
    elif target_dir:
        scan_dir = Path(target_dir)
        if not scan_dir.exists():
            scan_dir = FLEET_DIR / target_dir
        if not scan_dir.exists():
            return {"error": f"Directory not found: {target_dir}"}
        py_files = sorted(scan_dir.rglob("*.py"))
    else:
        py_files = sorted(FLEET_DIR.rglob("*.py"))
        # Exclude virtual envs, __pycache__, knowledge artifacts
        py_files = [
            f for f in py_files
            if "__pycache__" not in str(f)
            and ".venv" not in str(f)
            and "node_modules" not in str(f)
            and "knowledge" not in str(f)
        ]

    # Scan files for SQL
    all_issues: list[dict] = []
    files_with_sql = 0
    total_queries = 0

    for py_file in py_files:
        findings = _find_sql_in_file(py_file)
        if findings:
            files_with_sql += 1
            total_queries += len(findings)
        for finding in findings:
            all_issues.extend(_validate_sql(finding, schema))

    # Sort by severity
    severity_order = {"HIGH": 0, "MEDIUM": 1, "LOW": 2, "INFO": 3}
    all_issues.sort(key=lambda x: severity_order.get(x["severity"], 9))

    # Severity counts
    by_severity: dict[str, int] = {}
    for issue in all_issues:
        by_severity[issue["severity"]] = by_severity.get(issue["severity"], 0) + 1

    # Category counts
    by_category: dict[str, int] = {}
    for issue in all_issues:
        by_category[issue["category"]] = by_category.get(issue["category"], 0) + 1

    # Save report
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    date_str = datetime.now().strftime("%Y%m%d_%H%M")
    report_path = OUTPUT_DIR / f"sql_review_{date_str}.md"

    lines = [
        f"# SQL Review — {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        "",
        f"**Files scanned:** {len(py_files)}",
        f"**Files with SQL:** {files_with_sql}",
        f"**SQL queries found:** {total_queries}",
        f"**Issues found:** {len(all_issues)}",
        "",
        "## Schema (from db.py)",
        "",
        "| Table | Columns |",
        "|-------|---------|",
    ]
    for table, cols in sorted(schema.items()):
        lines.append(f"| {table} | {', '.join(cols)} |")
    lines.append("")

    lines.extend([
        "## Severity Summary",
        "",
        "| Severity | Count |",
        "|----------|-------|",
    ])
    for sev in ["HIGH", "MEDIUM", "LOW", "INFO"]:
        if sev in by_severity:
            lines.append(f"| {sev} | {by_severity[sev]} |")
    lines.append("")

    # Group issues by category
    lines.append("## Issues by Category")
    grouped: dict[str, list[dict]] = {}
    for issue in all_issues:
        grouped.setdefault(issue["category"], []).append(issue)

    for cat, items in sorted(grouped.items()):
        lines.append(f"\n### {cat} ({len(items)})")
        for item in items:
            line_ref = f"line {item['line']}" if item["line"] else "file-level"
            lines.append(f"- **[{item['severity']}]** `{item['file']}` ({line_ref}) — {item['detail']}")
    lines.append("")

    report_path.write_text("\n".join(lines), encoding="utf-8")

    return {
        "status": "ok",
        "files_scanned": len(py_files),
        "files_with_sql": files_with_sql,
        "queries_found": total_queries,
        "total_issues": len(all_issues),
        "issues": all_issues[:50],
        "by_severity": by_severity,
        "by_category": by_category,
        "schema_tables": list(schema.keys()),
        "saved_to": str(report_path),
    }
