from __future__ import annotations

import ast
import builtins
import hashlib
import json
import re
import subprocess
import uuid
from pathlib import Path

from .models import Analysis, Call, Diagnostic, Entity, Location, Scope

REPOSITORY = Path(__file__).resolve().parents[2]


def stable_id(*parts: object) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, "codescape:" + ":".join(map(str, parts))))


def digest(value: str | bytes) -> str:
    return hashlib.sha256(value.encode() if isinstance(value, str) else value).hexdigest()


def position(path: str, text: str, node: ast.AST) -> Location:
    lines = text.splitlines(keepends=True)
    start = getattr(node, "lineno", 1)
    end = getattr(node, "end_lineno", start)
    begin_byte = sum(len(line.encode()) for line in lines[: start - 1]) + getattr(node, "col_offset", 0)
    end_byte = sum(len(line.encode()) for line in lines[: end - 1]) + getattr(node, "end_col_offset", 0)
    return Location(path=path, start_line=start, end_line=end, start_byte=begin_byte, end_byte=end_byte)


def python_analysis(files: dict[str, str], version: str) -> Analysis:
    result = Analysis(tools={"python": "ast/3.12; conservative-bindings/1"})
    symbols: dict[tuple[str, str], list[Entity]] = {}
    imports: dict[str, dict[str, tuple[str, str | None]]] = {}
    unstable: dict[str, set[str]] = {}
    decorated: set[str] = set()
    pending: list[tuple[Call, str, list[str], set[str]]] = []

    def module_name(path: str) -> str:
        return path.removesuffix(".py").replace("/", ".").removesuffix(".__init__")

    for path, text in files.items():
        if not path.endswith(".py"):
            continue
        try:
            tree = ast.parse(text, filename=path)
        except SyntaxError as error:
            result.diagnostics.append(
                Diagnostic(category="parse_failed", path=path, line=error.lineno, message=str(error.msg))
            )
            continue
        imports[path] = {}
        result.file_hashes[path] = digest(ast.dump(tree, include_attributes=False))
        if tree.body:
            result.readable_paths.append(path)
        unstable[path] = set()

        def module_bindings(item: ast.AST) -> None:
            if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                return
            if isinstance(item, ast.Name) and isinstance(item.ctx, ast.Store):
                unstable[path].add(item.id)
            for child in ast.iter_child_nodes(item):
                module_bindings(child)

        for item in tree.body:
            module_bindings(item)
        for node in tree.body:
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imports[path][alias.asname or alias.name.split(".")[0]] = (alias.name, None)
            if isinstance(node, ast.ImportFrom):
                package = module_name(path).split(".")[:-1]
                if node.level:
                    package = package[: max(0, len(package) - node.level + 1)]
                else:
                    package = []
                module = ".".join(package + ([node.module] if node.module else []))
                for alias in node.names:
                    imports[path][alias.asname or alias.name] = (module, alias.name)
            if isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
                for descendant in ast.walk(node):
                    if isinstance(descendant, ast.Name) and isinstance(descendant.ctx, ast.Store):
                        unstable[path].add(descendant.id)
        root_id = stable_id(version, path, "module")
        result.scopes.append(
            Scope(
                id=root_id,
                kind="module",
                name=module_name(path),
                location=Location(
                    path=path,
                    start_line=1,
                    end_line=max(1, len(text.splitlines())),
                    start_byte=0,
                    end_byte=len(text.encode()),
                ),
            )
        )

        def visit(
            node: ast.AST, scope_id: str, names: list[str], in_function: bool, local_names: set[str]
        ) -> None:
            if isinstance(node, ast.ClassDef):
                for expression in (
                    node.bases + node.decorator_list + [keyword.value for keyword in node.keywords]
                ):
                    visit(expression, scope_id, names, in_function, local_names)
                class_id = stable_id(version, path, "class", node.lineno)
                result.scopes.append(
                    Scope(
                        id=class_id,
                        kind="class",
                        name=node.name,
                        parent_id=scope_id,
                        location=position(path, text, node),
                    )
                )
                for child in node.body:
                    visit(child, class_id, names + [node.name], in_function, set())
                return
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                for expression in (
                    list(getattr(node, "decorator_list", []))
                    + node.args.defaults
                    + [value for value in node.args.kw_defaults if value is not None]
                ):
                    visit(expression, scope_id, names, in_function, local_names)
                name = getattr(node, "name", "<lambda>")
                qualified = ".".join(names + [name])
                entity_id = stable_id(version, path, qualified, node.lineno, node.col_offset)
                child_scope = stable_id(entity_id, "scope")
                hidden = isinstance(node, ast.Lambda) or in_function
                result.scopes.append(
                    Scope(
                        id=child_scope,
                        kind="lambda" if isinstance(node, ast.Lambda) else "function",
                        name=qualified,
                        parent_id=scope_id,
                        location=position(path, text, node),
                    )
                )
                arguments = ast.unparse(node.args)
                entity = Entity(
                    id=entity_id,
                    name=name,
                    qualified_name=qualified,
                    signature=f"{name}({arguments})",
                    signature_key=digest(ast.dump(node.args, include_attributes=False) +
                                         ast.dump(node.returns, include_attributes=False)
                                         if getattr(node, "returns", None) else
                                         ast.dump(node.args, include_attributes=False)),
                    language="python",
                    location=position(path, text, node),
                    scope_id=child_scope,
                    visible=not hidden,
                    hidden_reason="匿名函数"
                    if isinstance(node, ast.Lambda)
                    else "嵌套局部函数"
                    if in_function
                    else None,
                    implementation_hash=digest(ast.dump(node, include_attributes=False)),
                )
                result.entities.append(entity)
                symbols.setdefault((path, qualified), []).append(entity)
                if getattr(node, "decorator_list", []):
                    decorated.add(entity_id)
                    result.diagnostics.append(
                        Diagnostic(
                            category="unresolved_call",
                            path=path,
                            line=node.lineno,
                            message=f"{qualified} 有装饰器，调用目标可能被改写。",
                        )
                    )
                params = {item.arg for item in node.args.args + node.args.posonlyargs + node.args.kwonlyargs}
                if node.args.vararg:
                    params.add(node.args.vararg.arg)
                if node.args.kwarg:
                    params.add(node.args.kwarg.arg)
                body = node.body if isinstance(node.body, list) else [node.body]
                locals_ = set(params)

                # A lexical pre-pass must not descend into nested execution scopes.
                def collect(item: ast.AST) -> None:
                    if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                        locals_.add(item.name)
                        return
                    if isinstance(
                        item, (ast.Lambda, ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)
                    ):
                        return
                    if isinstance(item, ast.Name) and isinstance(item.ctx, ast.Store):
                        locals_.add(item.id)
                    for child in ast.iter_child_nodes(item):
                        collect(child)

                for child in body:
                    collect(child)
                for child in body:
                    visit(child, child_scope, names + [name], True, locals_)
                return
            if isinstance(node, (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)):
                hidden_scope = stable_id(version, path, "comprehension", node.lineno, node.col_offset)
                result.scopes.append(
                    Scope(
                        id=hidden_scope,
                        kind="comprehension",
                        name="<comprehension>",
                        parent_id=scope_id,
                        location=position(path, text, node),
                    )
                )
                bindings = set(local_names) | {
                    item.id
                    for generator in node.generators
                    for item in ast.walk(generator.target)
                    if isinstance(item, ast.Name)
                }
                visit(node.generators[0].iter, scope_id, names, in_function, local_names)
                for index, generator in enumerate(node.generators):
                    if index:
                        visit(generator.iter, hidden_scope, names, True, bindings)
                    for expression in generator.ifs:
                        visit(expression, hidden_scope, names, True, bindings)
                for field in ("elt", "key", "value"):
                    expression = getattr(node, field, None)
                    if expression is not None:
                        visit(expression, hidden_scope, names, True, bindings)
                return
            if isinstance(node, ast.Call):
                call = Call(
                    id=stable_id(version, path, "call", node.lineno, node.col_offset),
                    caller_scope_id=scope_id,
                    expression=ast.unparse(node.func),
                    location=position(path, text, node),
                )
                result.calls.append(call)
                pending.append((call, path, names, local_names))
            for child in ast.iter_child_nodes(node):
                visit(child, scope_id, names, in_function, local_names)

        for node in tree.body:
            visit(node, root_id, [], False, set())

    for call, path, names, locals_ in pending:
        expression = call.expression
        targets: list[Entity] = []
        parts = expression.split(".")
        if parts[0] in locals_ or parts[0] in unstable[path]:
            call.resolution = "excluded_indirect"
            call.reason = "名称来自参数、局部绑定或重赋值，不能确认静态直接调用。"
        elif len(parts) == 1 and (path, expression) in symbols:
            targets = symbols[(path, expression)]
        elif parts[0] in imports[path]:
            module, member = imports[path][parts[0]]
            target_name = member if member else ".".join(parts[1:])
            for candidate_path in files:
                if module_name(candidate_path) == module:
                    targets += symbols.get((candidate_path, target_name or ""), [])
            if not targets:
                call.resolution = "external"
                call.reason = "导入目标未纳入当前源码范围。"
        elif len(parts) == 1 and hasattr(builtins, expression):
            call.resolution = "external"
            call.reason = "Python 内置函数或类型。"
        else:
            call.reason = "动态属性或目标绑定不足，保留未解析。"
        if len(targets) == 1 and targets[0].id not in decorated:
            call.target_id = targets[0].id
            call.target_name = targets[0].qualified_name
            call.resolution = "confirmed"
            call.reason = "词法绑定指向分析范围内唯一实现，无已知重赋值或装饰器改写。"
        elif targets:
            call.reason = "目标存在多个实现或装饰器改写，不能确认。"
    for entity in result.entities:
        entity.unresolved_count = sum(
            c.caller_scope_id == entity.scope_id and c.resolution in ("unresolved", "excluded_indirect")
            for c in result.calls
        )
    return result


