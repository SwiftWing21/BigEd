"""
Documentation generator skill — uses AST to extract classes, functions,
docstrings, and signatures from Python modules. Produces a markdown API
reference.
"""
SKILL_NAME = "doc_generate"
DESCRIPTION = "Auto-generate markdown API documentation for Python modules using AST."
VERSION = "1.0.0"
COMPLEXITY = "medium"
REQUIRES_NETWORK = False
SUITE = "content"


def run(payload, config):
    import ast
    import logging
    import re
    from datetime import date
    from pathlib import Path

    log = logging.getLogger(SKILL_NAME)

    FLEET_DIR = Path(__file__).parent.parent
    KNOWLEDGE_DIR = FLEET_DIR / "knowledge"
    DOCS_DIR = KNOWLEDGE_DIR / "docs"
    DOCS_DIR.mkdir(parents=True, exist_ok=True)

    module_path = payload.get("module", payload.get("file", ""))
    if not module_path:
        return {"status": "error", "message": "No module path provided in payload"}

    target = Path(module_path)
    if not target.is_absolute():
        target = FLEET_DIR.parent / module_path

    if not target.exists():
        return {"status": "error", "message": f"Module not found: {target}"}

    if not target.is_file() or target.suffix != ".py":
        return {"status": "error", "message": f"Not a Python file: {target}"}

    # Read and parse the module
    try:
        source = target.read_text(encoding="utf-8")
    except Exception:
        log.warning("Cannot read module %s", target, exc_info=True)
        return {"status": "error", "message": f"Cannot read: {target}"}

    try:
        tree = ast.parse(source)
    except SyntaxError as e:
        log.warning("Syntax error in %s: %s", target, e, exc_info=True)
        return {"status": "error", "message": f"Syntax error: {e}"}

    module_name = target.stem
    module_doc = ast.get_docstring(tree) or ""

    # Extract module-level constants
    constants = []
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name) and t.id.isupper():
                    try:
                        val_repr = ast.unparse(node.value)
                    except Exception:
                        val_repr = "..."
                    constants.append((t.id, val_repr))

    # Extract functions
    functions = []
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.FunctionDef) or isinstance(node, ast.AsyncFunctionDef):
            func_info = _extract_function(node)
            functions.append(func_info)

    # Extract classes
    classes = []
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.ClassDef):
            class_info = _extract_class(node)
            classes.append(class_info)

    # Build the markdown doc
    today = date.today().isoformat()
    md = [
        f"# `{module_name}` — API Reference",
        "",
        f"*Generated: {today}*",
        f"*Source: `{target}`*",
        "",
    ]

    if module_doc:
        md.append("## Module Description")
        md.append("")
        md.append(module_doc)
        md.append("")

    if constants:
        md.append("## Constants")
        md.append("")
        md.append("| Name | Value |")
        md.append("|------|-------|")
        for name, val in constants:
            safe_val = val[:60].replace("|", "\\|")
            md.append(f"| `{name}` | `{safe_val}` |")
        md.append("")

    if functions:
        md.append("## Functions")
        md.append("")
        for func in functions:
            md.append(f"### `{func['signature']}`")
            md.append("")
            if func["docstring"]:
                md.append(func["docstring"])
                md.append("")
            if func["args"]:
                md.append("**Parameters:**")
                md.append("")
                for arg in func["args"]:
                    annotation = f" (`{arg['annotation']}`)" if arg["annotation"] else ""
                    default = f" = `{arg['default']}`" if arg["default"] else ""
                    md.append(f"- `{arg['name']}`{annotation}{default}")
                md.append("")
            if func["returns"]:
                md.append(f"**Returns:** `{func['returns']}`")
                md.append("")
            if func["decorators"]:
                md.append(
                    f"**Decorators:** {', '.join(f'`@{d}`' for d in func['decorators'])}"
                )
                md.append("")
            md.append("---")
            md.append("")

    if classes:
        md.append("## Classes")
        md.append("")
        for cls in classes:
            bases = f"({', '.join(cls['bases'])})" if cls["bases"] else ""
            md.append(f"### `class {cls['name']}{bases}`")
            md.append("")
            if cls["docstring"]:
                md.append(cls["docstring"])
                md.append("")
            if cls["methods"]:
                md.append("**Methods:**")
                md.append("")
                for method in cls["methods"]:
                    md.append(f"#### `{method['signature']}`")
                    md.append("")
                    if method["docstring"]:
                        md.append(method["docstring"])
                        md.append("")
                    if method["args"]:
                        md.append("**Parameters:**")
                        md.append("")
                        for arg in method["args"]:
                            if arg["name"] == "self":
                                continue
                            annotation = (
                                f" (`{arg['annotation']}`)"
                                if arg["annotation"] else ""
                            )
                            default = (
                                f" = `{arg['default']}`" if arg["default"] else ""
                            )
                            md.append(f"- `{arg['name']}`{annotation}{default}")
                        md.append("")
                    if method["returns"]:
                        md.append(f"**Returns:** `{method['returns']}`")
                        md.append("")
            md.append("---")
            md.append("")

    # Summary section
    md.append("## Summary")
    md.append("")
    md.append(f"- **Module:** `{module_name}`")
    md.append(f"- **Constants:** {len(constants)}")
    md.append(f"- **Functions:** {len(functions)}")
    md.append(f"- **Classes:** {len(classes)}")
    total_methods = sum(len(c["methods"]) for c in classes)
    md.append(f"- **Class methods:** {total_methods}")
    md.append("")

    report = "\n".join(md)
    safe_name = re.sub(r"[^\w.-]", "_", module_name)
    out_file = DOCS_DIR / f"doc_{safe_name}_{today}.md"
    out_file.write_text(report, encoding="utf-8")

    return {
        "status": "ok",
        "saved_to": str(out_file),
        "module": module_name,
        "constants": len(constants),
        "functions": len(functions),
        "classes": len(classes),
        "methods": total_methods,
    }


