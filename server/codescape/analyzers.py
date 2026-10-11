from __future__ import annotations

import ast
import builtins
import hashlib
import json
import re
import subprocess
import uuid
from functools import lru_cache
from pathlib import Path

from .cpp_environment import compiler_configurations, snapshot_overlay, trusted_headers, verify_includes
from .cpp_syntax import recover_parse, recovery_macros
from .models import Analysis, Call, Diagnostic, Entity, Location, Scope
from .runtime import executable, resource_root

REPOSITORY = resource_root()


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
    result = Analysis(tools={"python": "ast/3.12; conservative-bindings/1; type-declarations/1"})
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
        declaration_shadows = {node.name for node in tree.body
                               if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))}
        mutated_imports = {node.value.id for node in ast.walk(tree)
                           if isinstance(node, ast.Attribute) and isinstance(node.ctx, (ast.Store, ast.Del))
                           and isinstance(node.value, ast.Name)}

        def placeholder_body(node):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                return False
            body = node.body[1:] if node.body and isinstance(node.body[0], ast.Expr) and isinstance(
                node.body[0].value, ast.Constant) and isinstance(node.body[0].value.value, str) else node.body
            return bool(body) and all(isinstance(item, ast.Pass) or isinstance(item, ast.Expr)
                and isinstance(item.value, ast.Constant) and item.value.value is Ellipsis for item in body)

        def imported_name(expression, shadowed):
            root = expression.value if isinstance(expression, ast.Attribute) else expression
            if not isinstance(root, ast.Name) or root.id in unstable[path] | declaration_shadows | mutated_imports | shadowed:
                return None, None
            module, name = imports[path].get(root.id, (None, None))
            if isinstance(expression, ast.Attribute):
                return (module, expression.attr) if name is None else (None, None)
            return module, name

        def declaration_reason(node, shadowed, protocol):
            if not placeholder_body(node):
                return None
            for decorator in node.decorator_list:
                module, name = imported_name(decorator, shadowed)
                if module in {'typing', 'typing_extensions'} and name == 'overload':
                    return '类型重载声明'
                if module == 'abc' and name == 'abstractmethod':
                    return '抽象方法声明'
            return '接口方法声明' if protocol else None
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
            node: ast.AST, scope_id: str, names: list[str], in_function: bool, local_names: set[str],
            protocol: bool = False,
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
                class_bindings = {child.name for child in node.body
                                  if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))}
                for child in node.body:
                    if isinstance(child, (ast.Assign, ast.AnnAssign, ast.Import, ast.ImportFrom)):
                        class_bindings.update(item.id for item in ast.walk(child)
                                              if isinstance(item, ast.Name) and isinstance(item.ctx, ast.Store))
                        if isinstance(child, (ast.Import, ast.ImportFrom)):
                            class_bindings.update(alias.asname or alias.name.split('.')[0] for alias in child.names)
                protocol_class = any(imported_name(base, local_names) in {
                    ('typing', 'Protocol'), ('typing_extensions', 'Protocol')} for base in node.bases)
                for child in node.body:
                    visit(child, class_id, names + [node.name], in_function, class_bindings, protocol_class)
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
                declaration = declaration_reason(node, local_names, protocol)
                hidden = isinstance(node, ast.Lambda) or in_function or declaration
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
                    else declaration
                    if declaration
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
                visit(child, scope_id, names, in_function, local_names, protocol)

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
            target_name = ".".join([member, *parts[1:]]) if member else ".".join(parts[1:])
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


def cpp_analysis(root: Path, files: dict[str, str], version: str, source_root: str | None = None, syntax_only: bool = False) -> Analysis:
    if not any(Path(path).suffix in {".c", ".h", ".cc", ".cpp", ".cxx", ".hpp", ".hh"} for path in files):
        return Analysis()
    root = root.resolve()
    header_directories, header_files, header_error = trusted_headers()
    with snapshot_overlay(root, files, header_directories, header_files) as overlay:
        return _cpp_analysis(root, files, version, source_root, header_directories, header_files, header_error, overlay, syntax_only)


