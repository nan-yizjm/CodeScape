"""隐藏实现的阅读提示；保持真实匿名作用域，不产生可见函数或调用绑定。"""


def non_node_implementations(analysis, files, ranges):
    scopes = {scope.id: scope for scope in analysis.scopes}
    candidates = []
    for entity in analysis.entities:
        scope = scopes.get(entity.scope_id)
        parent = scopes.get(scope.parent_id) if scope else None
        location = entity.location
        bounds = ranges.get(location.path)
        if (entity.visible or not scope or scope.kind != 'lambda' or not parent
                or parent.kind not in {'module', 'namespace'} or not bounds
                or not bounds[0] <= location.start_line <= bounds[1]):
            continue
        candidates.append(entity)
    candidates.sort(key=lambda entity: (entity.location.path, entity.location.start_byte, entity.id))
    items = []
    for entity in candidates[:40]:
        location = entity.location
        first, last = ranges[location.path]
        start, end = max(first, location.start_line - 3), min(last, location.start_line + 1)
        lines = files[location.path].splitlines()
        text = '\n'.join(lines[start - first:end - first + 1])
        item = {'scope_id': entity.scope_id, 'hidden_reason': entity.hidden_reason,
                'implementation_location': location.model_dump(),
                'implementation_fully_supplied': location.end_line <= last}
        # 不截断源码后声称引用覆盖完整行；过长时只给真实位置供受控补读。
        if len(text) <= 1024:
            item['source_heading'] = {'path': location.path, 'start_line': start, 'end_line': end, 'text': text}
        items.append(item)
    return {'total_count': len(candidates), 'omitted_count': max(0, len(candidates) - len(items)), 'items': items}
