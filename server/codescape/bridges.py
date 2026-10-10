"""有双侧源码依据的接口契约；不将跨语言接口冒充直接函数调用。"""
from __future__ import annotations

import ast
import re
from collections import defaultdict
from pathlib import PurePosixPath

from .cpp_environment import preprocessing_lines
from .models import Call, Location


def pybind_interfaces(files, analysis):
    import tree_sitter_cpp
    from tree_sitter import Language, Parser

    native_files = {path:text for path,text in files.items() if PurePosixPath(path).suffix in {'.cpp','.cc','.cxx','.hpp','.hh','.h'}}
    if not native_files or not any(path.endswith('.py') for path in files):
        return []
    # A project-defined replacement macro/header is outside this recognized API contract.
    if any(re.match(r'\s*(?:#|%:|\?\?=)\s*(?:define|undef)\s+PYBIND11_MODULE\b',line)
           for text in native_files.values() for line in preprocessing_lines(text)) or any(path.endswith('pybind11/pybind11.h') for path in files):
        return []
    registrations = defaultdict(list)
    module_counts = defaultdict(int)
    parser = Parser(Language(tree_sitter_cpp.language()))
    targets = [entity for entity in analysis.entities if entity.visible and entity.language == 'cpp']
    for path,text in native_files.items():
        data = text.encode()
        root = parser.parse(data).root_node
        def spelling(node):
            return data[node.start_byte:node.end_byte].decode() if node else ''
        includes = [spelling(child.child_by_field_name('path')) for child in root.named_children if child.type == 'preproc_include']
        if '<pybind11/pybind11.h>' not in includes:
            continue
        # Top-level registration only: conditional/local/macro-generated bindings are not guessed.
        for node in root.named_children:
            if node.type != 'function_definition':
                continue
            declarator = node.child_by_field_name('declarator')
            if not declarator or spelling(declarator.child_by_field_name('declarator')) != 'PYBIND11_MODULE':
                continue
            params = declarator.child_by_field_name('parameters')
            names = [spelling(param).strip() for param in params.named_children] if params else []
            if len(names) < 2 or not all(re.fullmatch(r'[A-Za-z_]\w*',name) for name in names[:2]):
                continue
            module_name, receiver = names[:2]
            module_counts[module_name] += 1
            body = node.child_by_field_name('body')
            if not body:
                continue
            def mutated_receiver(current):
                if current.type == 'assignment_expression':
                    left = spelling(current.child_by_field_name('left')).replace(' ','')
                    if left != receiver+'.doc()':
                        return True
                return any(mutated_receiver(child) for child in current.named_children)
            if mutated_receiver(body):
                continue
            def recognized_statement(statement):
                if statement.type == 'comment':
                    return True
                if statement.type != 'expression_statement' or len(statement.named_children) != 1:
                    return False
                expression = statement.named_children[0]
                if expression.type == 'assignment_expression':
                    return spelling(expression.child_by_field_name('left')).replace(' ','') == receiver+'.doc()'
                if expression.type != 'call_expression':
                    return False
                return spelling(expression.child_by_field_name('function')).replace(' ','') == receiver+'.def'
            if not all(recognized_statement(statement) for statement in body.named_children):
                continue
            registration = Location(path=path,start_line=node.start_point.row+1,end_line=node.end_point.row+1,
                start_byte=node.start_byte,end_byte=node.end_byte)
            for statement in body.named_children:
                if statement.type != 'expression_statement':
                    continue
                expression = next((child for child in statement.named_children if child.type == 'call_expression'),None)
                if not expression:
                    continue
                callee = expression.child_by_field_name('function')
                if not callee or callee.type != 'field_expression' or spelling(callee.child_by_field_name('argument')) != receiver or spelling(callee.child_by_field_name('field')) != 'def':
                    continue
                arguments = expression.child_by_field_name('arguments')
                values = arguments.named_children if arguments else []
                if len(values) < 2 or values[0].type != 'string_literal' or values[1].type != 'pointer_expression':
                    continue
                exported = spelling(values[0])
                pointer = spelling(values[1]).strip()
                if not re.fullmatch(r'"[A-Za-z_]\w*"',exported) or not re.fullmatch(r'&\s*(?:[A-Za-z_]\w*::)*[A-Za-z_]\w*',pointer):
                    continue
                name = re.sub(r'\s+','',pointer)[1:]
                candidates = [entity for entity in targets if entity.qualified_name == name]
                if len(candidates) != 1 or candidates[0].condition:
                    continue
                location = Location(path=path,start_line=expression.start_point.row+1,end_line=expression.end_point.row+1,
                    start_byte=expression.start_byte,end_byte=expression.end_byte)
                registrations[(module_name,exported[1:-1])].append((candidates[0],location,registration))
    confirmed = []
    for path,text in files.items():
        if not path.endswith('.py'):
            continue
        try:
            tree = ast.parse(text)
        except SyntaxError:
            continue
        imports = {}
        duplicate_imports = set()
        import_lines = {}
        import_refs = {}
        lines = text.splitlines(keepends=True)
        def import_location(node):
            return Location(path=path,start_line=node.lineno,end_line=node.end_lineno,
                start_byte=len(''.join(lines[:node.lineno-1]).encode())+node.col_offset,
                end_byte=len(''.join(lines[:node.end_lineno-1]).encode())+node.end_col_offset)
        for node in tree.body:
            if isinstance(node,ast.Import):
                for alias in node.names:
                    if '.' not in alias.name:
                        if (alias.asname or alias.name) in imports:
                            duplicate_imports.add(alias.asname or alias.name)
                        imports[alias.asname or alias.name] = (alias.name,None)
                        import_lines[alias.asname or alias.name] = node.lineno
                        import_refs[alias.asname or alias.name] = import_location(node)
            if isinstance(node,ast.ImportFrom) and not node.level and node.module and '.' not in node.module:
                for alias in node.names:
                    if alias.name != '*':
                        if (alias.asname or alias.name) in imports:
                            duplicate_imports.add(alias.asname or alias.name)
                        imports[alias.asname or alias.name] = (node.module,alias.name)
                        import_lines[alias.asname or alias.name] = node.lineno
                        import_refs[alias.asname or alias.name] = import_location(node)
        if any(isinstance(node,ast.Call) and isinstance(node.func,ast.Name) and
               node.func.id in {'exec','eval','globals','locals','__import__','setattr','delattr'} for node in ast.walk(tree)):
            continue
        blocked = duplicate_imports | {node.id for node in ast.walk(tree) if isinstance(node,ast.Name) and isinstance(node.ctx,(ast.Store,ast.Del))}
        for node in ast.walk(tree):
            if isinstance(node,(ast.Import,ast.ImportFrom)) and node not in tree.body:
                blocked.update(alias.asname or alias.name.split('.')[0] for alias in node.names)
        blocked.update(node.arg for node in ast.walk(tree) if isinstance(node,ast.arg))
        blocked.update(node.name for node in ast.walk(tree) if isinstance(node,(ast.FunctionDef,ast.AsyncFunctionDef,ast.ClassDef)))
        for node in ast.walk(tree):
            if isinstance(node,ast.Attribute) and isinstance(node.ctx,(ast.Store,ast.Del)) and isinstance(node.value,ast.Name):
                blocked.add(node.value.id)
            if isinstance(node,ast.ExceptHandler) and node.name:
                blocked.add(node.name)
            if isinstance(node,(ast.MatchAs,ast.MatchStar)) and node.name:
                blocked.add(node.name)
            if isinstance(node,ast.MatchMapping) and node.rest:
                blocked.add(node.rest)
        for call in analysis.calls:
            if call.location.path != path or call.kind != 'direct' or call.resolution not in {'external','unresolved'}:
                continue
            pieces = call.expression.split('.')
            alias = pieces[0]
            if alias not in imports or alias in blocked:
                continue
            scope = next((scope for scope in analysis.scopes if scope.id == call.caller_scope_id),None)
            if scope and scope.kind == 'module' and call.location.start_line <= import_lines[alias]:
                continue
            module_name, imported_name = imports[alias]
            exported = pieces[1] if imported_name is None and len(pieces) == 2 else imported_name if len(pieces) == 1 else None
            candidates = registrations.get((module_name,exported),[])
            if module_counts[module_name] != 1 or len(candidates) != 1:
                continue
            # An in-scope Python module with the same name makes import resolution ambiguous.
            if any(PurePosixPath(candidate).name in {module_name+'.py','__init__.py'} and
                   (PurePosixPath(candidate).stem == module_name or PurePosixPath(candidate).parent.name == module_name)
                   for candidate in files):
                continue
            target,binding,registration = candidates[0]
            confirmed.append(Call(id=call.id+':pybind-interface',caller_scope_id=call.caller_scope_id,
                target_id=target.id,expression=call.expression,location=call.location.model_copy(),
                resolution='confirmed',origin='static',target_name=target.qualified_name,kind='interface',
                interface_evidence=[call.location.model_copy(),import_refs[alias],registration,binding,target.location.model_copy()],
                reason=f'源码接口契约：Python 使用 {module_name}.{exported}，pybind11 注册将该公开名称绑定到 {target.qualified_name} 的唯一实现。仅确认声明的模块协作，不确认运行时加载或普通直接调用。'))
    return confirmed
