from __future__ import annotations

from .models import Analysis, Architecture


def learning_units(analysis: Analysis, architecture: Architecture | None, files: dict) -> list[dict]:
    units = [{"id": entity.id, "kind": "function", "source_id": entity.id}
             for entity in analysis.entities if entity.visible]
    if architecture:
        parents = {module.parent_id for module in architecture.modules}
        for module in architecture.modules:
            if (module.id not in parents and not module.entity_ids and not module.incomplete
                    and module.summary.strip() and module.code_paths
                    and all(path in files and path in analysis.readable_paths for path in module.code_paths)):
                units.append({"id": "module:" + module.id, "kind": "module", "source_id": module.id})
    return units


def module_learning_fingerprint(module, manifest: dict, analysis: Analysis, architecture: Architecture) -> tuple:
    # File-level module ranges are conservative: a changed code set, responsibility
    # or implementation hash resets state. No inferred equivalence or fuzzy matching.
    ancestors, parent = [], module.parent_id
    by_id = {item.id: item for item in architecture.modules}
    while parent and parent in by_id:
        item = by_id[parent]
        ancestors.append((item.name, item.summary, tuple(sorted(item.code_paths))))
        parent = item.parent_id
    return (module.name, module.summary, tuple(ancestors), tuple(sorted(module.code_paths)),
            tuple(sorted((path, analysis.file_hashes.get(path, manifest["files"][path]["hash"])) for path in module.code_paths)))


def module_learning_mapping(old: Architecture | None, new: Architecture, old_analysis: Analysis,
                            new_analysis: Analysis, old_snapshot: dict, new_snapshot: dict) -> dict[str, str]:
    if not old:
        return {}
    previous = {unit["source_id"] for unit in learning_units(old_analysis, old, old_snapshot["files"])
                if unit["kind"] == "module"}
    following = {unit["source_id"] for unit in learning_units(new_analysis, new, new_snapshot["files"])
                 if unit["kind"] == "module"}
    mapping = {}
    for module in old.modules:
        if module.id not in previous:
            continue
        fingerprint = module_learning_fingerprint(module, old_snapshot, old_analysis, old)
        candidates = [other for other in new.modules if other.id in following and
                      module_learning_fingerprint(other, new_snapshot, new_analysis, new) == fingerprint]
        duplicates = [other for other in old.modules if other.id in previous and
                      module_learning_fingerprint(other, old_snapshot, old_analysis, old) == fingerprint]
        if len(candidates) == len(duplicates) == 1:
            mapping["module:" + module.id] = "module:" + candidates[0].id
    return mapping
