"""Index Python functions/classes in a directory using AST parsing."""
SKILL_NAME = "code_index"
DESCRIPTION = "Index Python functions/classes in a directory using AST parsing."

import ast
import json
from pathlib import Path


def run(payload, config):
    target = Path(payload.get("directory", str(Path(__file__).parent.parent.parent)))
    max_files = int(payload.get("max_files", 20))

    out_file = Path(__file__).parent.parent / "knowledge" / "code_index.jsonl"
    index = []

    py_files = [p for p in target.rglob("*.py")
                if ".venv" not in str(p) and "__pycache__" not in str(p)][:max_files]

    for py_file in py_files:
        try:
            source = py_file.read_text(errors="ignore")
            tree = ast.parse(source)
        except Exception:
            continue

        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                index.append({
                    "file": str(py_file.relative_to(target)),
                    "type": type(node).__name__,
                    "name": node.name,
                    "args": [a.arg for a in node.args.args] if hasattr(node, "args") else [],
                    "docstring": (ast.get_docstring(node) or "")[:200],
                    "line": node.lineno,
                })

    with open(out_file, "w") as f:
        for item in index:
            f.write(json.dumps(item) + "\n")

    return {
        "functions_indexed": len(index),
        "files_scanned": len(py_files),
        "saved_to": str(out_file),
    }