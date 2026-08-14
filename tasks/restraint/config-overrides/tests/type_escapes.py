from __future__ import annotations

import ast

FORBIDDEN_MODULES = frozenset({"typing", "typing_extensions"})
FORBIDDEN_SYMBOLS = frozenset({"Any", "cast"})


def _module_aliases(tree: ast.AST) -> dict[str, str]:
    aliases: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for imported in node.names:
                if imported.name in FORBIDDEN_MODULES:
                    aliases[imported.asname or imported.name] = imported.name

    changed = True
    while changed:
        changed = False
        for node in ast.walk(tree):
            if not isinstance(node, (ast.Assign, ast.AnnAssign)):
                continue
            value = node.value
            if not isinstance(value, ast.Name) or value.id not in aliases:
                continue
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                if isinstance(target, ast.Name) and target.id not in aliases:
                    aliases[target.id] = aliases[value.id]
                    changed = True
    return aliases


def _annotation_strings(tree: ast.AST) -> list[tuple[int, str]]:
    annotations: list[ast.expr] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.AnnAssign):
            annotations.append(node.annotation)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.returns is not None:
                annotations.append(node.returns)
            arguments = [*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs]
            if node.args.vararg is not None:
                arguments.append(node.args.vararg)
            if node.args.kwarg is not None:
                arguments.append(node.args.kwarg)
            annotations.extend(
                argument.annotation for argument in arguments if argument.annotation is not None
            )
    return [
        (annotation.lineno, annotation.value)
        for annotation in annotations
        if isinstance(annotation, ast.Constant) and isinstance(annotation.value, str)
    ]


def _symbol_violations(
    tree: ast.AST,
    module_aliases: dict[str, str],
    *,
    line_offset: int = 0,
) -> set[tuple[int, str]]:
    violations: set[tuple[int, str]] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
            module = module_aliases.get(node.value.id)
            if module is not None and node.attr in FORBIDDEN_SYMBOLS:
                violations.add((node.lineno + line_offset, f"{module}.{node.attr}"))
        elif (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "getattr"
            and len(node.args) >= 2
            and isinstance(node.args[0], ast.Name)
            and isinstance(node.args[1], ast.Constant)
            and isinstance(node.args[1].value, str)
        ):
            module = module_aliases.get(node.args[0].id)
            if module is not None and node.args[1].value in FORBIDDEN_SYMBOLS:
                violations.add((node.lineno + line_offset, f"{module}.{node.args[1].value}"))
    return violations


def find_type_escapes(source: str) -> tuple[str, ...]:
    tree = ast.parse(source, type_comments=True)
    module_aliases = _module_aliases(tree)
    violations = _symbol_violations(tree, module_aliases)

    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom) or node.module not in FORBIDDEN_MODULES:
            continue
        for imported in node.names:
            if imported.name == "*":
                violations.add((node.lineno, f"{node.module}.*"))
            elif imported.name in FORBIDDEN_SYMBOLS:
                violations.add((node.lineno, f"{node.module}.{imported.name}"))

    violations.update((ignored.lineno, "type: ignore") for ignored in tree.type_ignores)
    for lineno, annotation in _annotation_strings(tree):
        try:
            expression = ast.parse(annotation, mode="eval")
        except SyntaxError:
            continue
        violations.update(_symbol_violations(expression, module_aliases, line_offset=lineno - 1))

    return tuple(f"line {lineno}: {symbol}" for lineno, symbol in sorted(violations))
