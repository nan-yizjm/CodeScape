"""导出注解与命名空间宏的受限语法恢复；不预处理源码，也不提供调用绑定证明。"""
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


def recover_parse(parser, parsed, source, macros):
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
    return (parser.parse(bytes(view)), True, namespace_changed) if changed else (parsed, False, False)
