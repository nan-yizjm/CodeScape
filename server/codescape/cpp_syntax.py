"""已提供包装宏的受限语法恢复；不预处理源码，也不提供调用绑定证明。"""
from __future__ import annotations

import re
from collections import defaultdict

from .cpp_environment import preprocessing_lines


def recovery_macros(files):
    definitions = defaultdict(set)
    for text in files.values():
        for line in preprocessing_lines(text):
            match = re.fullmatch(r'\s*#\s*define\s+([A-Za-z_]\w*)(?:[ \t]+(.*))?', line)
            if match:
                definitions[match[1]].add((match[2] or '').strip())
    annotations, namespaces = set(), set()
    for name, replacements in definitions.items():
        if all(not value or re.fullmatch(r'__(?:declspec|attribute__)\s*\([^;{}]*\)', value)
               for value in replacements):
            annotations.add(name.encode())
        if all(re.fullmatch(r'namespace\s+[A-Za-z_]\w*(?:::[A-Za-z_]\w*)*\s*\{|\}', value)
               for value in replacements):
            namespaces.add(name.encode())
    return annotations, namespaces


def exception_wrappers(files):
    """识别纯 try 前缀和完整 catch 后缀；不推测缺失、解除或其他控制宏。"""
    import tree_sitter_cpp
    from tree_sitter import Language, Parser

    definitions, suffixes = defaultdict(set), defaultdict(set)
    undefined = set()
    for text in files.values():
        for line in preprocessing_lines(text):
            removed = re.fullmatch(r'\s*#\s*undef\s+([A-Za-z_]\w*)\s*', line)
            if removed:
                undefined.add(removed[1])
            match = re.fullmatch(r'\s*#\s*define\s+([A-Za-z_]\w*)(?:[ \t]+(.*))?', line)
            if match:
                definitions[match[1]].add((match[2] or '').strip())
            match = re.fullmatch(r'\s*#\s*define\s+([A-Za-z_]\w*)\([^)]*\)(?:[ \t]+(.*))?', line)
            if match:
                suffixes[match[1]].add((match[2] or '').strip())
    prefixes = {name.encode() for name, values in definitions.items()
                if name not in undefined and 'try' in values and values <= {'', 'try'}}
    parser = Parser(Language(tree_sitter_cpp.language()))

    def complete_catches(value):
        if not value:
            return True
        if not re.match(r'catch\s*\(', value):
            return False
        tree = parser.parse(('void owned() { try {} ' + value + ' }').encode())
        if tree.root_node.has_error or len(tree.root_node.named_children) != 1:
            return False
        body = tree.root_node.named_children[0].child_by_field_name('body')
        return bool(body and len(body.named_children) == 1 and body.named_children[0].type == 'try_statement')

    catches = {name.encode() for name, values in suffixes.items()
               if name not in undefined and any(values) and all(complete_catches(value) for value in values)}
    # 对象式和函数式同名冲突时不能采用任何恢复假设。
    conflicting = {name.encode() for name in definitions.keys() & suffixes.keys()}
    return prefixes - conflicting, catches - conflicting


def recover_parse(parser, parsed, source, macros, exceptions=None):
    if not parsed.root_node.has_error:
        return parsed, False, False
    annotations, namespaces = macros
    excluded = []
    pending = [parsed.root_node]
    while pending:
        node = pending.pop()
        if node.type in {'comment', 'string_literal', 'raw_string_literal', 'char_literal'}:
            excluded.append((node.start_byte, node.end_byte))
        elif node.type.startswith('preproc_') and node.type not in {
            'preproc_if', 'preproc_ifdef', 'preproc_else', 'preproc_elif', 'preproc_elifdef'}:
            excluded.append((node.start_byte, node.end_byte))
        else:
            pending.extend(node.named_children)
    view = bytearray(source)
    changed = namespace_changed = False

    def mask(start, end):
        nonlocal changed
        if any(left < end and start < right for left, right in excluded):
            return False
        # 同长度空白只用于语法树，所有位置、签名、哈希和模型源码仍取原始字节。
        view[start:end] = b' ' * (end - start)
        changed = True
        return True

    for match in re.finditer(rb'(?m)^[ \t]*([A-Za-z_]\w*)[ \t]*\r?$', source):
        if match[1] in namespaces and mask(*match.span(1)):
            namespace_changed = True
    # 仅恢复 class/struct 名称前已知的导出注解；未知宏、继承和函数宏不猜测展开。
    for match in re.finditer(rb'\b(?:class|struct)[ \t]+([A-Za-z_]\w*)[ \t]+[A-Za-z_]\w*'
                             rb'[ \t]*(?:(?:final[ \t]*)?[:{])', source):
        if match[1] in annotations:
            mask(*match.span(1))
    prefixes, catches = exceptions or (set(), set())
    # 只保留源码显式的块边界。原始 try/catch 宏内容仍供原生解析及 AI 阅读，
    # 此处不展开 catch 中的调用，也不选择异常开关的编译配置。
    for match in re.finditer(rb'\b([A-Za-z_]\w*)[ \t]*(?=\{)', source):
        if match[1] in prefixes:
            mask(*match.span(1))
    for match in re.finditer(rb'(?m)^[ \t]*([A-Za-z_]\w*)[ \t]*(\(.*\))[ \t]*(?://[^\n]*)?\r?$', source):
        if match[1] not in catches or any(left <= match.start(1) < right for left, right in excluded):
            continue
        # 将无分号的已知 catch 宏调用作为不透明语法语句保留；在其后现有空白
        # 插入分号，保留换行、调用文本及实参中的嵌套调用，绝不改磁盘源码。
        position = match.end(2)
        while position < len(view) and view[position] in b' \t\r\n':
            if view[position] in b' \t' and not any(left <= position < right for left, right in excluded):
                view[position] = ord(';')
                changed = True
                break
            position += 1
    return (parser.parse(bytes(view)), True, namespace_changed) if changed else (parsed, False, False)