def _cpp_analysis(root, files, version, source_root, header_directories, header_files, header_error, overlay, syntax_only=False):
    import tree_sitter_c
    import tree_sitter_cpp
    from clang import cindex
    from tree_sitter import Language, Parser

    result = Analysis(tools={"cpp": "tree-sitter/0.25.2 + libclang/18.1.1 + snapshot-config/7"})
    if syntax_only:
        result.tools['cpp'] += ' + syntax-fallback/1'
    if not any(Path(path).suffix in {".c", ".h", ".cc", ".cpp", ".cxx", ".hpp", ".hh"} for path in files):
        return result
    configurations, notes = compiler_configurations(root, files, source_root)
    for path, message in notes:
        result.diagnostics.append(Diagnostic(category="configuration", path=path, message=message))
    if header_error:
        result.diagnostics.append(Diagnostic(category="missing_dependency", message=header_error))
    native_symbols: dict[str, list[Entity]] = {}
    failed_config_paths: set[str] = set(configurations.incomplete)
    pending: list[tuple[Call, str, bool, str | None, bool]] = []
    calls_by_id: dict[str, Call] = {}
    unsafe_macro_calls: set[str] = set()
    ambiguous_names: dict[str, Diagnostic] = {}
    native_names: dict[str, set[str]] = {}
    syntactic_bindings: dict[str, set[str]] = {}
    by_location: dict[tuple[str, int], Entity] = {}
    function_kinds = {
        cindex.CursorKind.FUNCTION_DECL,
        cindex.CursorKind.CXX_METHOD,
        cindex.CursorKind.CONSTRUCTOR,
        cindex.CursorKind.DESTRUCTOR,
        cindex.CursorKind.FUNCTION_TEMPLATE,
    }
    syntax_macros = recovery_macros(files)
    for path, text in files.items():
        if Path(path).suffix not in {".c", ".h", ".cc", ".cpp", ".cxx", ".hpp", ".hh"}:
            continue
        language = "c" if Path(path).suffix == ".c" else "cpp"
        parser = Parser(Language(tree_sitter_c.language() if language == "c" else tree_sitter_cpp.language()))
        source_bytes = text.encode()
        parsed = parser.parse(source_bytes)
        original_parsed = parsed
        recovered = namespace_recovered = False
        if language == 'cpp':
            parsed, recovered, namespace_recovered = recover_parse(parser, parsed, source_bytes, syntax_macros)
        if recovered:
            result.diagnostics.append(Diagnostic(category='syntax_recovery', path=path,
                message='仅在内存语法视图忽略已知导出注解和命名空间包装宏以恢复声明；'
                        '源码位置与原字节不变，宏命名空间名称待原生定义确认，不以此证明调用绑定。'))
        module_scope = stable_id(version, path, "module")
        result.scopes.append(Scope(id=module_scope, kind="module", name=path,
            location=Location(path=path,start_line=1,end_line=max(1,len(text.splitlines())),
                start_byte=0,end_byte=len(source_bytes))))
        guard_range = None
        meaningful = [child for child in parsed.root_node.named_children if child.type != 'comment']
        if Path(path).suffix in {'.h','.hh','.hpp'} and len(meaningful) == 1:
            guard = meaningful[0]
            name = guard.child_by_field_name('name') if guard.type == 'preproc_ifdef' else None
            if name and re.match(rb'#\s*ifndef\b', source_bytes[guard.start_byte:guard.end_byte].lstrip()):
                definitions = [child for child in guard.named_children if child.type == 'preproc_def']
                macro = source_bytes[name.start_byte:name.end_byte]
                first = definitions[0].child_by_field_name('name') if definitions else None
                if first and source_bytes[first.start_byte:first.end_byte] == macro and not guard.child_by_field_name('alternative'):
                    guard_range = (guard.start_byte, guard.end_byte)

        def node_bytes(node) -> bytes:
            return source_bytes[node.start_byte : node.end_byte]

        def declared_identifier(node):
            while node and node.type not in {'identifier','field_identifier'}:
                node = node.child_by_field_name('declarator') or next(
                    (child for child in node.named_children if 'declarator' in child.type),None)
            return node_bytes(node).decode() if node else None

        def parameters(node, scope):
            if node.type == 'parameter_declaration':
                name = declared_identifier(node.child_by_field_name('declarator'))
                if name:
                    syntactic_bindings.setdefault(scope,set()).add(name)
            for child in node.named_children:
                parameters(child,scope)

        def semantic_tree(node):
            if node.type == "comment":
                return None
            if node.type.startswith("preproc_"):
                # Directive line breaks and continuations can alter preprocessing.
                return [node.type, node_bytes(node).decode()]
            children = [value for child in node.children if (value := semantic_tree(child)) is not None]
            return [node.type, children if children else node_bytes(node).decode()]
        result.file_hashes[path] = digest(json.dumps(semantic_tree(original_parsed.root_node)))
        if any(child.type != "comment" for child in parsed.root_node.named_children):
            result.readable_paths.append(path)
        if original_parsed.root_node.has_error:
            result.diagnostics.append(
                Diagnostic(category="parse_failed", path=path, message="部分语法无法识别。")
            )

        def combined(outer, inner):
            return f'({outer}) && ({inner})' if outer else inner

        def alternative(node, parents, outer, previous, scope):
            excluded = ' && '.join(f'!({value})' for value in previous)
            expression_node = node.child_by_field_name('condition')
            expression = node_bytes(expression_node).decode() if expression_node else None
            current = combined(outer, combined(excluded, expression) if expression else excluded)
            following = node.child_by_field_name('alternative')
            for child in node.children:
                if following and child == following:
                    alternative(child, parents, outer, previous + ([expression] if expression else []), scope)
                else:
                    syntax(child, parents, current, scope)

        def syntax(node, parents: list[str], condition: str | None = None, scope: str = module_scope):
            if node.type in ("preproc_if", "preproc_ifdef"):
                condition_node = node.child_by_field_name("condition") or node.child_by_field_name("name")
                expression = node_bytes(condition_node).decode() if condition_node else "未知条件"
                if node.type == 'preproc_ifdef':
                    expression = f'defined({expression})'
                if re.match(rb'#\s*ifndef\b', node_bytes(node).lstrip()):
                    expression = f"!({expression})"
                guard = (node.start_byte,node.end_byte) == guard_range
                active = condition if guard else combined(condition, expression)
                if not guard:
                    result.diagnostics.append(
                    Diagnostic(
                        category="condition_unknown",
                        path=path,
                        line=node.start_point.row + 1,
                        message="条件分支已索引；语义解析仅覆盖已提供的编译配置与保守默认配置。",
                    )
                )
                following = node.child_by_field_name('alternative')
                for child in node.children:
                    if following and child == following:
                        alternative(child, parents, condition, [expression], scope)
                    else:
                        syntax(child, parents, active, scope)
                return
            if node.type in ("class_specifier", "struct_specifier", "namespace_definition"):
                named = node.child_by_field_name("name")
                parents = parents + ([node_bytes(named).decode()] if named else [])
            if node.type == "function_definition":
                declarator = node.child_by_field_name("declarator")
                probe = declarator
                while probe and probe.type != 'function_declarator':
                    probe = probe.child_by_field_name('declarator')
                if probe is None:
                    result.diagnostics.append(Diagnostic(category='parse_failed',path=path,
                        line=node.start_point.row+1,
                        message='语法恢复节点没有函数声明结构，未当作函数或调用所属作用域。'))
                    for child in node.children:
                        syntax(child, parents, condition, scope)
                    return
                current = declarator
                while current and current.type not in {
                    "identifier",
                    "field_identifier",
                    "qualified_identifier",
                    "operator_name",
                    "destructor_name",
                    "template_function",
                }:
                    current = current.child_by_field_name("declarator")
                name = node_bytes(current).decode() if current else "<unresolved>"
                implementation = node.parent if node.parent and node.parent.type == 'template_declaration' else node
                location = Location(
                    path=path,
                    start_line=implementation.start_point.row + 1,
                    end_line=node.end_point.row + 1,
                    start_byte=implementation.start_byte,
                    end_byte=node.end_byte,
                )
                entity_id = stable_id(version, path, "fn", node.start_byte)
                ambiguous_name = bool(current and current.has_error)
                if ambiguous_name:
                    leaf = current
                    while leaf and leaf.type == 'qualified_identifier':
                        leaf = leaf.child_by_field_name('name')
                    name = node_bytes(leaf).decode() if leaf and not leaf.has_error else '<unresolved>'
                    note = Diagnostic(category='symbol_name_unresolved',path=path,line=location.start_line,
                        message='声明含宏或语法歧义，暂仅展示末级名称；限定名称待可靠原生定义确认，源码签名保持原样。')
                    ambiguous_names[entity_id] = note
                    result.diagnostics.append(note)
                parts = name.split("::")
                hidden = (
                    parts[-1].startswith("~")
                    or bool(parents and name == parents[-1])
                    or (len(parts) > 1 and parts[-1] == parts[-2])
                    or name == 'PYBIND11_MODULE'
                )
                signature = node_bytes(declarator).decode() if declarator else name
                entity = Entity(
                    id=entity_id,
                    name=name,
                    qualified_name="::".join(parents + (['<声明名称待确认>'] if ambiguous_name else []) + [name]),
                    signature=signature,
                    signature_key=json.dumps(semantic_tree(declarator)) if declarator else signature,
                    language=language,
                    location=location,
                    scope_id=stable_id(entity_id, "scope"),
                    visible=not hidden,
                    hidden_reason=("宏注册入口" if name == 'PYBIND11_MODULE' else "构造或析构") if hidden else None,
                    implementation_hash=digest(json.dumps(semantic_tree(implementation))),
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
                body = node.child_by_field_name('body')
                if declarator:
                    parameters(declarator,entity.scope_id)
                for child in node.children:
                    syntax(child, parents, condition, entity.scope_id if child == body or child.type == 'field_initializer_list' else scope)
                return
            if node.type == 'lambda_expression':
                scope = stable_id(version, path, "lambda", node.start_point.row + 1, node.start_point.column + 1)
                result.scopes.append(Scope(id=scope, kind='lambda', name='<lambda>',
                    location=Location(path=path,start_line=node.start_point.row+1,end_line=node.end_point.row+1,
                        start_byte=node.start_byte,end_byte=node.end_byte)))
                parameters(node.child_by_field_name('declarator') or node,scope)
            if node.type == 'init_declarator':
                name = declared_identifier(node.child_by_field_name('declarator'))
                if name:
                    syntactic_bindings.setdefault(scope,set()).add(name)
            if node.type == 'call_expression':
                function = node.child_by_field_name('function')
                call = Call(id=stable_id(version,path,'call',node.start_byte), caller_scope_id=scope,
                    expression=node_bytes(function).decode() if function else '<unresolved>',
                    location=Location(path=path,start_line=node.start_point.row+1,end_line=node.end_point.row+1,
                        start_byte=node.start_byte,end_byte=node.end_byte))
                result.calls.append(call)
                calls_by_id[call.id] = call
            for child in node.children:
                syntax(child, parents, condition, scope)

        syntax(parsed.root_node, ['<宏命名空间待确认>'] if namespace_recovered else [])
    entities_by_path = {}
    for entity in result.entities:
        entities_by_path.setdefault(entity.location.path,[]).append(entity)
    # All entities exist before any translation unit/configuration is resolved.
    def parse_unit(path, text, configured_arguments, includes):
        language = "c" if Path(path).suffix == ".c" else "cpp"
        visited, pending_paths, unsafe = set(), [path], False
        while pending_paths:
            checked = pending_paths.pop()
            if checked in visited:
                continue
            visited.add(checked)
            dependencies, problems = verify_includes(root, files, checked, includes, header_directories, header_files)
            pending_paths.extend(dependencies)
            for message in problems:
                if message.startswith("包含文件 "):
                    # An inactive platform branch may mention an unavailable header.
                    # The closed VFS prevents reads; let Clang report only active missing headers.
                    continue
                unsafe = True
                result.diagnostics.append(Diagnostic(category="missing_dependency", path=checked, message=message))
        if unsafe:
            if path in configurations:
                failed_config_paths.update(visited)
            return
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
                    "--target=x86_64-w64-windows-gnu",
                    *[argument for directory in header_directories for argument in ("-isystem", str(directory))],
                    *configured_arguments,
                    "-ivfsoverlay", str(overlay),
                ],
                unsaved_files=[(str(root / included), value) for included, value in files.items()],
                options=cindex.TranslationUnit.PARSE_DETAILED_PROCESSING_RECORD,
            )
        except cindex.TranslationUnitLoadError:
            if path in configurations:
                failed_config_paths.update(visited)
            result.diagnostics.append(
                Diagnostic(category="parse_failed", path=path, message="libclang 无法解析该文件。")
            )
            return
        native_errors = False
        for diagnostic in unit.diagnostics:
            if diagnostic.severity >= cindex.Diagnostic.Error:
                native_errors = True
                result.diagnostics.append(
                    Diagnostic(
                        category="missing_dependency",
                        path=path,
                        line=diagnostic.location.line or None,
                        message=diagnostic.spelling,
                    )
                )
        if native_errors:
            if path in configurations:
                failed_config_paths.update(visited)
            return  # Recovery ASTs cannot establish unique targets in an incomplete unit.

        @lru_cache(maxsize=4096)
        def relative_native_path(filename):
            try:
                return Path(filename).resolve().relative_to(root).as_posix()
            except ValueError:
                return None

        def local_path(cursor):
            filename = cursor.location.file
            return relative_native_path(filename.name) if filename else None

        macros = {(local_path(cursor), cursor.extent.start.offset):cursor
            for cursor in unit.cursor.get_children()
            if cursor.kind == cindex.CursorKind.MACRO_INSTANTIATION and local_path(cursor) in files}

        def macro_spelling(macro, target_name):
            definition = macro.referenced
            definition_path = local_path(definition) if definition else None
            if definition_path not in files:
                return None
            tokens = list(definition.get_tokens())
            matches = [index for index,token in enumerate(tokens[:-1])
                if token.spelling == target_name and tokens[index+1].spelling == '(']
            if len(matches) != 1:
                return None
            start = matches[0]
            depth = 0
            for token in tokens[start+1:]:
                depth += int(token.spelling == '(') - int(token.spelling == ')')
                if depth == 0:
                    return Location(path=definition_path,start_line=tokens[start].extent.start.line,
                        end_line=token.extent.end.line,start_byte=tokens[start].extent.start.offset,
                        end_byte=token.extent.end.offset)
            return None

        def entity_for_cursor(cursor):
            cursor_path = local_path(cursor)
            if not cursor_path:
                return None
            entity = by_location.get((cursor_path, cursor.extent.start.offset))
            if entity:
                return entity
            matches = [candidate for candidate in entities_by_path.get(cursor_path,[])
                if candidate.location.path == cursor_path
                and candidate.location.start_byte <= cursor.location.offset < candidate.location.end_byte
                and (candidate.name.split('::')[-1] == cursor.spelling
                    or candidate.name.split('::')[-1].startswith(cursor.spelling + '<'))]
            return matches[0] if len(matches) == 1 else None

        def native(cursor, active_scope: str | None = None):
            cursor_path = local_path(cursor)
            if cursor.location.file and not cursor_path:
                return  # Trusted declarations are not project entities, calls or model input.
            if cursor_path and cursor_path not in files:
                return
            if cursor.kind in function_kinds and cursor.is_definition() and cursor_path:
                entity = entity_for_cursor(cursor)
                if entity:
                    active_scope = entity.scope_id
                    if cursor.spelling:
                        leaf = entity.name.split('::')[-1]
                        names = [leaf if leaf.startswith(cursor.spelling+'<') else cursor.spelling]
                        parent = cursor.semantic_parent
                        while parent and parent.kind != cindex.CursorKind.TRANSLATION_UNIT:
                            if parent.spelling:
                                names.insert(0,parent.displayname if parent.kind in {
                                    cindex.CursorKind.CLASS_DECL,cindex.CursorKind.STRUCT_DECL,
                                    cindex.CursorKind.CLASS_TEMPLATE,cindex.CursorKind.CLASS_TEMPLATE_PARTIAL_SPECIALIZATION
                                } else parent.spelling)
                            parent = parent.semantic_parent
                        native_names.setdefault(entity.id,set()).add('::'.join(names))
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
                definition = target.get_definition() if target else None
                defined_entity = entity_for_cursor(definition) if definition else None
                if target and not defined_entity:
                    template = cindex.conf.lib.clang_getSpecializedCursorTemplate(target)
                    if template:
                        defined_entity = entity_for_cursor(template.get_definition() or template)
                        usr = template.get_usr()
                indirect = not target or target.kind not in function_kinds
                if target and target.kind == cindex.CursorKind.CXX_METHOD and target.is_virtual_method():
                    callee = files[cursor_path].encode()[cursor.extent.start.offset:cursor.extent.end.offset].decode().split('(',1)[0]
                    indirect = not bool(re.search(r'::\s*'+re.escape(target.spelling)+r'\s*$',callee))
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
                existing = calls_by_id.get(call.id)
                if existing is None:
                    result.calls.append(call)
                    calls_by_id[call.id] = call
                else:
                    if existing.caller_scope_id != call.caller_scope_id:
                        indirect = True
                    call = existing
                macro = macros.get((cursor_path,cursor.extent.start.offset))
                if macro:
                    call.expansion_location = call.location.model_copy()
                    call.spelling_location = macro_spelling(macro,cursor.spelling)
                    if call.spelling_location is None:
                        unsafe_macro_calls.add(call.id)
                        result.diagnostics.append(Diagnostic(category='macro_location',path=cursor_path,
                            line=call.location.start_line,message='宏展开中的调用拼写不能唯一定位，不确认调用目标。'))
                pending.append((call, usr, indirect, defined_entity.id if defined_entity else None, path in configurations))
            for child in cursor.get_children():
                native(child, active_scope)

        module_scope = stable_id(version, path, "module")
        if not any(scope.id == module_scope for scope in result.scopes):
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

    default_includes = [root] + ([root / 'include'] if any(relative.startswith('include/') for relative in files) else [])
    default_arguments = ['-I' + str(directory) for directory in default_includes[1:]]
    if len(default_includes) > 1 and any(path not in configurations for path in files if Path(path).suffix in {'.c','.cpp','.cc','.cxx','.h','.hpp','.hh'}):
        result.diagnostics.append(Diagnostic(category='configuration',message=
            '未提供编译配置的单元使用保守默认配置，包含目录仅为快照根及已有的 include 目录；不代表覆盖真实构建参数。'))
    for path, text in files.items():
        if Path(path).suffix not in {".c", ".h", ".cc", ".cpp", ".cxx", ".hpp", ".hh"}:
            continue
        choices = configurations.get(path, [(default_arguments, default_includes, 0)])
        if syntax_only:
            continue
        if len(choices) > 1:
            result.diagnostics.append(Diagnostic(category="configuration", path=path,
                message=f"已分别解析 {len(choices)} 个快照内编译配置；相同调用位置的目标冲突不进入确定图。"))
        for configured_arguments, includes, _ in choices:
            parse_unit(path, text, configured_arguments, includes)
    scopes_by_id = {scope.id:scope for scope in result.scopes}
    for entity in result.entities:
        names = native_names.get(entity.id,set())
        if len(names) == 1 and entity.location.path not in failed_config_paths:
            entity.qualified_name = next(iter(names))
            scopes_by_id[entity.scope_id].name = entity.qualified_name
            if entity.id in ambiguous_names:
                entity.name = entity.qualified_name
                result.diagnostics.remove(ambiguous_names[entity.id])
    outcomes = {}
    entities = {entity.id:entity for entity in result.entities}
    callers = {entity.scope_id:entity for entity in result.entities}
    for call, usr, indirect, definition_id, configured in pending:
        targets = [entities[definition_id]] if definition_id else native_symbols.get(usr, [])
        caller = callers.get(call.caller_scope_id)
        target = targets[0] if len(targets) == 1 else None
        if indirect:
            outcome = ('excluded_indirect',None)
        elif target and (not target.condition or configured or (caller and caller.condition == target.condition)):
            outcome = ('confirmed',target.id)
        elif not targets:
            outcome = ('external',None)
        else:
            outcome = ('unresolved',None)
        outcomes.setdefault(call.id,set()).add(outcome)
    for call in result.calls:
        choices = outcomes.get(call.id,set())
        if not choices:
            if call.expression in syntactic_bindings.get(call.caller_scope_id,set()):
                call.resolution = 'excluded_indirect'
                call.reason = '参数或变量遮蔽了调用名称；语义缺口下不将间接调用归到同名函数。'
            else:
                call.reason = "语义解析未覆盖此调用位置；保留真实语法作用域和位置，不按名称猜测目标。"
            result.diagnostics.append(Diagnostic(category='unresolved_call',path=call.location.path,
                line=call.location.start_line,message=call.reason))
            continue
        if call.id in unsafe_macro_calls:
            call.reason = '宏展开调用的源码拼写不能唯一定位；保留未解析，不绘制确定边。'
            continue
        if len(choices) != 1 or call.location.path in failed_config_paths:
            call.reason = ("编译配置未覆盖或解析失败，不能确认该位置在全部配置中的目标。"
                if call.location.path in failed_config_paths else "不同编译配置或同位置宏展开存在目标冲突，不生成确定调用。")
            result.diagnostics.append(Diagnostic(category='condition_unknown', path=call.location.path,
                line=call.location.start_line,message=call.reason))
            continue
        resolution, target_id = next(iter(choices))
        call.resolution = resolution
        call.target_id = target_id
        call.target_name = entities[target_id].qualified_name if target_id else None
        call.reason = {'confirmed':'libclang 在已解析配置中对应同一具体实现。',
            'external':'目标没有当前范围内实现。','excluded_indirect':'函数指针、回调或虚分派不进入直接调用图。',
            'unresolved':'条件配置或多个实现未消除歧义。'}[resolution]
    return result


def typescript_analysis(root: Path, files: dict[str, str], version: str) -> Analysis:
    paths = [path for path in files if Path(path).suffix in {".ts", ".tsx"} and not path.endswith(".d.ts")]
    if not paths:
        return Analysis()
    try:
        completed = subprocess.run(
            [executable("runtime/node/node.exe", "node"), str(REPOSITORY / "analyzers/typescript/analyze.mjs")],
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


def analyze(root: Path, files: dict[str, str], version: str, source_root: str | None = None, syntax_only: bool = False) -> Analysis:
    results = [
        python_analysis(files, version),
        cpp_analysis(root, files, version, source_root, syntax_only),
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
    from .bridges import pybind_interfaces
    combined.calls.extend(pybind_interfaces(files,combined))
    families = {'python' if Path(path).suffix == '.py' else 'typescript' if Path(path).suffix in {'.ts','.tsx'}
        else 'native' for path in combined.readable_paths}
    if len(families) > 1:
        combined.diagnostics.append(Diagnostic(category='interface_boundary',message=
            '跨语言接口当前仅确认受限 pybind11 显式注册契约；其他机制、动态加载及运行时行为未确认，不按同名或字符串猜测关联。'))
    return combined
