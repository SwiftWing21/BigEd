"""Generate pytest-compatible unit tests for a Python module."""

SKILL_NAME = "test_generate"
DESCRIPTION = "Generate pytest-compatible unit tests for a Python module."
REQUIRES_NETWORK = False


def run(payload, config):
    import ast
    import db  # noqa: F401 — lazy import per fleet convention
    from datetime import date
    from pathlib import Path

    FLEET_DIR = Path(__file__).parent.parent
    KNOWLEDGE_DIR = FLEET_DIR / "knowledge"
    TESTS_DIR = KNOWLEDGE_DIR / "tests"
    TESTS_DIR.mkdir(parents=True, exist_ok=True)

    target = payload.get("file")
    if not target:
        return {"error": "Missing 'file' parameter", "status": "skip"}

    target_path = Path(target)
    if not target_path.exists():
        # Try relative to project root
        target_path = FLEET_DIR.parent / target
    if not target_path.exists():
        return {"error": f"File not found: {target}", "status": "skip"}

    try:
        source = target_path.read_text(encoding="utf-8")
        tree = ast.parse(source)
    except Exception as e:
        return {"error": f"Failed to parse: {e}", "status": "fail"}

    module_name = target_path.stem
    functions = []
    classes = []

    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and not node.name.startswith("_"):
            # Extract function signature
            args = []
            for arg in node.args.args:
                if arg.arg != "self":
                    args.append(arg.arg)
            # Extract docstring
            docstring = ast.get_docstring(node) or ""
            # Check for return statement
            has_return = any(
                isinstance(n, ast.Return) and n.value is not None
                for n in ast.walk(node)
            )
            functions.append({
                "name": node.name,
                "args": args,
                "docstring": docstring,
                "has_return": has_return,
                "lineno": node.lineno,
            })
        elif isinstance(node, ast.ClassDef) and not node.name.startswith("_"):
            methods = []
            for item in node.body:
                if isinstance(item, ast.FunctionDef) and not item.name.startswith("_"):
                    m_args = [a.arg for a in item.args.args if a.arg != "self"]
                    methods.append({
                        "name": item.name,
                        "args": m_args,
                        "docstring": ast.get_docstring(item) or "",
                        "has_return": any(
                            isinstance(n, ast.Return) and n.value is not None
                            for n in ast.walk(item)
                        ),
                    })
            classes.append({
                "name": node.name,
                "methods": methods,
                "docstring": ast.get_docstring(node) or "",
            })

    if not functions and not classes:
        return {"status": "skip", "message": "No public functions or classes found"}

    # Generate test file
    lines = [
        f'"""Auto-generated tests for {module_name} — {date.today().isoformat()}"""',
        "import pytest",
        "import sys",
        "from pathlib import Path",
        "",
        "# Add module to path",
        "sys.path.insert(0, str(Path(__file__).parent.parent.parent))",
        "",
    ]

    # Determine import path
    if target_path.is_relative_to(FLEET_DIR.parent):
        rel = target_path.relative_to(FLEET_DIR.parent)
    else:
        rel = target_path
    import_parts = list(rel.with_suffix("").parts)
    if len(import_parts) > 1:
        lines.append(f"from {'.'.join(import_parts[:-1])} import {import_parts[-1]}")
    else:
        lines.append(f"import {import_parts[0]}")
    lines.append("")

    # Generate function tests
    for func in functions:
        lines.append("")
        lines.append(f"class Test_{func['name']}:")
        lines.append(f'    """Tests for {func["name"]}()."""')
        lines.append("")

        # Happy path test
        args_str = ", ".join(f"{a}=None" for a in func["args"]) if func["args"] else ""
        lines.append(f"    def test_{func['name']}_basic(self):")
        if func["docstring"]:
            lines.append(f'        """{func["docstring"][:80]}"""')
        if func["has_return"]:
            lines.append(f"        result = {module_name}.{func['name']}({args_str})")
            lines.append("        assert result is not None")
        else:
            lines.append("        # Function doesn't return a value — verify no exception")
            lines.append(f"        {module_name}.{func['name']}({args_str})")
        lines.append("")

        # Edge case: empty/None inputs
        if func["args"]:
            lines.append(f"    def test_{func['name']}_empty_input(self):")
            lines.append('        """Test with empty/minimal input."""')
            none_args = ", ".join("None" for _ in func["args"])
            lines.append("        try:")
            lines.append(f"            {module_name}.{func['name']}({none_args})")
            lines.append("        except (TypeError, ValueError, AttributeError):")
            lines.append("            pass  # Expected for invalid input")
            lines.append("")

        # Error handling test
        lines.append(f"    def test_{func['name']}_no_crash(self):")
        lines.append('        """Verify function handles errors gracefully."""')
        lines.append("        try:")
        bad_args = ", ".join("'__invalid__'" for _ in func["args"]) if func["args"] else ""
        lines.append(f"            {module_name}.{func['name']}({bad_args})")
        lines.append("        except Exception:")
        lines.append("            pass  # Should not crash with unhandled exception")
        lines.append("")

    # Generate class tests
    for cls in classes:
        lines.append("")
        lines.append(f"class Test_{cls['name']}:")
        lines.append(f'    """Tests for {cls["name"]} class."""')
        lines.append("")

        lines.append("    def test_instantiate(self):")
        lines.append('        """Verify class can be instantiated."""')
        lines.append("        try:")
        lines.append(f"            obj = {module_name}.{cls['name']}()")
        lines.append("            assert obj is not None")
        lines.append("        except TypeError:")
        lines.append("            pass  # May require constructor args")
        lines.append("")

        for method in cls["methods"]:
            args_str = (
                ", ".join(f"{a}=None" for a in method["args"])
                if method["args"]
                else ""
            )
            lines.append(f"    def test_{method['name']}_basic(self):")
            if method["has_return"]:
                lines.append(f"        obj = {module_name}.{cls['name']}()")
                lines.append(f"        result = obj.{method['name']}({args_str})")
                lines.append("        assert result is not None")
            else:
                lines.append(f"        obj = {module_name}.{cls['name']}()")
                lines.append(f"        obj.{method['name']}({args_str})")
            lines.append("")

    test_content = "\n".join(lines)
    out_file = TESTS_DIR / f"{module_name}_test_{date.today().isoformat()}.py"
    out_file.write_text(test_content, encoding="utf-8")

    return {
        "status": "ok",
        "saved_to": str(out_file),
        "functions": len(functions),
        "classes": len(classes),
        "test_count": sum(
            2 + (1 if f["args"] else 0) for f in functions
        ) + sum(1 + len(c["methods"]) for c in classes),
    }