def _extract_function(node):
    """Extract function info from an AST FunctionDef node."""
    import ast

    name = node.name
    docstring = ast.get_docstring(node) or ""
    args = _extract_args(node.args)
    returns = ""
    if node.returns:
        try:
            returns = ast.unparse(node.returns)
        except Exception:
            returns = "..."

    decorators = []
    for dec in node.decorator_list:
        try:
            decorators.append(ast.unparse(dec))
        except Exception:
            decorators.append("...")

    # Build signature
    arg_parts = []
    for arg in args:
        part = arg["name"]
        if arg["annotation"]:
            part += f": {arg['annotation']}"
        if arg["default"]:
            part += f" = {arg['default']}"
        arg_parts.append(part)
    sig = f"{name}({', '.join(arg_parts)})"
    if returns:
        sig += f" -> {returns}"

    is_async = isinstance(node, ast.AsyncFunctionDef)
    if is_async:
        sig = f"async {sig}"

    return {
        "name": name,
        "signature": sig,
        "docstring": docstring,
        "args": args,
        "returns": returns,
        "decorators": decorators,
        "is_async": is_async,
    }


def _extract_class(node):
    """Extract class info from an AST ClassDef node."""
    import ast

    name = node.name
    docstring = ast.get_docstring(node) or ""

    bases = []
    for base in node.bases:
        try:
            bases.append(ast.unparse(base))
        except Exception:
            bases.append("...")

    methods = []
    for item in node.body:
        if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
            methods.append(_extract_function(item))

    return {
        "name": name,
        "docstring": docstring,
        "bases": bases,
        "methods": methods,
    }


def _extract_args(args_node):
    """Extract argument info from an AST arguments node."""
    import ast

    result = []
    # Calculate defaults offset — defaults align to the end of args
    num_args = len(args_node.args)
    num_defaults = len(args_node.defaults)
    default_offset = num_args - num_defaults

    for i, arg in enumerate(args_node.args):
        annotation = ""
        if arg.annotation:
            try:
                annotation = ast.unparse(arg.annotation)
            except Exception:
                annotation = "..."

        default = ""
        default_idx = i - default_offset
        if default_idx >= 0 and default_idx < len(args_node.defaults):
            try:
                default = ast.unparse(args_node.defaults[default_idx])
            except Exception:
                default = "..."

        result.append({
            "name": arg.arg,
            "annotation": annotation,
            "default": default,
        })

    # *args
    if args_node.vararg:
        annotation = ""
        if args_node.vararg.annotation:
            try:
                annotation = ast.unparse(args_node.vararg.annotation)
            except Exception:
                pass
        result.append({
            "name": f"*{args_node.vararg.arg}",
            "annotation": annotation,
            "default": "",
        })

    # keyword-only args
    for i, arg in enumerate(args_node.kwonlyargs):
        annotation = ""
        if arg.annotation:
            try:
                annotation = ast.unparse(arg.annotation)
            except Exception:
                pass
        default = ""
        if i < len(args_node.kw_defaults) and args_node.kw_defaults[i] is not None:
            try:
                default = ast.unparse(args_node.kw_defaults[i])
            except Exception:
                default = "..."
        result.append({
            "name": arg.arg,
            "annotation": annotation,
            "default": default,
        })

    # **kwargs
    if args_node.kwarg:
        annotation = ""
        if args_node.kwarg.annotation:
            try:
                annotation = ast.unparse(args_node.kwarg.annotation)
            except Exception:
                pass
        result.append({
            "name": f"**{args_node.kwarg.arg}",
            "annotation": annotation,
            "default": "",
        })

    return result