def cpp_analysis(root: Path, files: dict[str, str], version: str) -> Analysis:
    import tree_sitter_c
    import tree_sitter_cpp
    from clang import cindex
    from tree_sitter import Language, Parser

    result = Analysis(tools={"cpp": "tree-sitter/0.25.2 + libclang/18.1.1"})
    native_symbols: dict[str, list[Entity]] = {}
    pending: list[tuple[Call, str, bool]] = []
    by_location: dict[tuple[str, int], Entity] = {}
    function_kinds = {
        cindex.CursorKind.FUNCTION_DECL,
        cindex.CursorKind.CXX_METHOD,
        cindex.CursorKind.CONSTRUCTOR,
        cindex.CursorKind.DESTRUCTOR,
        cindex.CursorKind.FUNCTION_TEMPLATE,
    }
    unsafe_includes = False
    for include_path, include_text in files.items():
        if Path(include_path).suffix not in {".c", ".h", ".cc", ".cpp", ".cxx", ".hpp", ".hh"}:
            continue
        # Clang has no snapshot-only filesystem API. Reject unverified includes before native parsing.
        normalized = include_text.replace("\\\r\n", "").replace("\\\n", "")
        for line in normalized.splitlines():
            directive = re.match(r"\s*#\s*(include(?:_next)?|import)\b(.*)", line)
            if not directive:
                continue
            literal = re.fullmatch(r'\s*["<]([^">]+)[">]\s*(?://.*)?', directive.group(2))
            name = literal.group(1) if literal else ""
            candidates = [(root / Path(include_path).parent / name).resolve(), (root / name).resolve()]
            verified = (
                literal
                and directive.group(1) == "include"
                and all(candidate.is_relative_to(root.resolve()) for candidate in candidates)
            )
            verified = verified and any(
                candidate.relative_to(root.resolve()).as_posix() in files for candidate in candidates
            )
            if not verified:
                unsafe_includes = True
                result.diagnostics.append(
                    Diagnostic(
                        category="missing_dependency",
                        path=include_path,
                        message="包含文件无法在快照内验证，原型保留语法索引并跳过该批 libclang 语义解析。",
                    )
                )
    for path, text in files.items():
        if Path(path).suffix not in {".c", ".h", ".cc", ".cpp", ".cxx", ".hpp", ".hh"}:
            continue
        language = "c" if Path(path).suffix == ".c" else "cpp"
        parser = Parser(Language(tree_sitter_c.language() if language == "c" else tree_sitter_cpp.language()))
        source_bytes = text.encode()
        parsed = parser.parse(source_bytes)

        def node_bytes(node) -> bytes:
            return source_bytes[node.start_byte : node.end_byte]

        def semantic_tree(node):
            if node.type == "comment":
                return None
            if node.type.startswith("preproc_"):
                # Directive line breaks and continuations can alter preprocessing.
                return [node.type, node_bytes(node).decode()]
            children = [value for child in node.children if (value := semantic_tree(child)) is not None]
            return [node.type, children if children else node_bytes(node).decode()]
        result.file_hashes[path] = digest(json.dumps(semantic_tree(parsed.root_node)))
        if any(child.type != "comment" for child in parsed.root_node.named_children):
            result.readable_paths.append(path)
        if parsed.root_node.has_error:
            result.diagnostics.append(
                Diagnostic(category="parse_failed", path=path, message="部分语法无法识别。")
            )

        def syntax(node, parents: list[str], condition: str | None = None):
            if node.type in ("preproc_if", "preproc_ifdef"):
                condition_node = node.child_by_field_name("condition") or node.child_by_field_name("name")
                expression = node_bytes(condition_node).decode() if condition_node else "未知条件"
                if node_bytes(node).startswith(b"#ifndef"):
                    expression = f"!({expression})"
                condition = f"({condition}) && ({expression})" if condition else expression
                result.diagnostics.append(
                    Diagnostic(
                        category="condition_unknown",
                        path=path,
                        line=node.start_point.row + 1,
                        message="条件分支已索引；原型未枚举全部编译配置。",
                    )
                )
            if node.type in ("preproc_else", "preproc_elif"):
                expression = node.child_by_field_name("condition")
                condition = f"!({condition})" + (f" && ({node_bytes(expression).decode()})" if expression else "")
            if node.type in ("class_specifier", "struct_specifier", "namespace_definition"):
                named = node.child_by_field_name("name")
                parents = parents + ([node_bytes(named).decode()] if named else [])
            if node.type == "function_definition":
                declarator = node.child_by_field_name("declarator")
                current = declarator
                while current and current.type not in {
                    "identifier",
                    "field_identifier",
                    "qualified_identifier",
                    "operator_name",
                    "destructor_name",
                }:
                    current = current.child_by_field_name("declarator")
                name = node_bytes(current).decode() if current else "<unresolved>"
                line = node.start_point.row + 1
                location = Location(
                    path=path,
                    start_line=line,
                    end_line=node.end_point.row + 1,
                    start_byte=node.start_byte,
                    end_byte=node.end_byte,
                )
                entity_id = stable_id(version, path, "fn", node.start_byte)
                parts = name.split("::")
                hidden = (
                    parts[-1].startswith("~")
                    or bool(parents and name == parents[-1])
                    or (len(parts) > 1 and parts[-1] == parts[-2])
                )
                signature = node_bytes(declarator).decode() if declarator else name
                entity = Entity(
                    id=entity_id,
                    name=name,
                    qualified_name="::".join(parents + [name]),
                    signature=signature,
                    signature_key=json.dumps(semantic_tree(declarator)) if declarator else signature,
                    language=language,
                    location=location,
                    scope_id=stable_id(entity_id, "scope"),
                    visible=not hidden,
                    hidden_reason="构造或析构" if hidden else None,
                    implementation_hash=digest(json.dumps(semantic_tree(node))),
                    condition=condition,
                )
                if (path, node.start_byte) not in by_location:
                    result.entities.append(entity)
                    result.scopes.append(
                        Scope(
                            id=entity.scope_id, kind="function", name=entity.qualified_name, location=location
                        )
                    )
                    by_location[(path, node.start_byte)] = entity
            for child in node.children:
                syntax(child, parents, condition)

        syntax(parsed.root_node, [])
        if unsafe_includes:
            continue
        try:
            unit = cindex.Index.create().parse(
                str(root / path),
                args=[
                    "-x",
                    "c" if language == "c" else "c++",
                    "-std=c11" if language == "c" else "-std=c++17",
                    "-nostdinc",
                    "-nostdinc++",
                    "-I" + str(root),
                ],
                options=cindex.TranslationUnit.PARSE_DETAILED_PROCESSING_RECORD,
            )
        except cindex.TranslationUnitLoadError:
            result.diagnostics.append(
                Diagnostic(category="parse_failed", path=path, message="libclang 无法解析该文件。")
            )
            continue
        for diagnostic in unit.diagnostics:
            if diagnostic.severity >= cindex.Diagnostic.Error:
                result.diagnostics.append(
                    Diagnostic(
                        category="missing_dependency",
                        path=path,
                        line=diagnostic.location.line or None,
                        message=diagnostic.spelling,
                    )
                )

        def local_path(cursor):
            if not cursor.location.file:
                return None
            try:
                return Path(cursor.location.file.name).resolve().relative_to(root.resolve()).as_posix()
            except ValueError:
                return None

        def native(cursor, active_scope: str | None = None):
            cursor_path = local_path(cursor)
            if cursor_path and cursor_path not in files:
                return
            if cursor.kind in function_kinds and cursor.is_definition() and cursor_path:
                entity = by_location.get((cursor_path, cursor.extent.start.offset))
                if not entity:
                    matches = [
                        candidate
                        for candidate in result.entities
                        if candidate.location.path == cursor_path
                        and candidate.location.start_byte
                        <= cursor.location.offset
                        < candidate.location.end_byte
                        and candidate.name.split("::")[-1] == cursor.spelling
                    ]
                    entity = matches[0] if len(matches) == 1 else None
                if entity:
                    active_scope = entity.scope_id
                    usr = cursor.get_usr()
                    if usr:
                        entries = native_symbols.setdefault(usr, [])
                        if all(item.id != entity.id for item in entries):
                            entries.append(entity)
            if cursor.kind == cindex.CursorKind.LAMBDA_EXPR and cursor_path:
                active_scope = stable_id(
                    version, cursor_path, "lambda", cursor.location.line, cursor.location.column
                )
                if all(scope.id != active_scope for scope in result.scopes):
                    result.scopes.append(
                        Scope(
                            id=active_scope,
                            kind="lambda",
                            name="<lambda>",
                            location=Location(
                                path=cursor_path,
                                start_line=cursor.extent.start.line,
                                end_line=cursor.extent.end.line,
                                start_byte=cursor.extent.start.offset,
                                end_byte=cursor.extent.end.offset,
                            ),
                        )
                    )
            if cursor.kind == cindex.CursorKind.CALL_EXPR and active_scope and cursor_path:
                target = cursor.referenced
                usr = target.get_usr() if target else ""
                indirect = not target or target.kind not in function_kinds
                if target and target.kind == cindex.CursorKind.CXX_METHOD and target.is_virtual_method():
                    indirect = True
                call = Call(
                    id=stable_id(version, cursor_path, "call", cursor.extent.start.offset),
                    caller_scope_id=active_scope,
                    expression=cursor.spelling,
                    location=Location(
                        path=cursor_path,
                        start_line=cursor.extent.start.line,
                        end_line=cursor.extent.end.line,
                        start_byte=cursor.extent.start.offset,
                        end_byte=cursor.extent.end.offset,
                    ),
                )
                if all(item.id != call.id for item in result.calls):
                    result.calls.append(call)
                    pending.append((call, usr, indirect))
            for child in cursor.get_children():
                native(child, active_scope)

        module_scope = stable_id(version, path, "module")
        result.scopes.append(
            Scope(
                id=module_scope,
                kind="module",
                name=path,
                location=Location(
                    path=path,
                    start_line=1,
                    end_line=max(1, len(text.splitlines())),
                    start_byte=0,
                    end_byte=len(text.encode()),
                ),
            )
        )
        native(unit.cursor, module_scope)
    for call, usr, indirect in pending:
        targets = native_symbols.get(usr, [])
        if indirect:
            call.resolution = "excluded_indirect"
            call.reason = "函数指针、回调或虚分派不进入直接调用图。"
        elif len(targets) == 1 and not targets[0].condition:
            call.target_id = targets[0].id
            call.target_name = targets[0].qualified_name
            call.resolution = "confirmed"
            call.reason = "libclang 符号解析对应唯一实现。"
        elif not targets:
            call.resolution = "external"
            call.reason = "目标没有当前范围内实现。"
        else:
            call.reason = "条件配置或多个实现未消除歧义。"
    return result


