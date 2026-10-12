"""Locate bounded, original exception-wrapper definitions within authorized files."""
from __future__ import annotations

import re

from .context_tools import ReadRequest
from .cpp_syntax import exception_wrappers


def macro_reads(analysis, files, ranges):
    if not any(entity.visible and entity.language == 'cpp' and entity.location.path in ranges
               and ranges[entity.location.path][0] <= entity.location.end_line
               and entity.location.start_line <= ranges[entity.location.path][1] for entity in analysis.entities):
        return []
    sources = {path: text for path, text in files.items()
               if path.lower().endswith(('.h', '.hpp', '.hh', '.hxx', '.cpp', '.cc', '.cxx'))}
    prefixes, catches = exception_wrappers(sources)
    names = {name.decode() for name in prefixes | catches}
    local = '\n'.join(''.join(files[path].splitlines(keepends=True)[start-1:end])
                      for path, (start, end) in ranges.items())
    names = {name for name in names if re.search(r'\b'+re.escape(name)+r'\b', local)}
    if not names:
        return []
    import tree_sitter_cpp
    from tree_sitter import Language, Parser
    parser = Parser(Language(tree_sitter_cpp.language()))
    references = set()
    for path, text in sorted(sources.items()):
        raw = text.encode()
        pending = [parser.parse(raw).root_node]
        while pending:
            node = pending.pop()
            if node.type not in {'preproc_def', 'preproc_function_def'}:
                pending.extend(node.named_children)
                continue
            name = node.child_by_field_name('name')
            if name is None or raw[name.start_byte:name.end_byte].decode() not in names:
                continue
            target, parent = node, node.parent
            # Include all branches of the closest controlling block, never pick
            # a compile configuration. Oversized conditional blocks are skipped.
            while parent is not None:
                if parent.type in {'preproc_if', 'preproc_ifdef'}:
                    if parent.end_point.row-parent.start_point.row >= 200:
                        if target == node:
                            target = None
                        break
                    target = parent
                parent = parent.parent
            if target is None:
                continue
            start, end = target.start_point.row+1, target.end_point.row+(target.end_point.column > 0)
            end = max(start, end)
            if path in ranges and ranges[path][0] <= start <= end <= ranges[path][1]:
                continue
            fragment = ''.join(text.splitlines(keepends=True)[start-1:end])
            if end-start < 200 and len(fragment) <= 20_000:
                references.add((path, start, end))
    return [ReadRequest(kind='code', reference={'path': path, 'start_line': start, 'end_line': end})
            for path, start, end in sorted(references)[:4]]
