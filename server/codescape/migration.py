from __future__ import annotations

from collections import defaultdict

from .models import Analysis, Architecture


def identity(entity) -> tuple:
    return (entity.language, entity.location.path, entity.qualified_name,
            entity.signature_key or entity.signature, entity.condition)


def learning_mapping(old: Analysis, new: Analysis, *, same_snapshot: bool = False) -> tuple[dict[str, str], dict]:
    previous, following = defaultdict(list), defaultdict(list)
    old_positions, new_positions = defaultdict(list), defaultdict(list)

    def unchanged_definition(entity):
        return (entity.language,entity.location.path,entity.location.start_byte,entity.location.end_byte,
                entity.signature_key or entity.signature,entity.implementation_hash,entity.condition)

    for entity in old.entities:
        if entity.visible:
            previous[identity(entity)].append(entity)
            old_positions[unchanged_definition(entity)].append(entity)
    for entity in new.entities:
        if entity.visible:
            following[identity(entity)].append(entity)
            new_positions[unchanged_definition(entity)].append(entity)
    mapping, records = {}, []
    for key, entities in following.items():
        for entity in entities:
            before = previous.get(key, [])
            reason = "新增、移动或改名，无法唯一匹配"
            if before:
                reason = "身份存在歧义" if len(before) != 1 or len(entities) != 1 else "实现内容已改变"
                if len(before) == len(entities) == 1 and before[0].implementation_hash == entity.implementation_hash:
                    mapping[before[0].id] = entity.id
                    reason = "唯一身份与实现指纹一致"
            elif same_snapshot and entity.language in {'c','cpp'} and entity.implementation_hash:
                position = unchanged_definition(entity)
                candidates = old_positions.get(position,[])
                if len(candidates) == len(new_positions[position]) == 1 and candidates[0].id not in mapping:
                    before = candidates
                    mapping[before[0].id] = entity.id
                    reason = "同一快照的解析名称修正；唯一源码范围、声明与实现指纹一致"
            records.append({"new_id": entity.id, "old_id": before[0].id if len(before) == 1 else None,
                            "kept": entity.id in mapping.values(), "reason": reason})
    old_ids = {entity.id for entity in old.entities if entity.visible}
    return mapping, {"kept_count": len(mapping),
                     "reset_count": sum(not row["kept"] and row["old_id"] is not None for row in records),
                     "added_count": sum(row["old_id"] is None for row in records),
                     "removed_count": len(old_ids - {row["old_id"] for row in records}),
                     "mapping": records,
                     "note": "依赖分析已更新；学习标记仅记录个人进度，不代表客观掌握程度。"}


def module_mapping(old: Architecture | None, new: Architecture, entity_map: dict[str, str]) -> dict[str, str]:
    if not old:
        return {}

    def members(module, modules):
        return set(module.entity_ids).union(*(members(child, modules) for child in modules if child.parent_id == module.id))

    mapped = {}
    for module in old.modules:
        ids = members(module, old.modules)
        if not ids or not ids.issubset(entity_map):
            continue
        target = {entity_map[entity_id] for entity_id in ids}
        candidates = [other for other in new.modules if members(other, new.modules) == target]
        if len(candidates) == 1:
            mapped[module.id] = candidates[0].id
    return mapped