def typescript_analysis(root: Path, files: dict[str, str], version: str) -> Analysis:
    paths = [path for path in files if Path(path).suffix in {".ts", ".tsx"} and not path.endswith(".d.ts")]
    if not paths:
        return Analysis()
    try:
        completed = subprocess.run(
            ["node", str(REPOSITORY / "analyzers/typescript/analyze.mjs")],
            input=json.dumps({"root": str(root), "paths": paths, "version": version}),
            text=True,
            encoding="utf-8",
            capture_output=True,
            timeout=120,
            cwd=REPOSITORY,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        if completed.returncode:
            raise ValueError("TypeScript 解析进程失败")
        return Analysis.model_validate_json(completed.stdout)
    except (OSError, subprocess.TimeoutExpired, ValueError) as error:
        return Analysis(diagnostics=[Diagnostic(category="parse_failed", message=str(error))])


def analyze(root: Path, files: dict[str, str], version: str) -> Analysis:
    results = [
        python_analysis(files, version),
        cpp_analysis(root, files, version),
        typescript_analysis(root, files, version),
    ]
    combined = Analysis()
    for result in results:
        combined.entities += result.entities
        combined.scopes += result.scopes
        combined.calls += result.calls
        combined.diagnostics += result.diagnostics
        combined.tools.update(result.tools)
        combined.file_hashes.update(result.file_hashes)
        combined.readable_paths.extend(result.readable_paths)
    for entity in combined.entities:
        entity.unresolved_count = sum(
            call.caller_scope_id == entity.scope_id and call.resolution in {"unresolved", "excluded_indirect"}
            for call in combined.calls
        )
    return combined
