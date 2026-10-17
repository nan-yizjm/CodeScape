"""差异只依据唯一源码身份与证据，不把相似名字当成相同实体。"""
from collections import Counter, defaultdict

from .migration import identity
from .models import Analysis


def compare_versions(before, after):
    def collect(project):
        index = defaultdict(list)
        analysis = Analysis.model_validate(project.get('result') or {})
        for entity in analysis.entities:
            if entity.visible:
                index[identity(entity)].append(entity)
        return index, analysis
    old, old_analysis = collect(before)
    new, new_analysis = collect(after)
    added, removed, changed, ambiguous = [], [], [], []

    def item(entity):
        return {'id': entity.id, 'name': entity.qualified_name, 'location': entity.location.model_dump()}
    for key in sorted(old.keys() | new.keys(), key=repr):
        a, b = old.get(key, []), new.get(key, [])
        if len(a) > 1 or len(b) > 1:
            ambiguous.append({'name': key[2], 'before': [item(e) for e in a], 'after': [item(e) for e in b]})
        elif not a:
            added.append(item(b[0]))
        elif not b:
            removed.append(item(a[0]))
        elif a[0].implementation_hash != b[0].implementation_hash:
            changed.append({'before': item(a[0]), 'after': item(b[0])})

    def owners(project, analysis):
        entities = {e.id: e for e in analysis.entities if e.visible}
        modules = {m['id']: m for m in (project.get('architecture') or {}).get('modules', [])}
        def title(module):
            path, seen = [module['name']], {module['id']}
            parent = module['parent_id']
            while parent in modules and parent not in seen:
                seen.add(parent)
                path.insert(0, modules[parent]['name'])
                parent = modules[parent]['parent_id']
            return ' / '.join(path)
        return {identity(entities[eid]): title(module) for module in modules.values()
                for eid in module['entity_ids'] if eid in entities}
    previous_owners, following_owners = owners(before, old_analysis), owners(after, new_analysis)
    moved = [{'name': key[2], 'before': previous_owners[key], 'after': following_owners[key]}
             for key in previous_owners.keys() & following_owners.keys()
             if len(old.get(key, [])) == len(new.get(key, [])) == 1 and previous_owners[key] != following_owners[key]]

    def relationships(analysis):
        entities = {e.id: e for e in analysis.entities if e.visible}
        scopes = {e.scope_id: e for e in entities.values()}
        result = Counter()
        labels = {}
        for call in analysis.calls:
            if call.resolution != 'confirmed' or call.target_id not in entities or call.caller_scope_id not in scopes:
                continue
            source, target = scopes[call.caller_scope_id], entities[call.target_id]
            a, b = identity(source), identity(target)
            if len(old.get(a, [])) > 1 or len(new.get(a, [])) > 1 or len(old.get(b, [])) > 1 or len(new.get(b, [])) > 1:
                continue
            key = a, b
            result[key] += 1
            labels[key] = {'from': source.qualified_name, 'to': target.qualified_name,
                           'location': call.location.model_dump()}
        return result, labels
    previous_calls, old_labels = relationships(old_analysis)
    following_calls, new_labels = relationships(new_analysis)
    relation_changes = [dict((new_labels | old_labels)[key], before=previous_calls[key], after=following_calls[key])
                        for key in previous_calls.keys() | following_calls.keys()
                        if previous_calls[key] != following_calls[key]]
    old_names = {m['name'] for m in (before.get('architecture') or {}).get('modules', [])}
    new_names = {m['name'] for m in (after.get('architecture') or {}).get('modules', [])}
    return {'from_version_id': before['version_id'], 'to_version_id': after['version_id'],
            'functions': {'added': added, 'removed': removed, 'changed': changed, 'ambiguous': ambiguous},
            'modules': {'added_names': sorted(new_names - old_names), 'removed_names': sorted(old_names - new_names),
                        'membership_changes': sorted(moved, key=lambda row: row['name'])},
            'dependencies': sorted(relation_changes, key=lambda row: (row['from'], row['to'])),
            'manual': {'before': before.get('manual_edit', {}).get('modified', False),
                       'after': after.get('manual_edit', {}).get('modified', False)},
            'limitations': ['只比较已确认的源码调用，未确认关系不推定为新增或删除。',
                            '模块名称集合变化不代表可靠模块配对；归属变化按唯一函数身份比较。']}
