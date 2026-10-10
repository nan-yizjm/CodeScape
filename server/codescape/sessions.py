from __future__ import annotations

from copy import deepcopy


def validate_session(value, project):
    entities = {entity["id"] for entity in (project.get("result") or {}).get("entities", [])}
    calls = {call["id"] for call in (project.get("result") or {}).get("calls", []) if call["resolution"] == "confirmed"}
    paths = project["snapshot"]["files"]
    for panel in value.get("panels", []):
        if panel.get("entity_id") and panel["entity_id"] not in entities:
            raise ValueError("外部实体")
        if panel.get("location") and panel["location"]["path"] not in paths:
            raise ValueError("外部源码")
        if any(identifier not in calls for identifier in panel.get("call_ids", [])):
            raise ValueError("外部调用")


def migrate_session(session, old, new, modules, entities, version_id):
    result = deepcopy(session)
    previous_modules = {module["id"]: module for module in (old.get("architecture") or {}).get("modules", [])}
    following_modules = {module["id"]: module for module in (new.get("architecture") or {}).get("modules", [])}
    navigation_map = dict(modules)

    def module_identity(module, pool):
        names, parent, seen = [], module.get("parent_id"), set()
        while parent in pool and parent not in seen:
            seen.add(parent)
            names.append(pool[parent].get("name"))
            parent = pool[parent].get("parent_id")
        return (module.get("name"), tuple(sorted(module.get("code_paths", []))), tuple(names))

    for identifier, module in previous_modules.items():
        if not module.get("name") or not module.get("code_paths"):
            continue
        identity = module_identity(module, previous_modules)
        previous = [item for item in previous_modules.values() if module_identity(item, previous_modules) == identity]
        following = [item for item in following_modules.values() if module_identity(item, following_modules) == identity]
        if len(previous) == len(following) == 1:
            navigation_map.setdefault(identifier, following[0]["id"])
    following_entities = {entity["id"]: entity for entity in new["result"]["entities"]}
    old_files, new_files = old["snapshot"]["files"], new["snapshot"]["files"]

    def nearest(identifier):
        seen = set()
        while identifier and identifier not in seen:
            if identifier in navigation_map:
                return navigation_map[identifier]
            seen.add(identifier)
            identifier = previous_modules.get(identifier, {}).get("parent_id")
        return None

    focus = nearest(result.get("focus_id"))
    panels = []
    for saved in result.get("panels", []):
        panel = deepcopy(saved)
        kind = panel.get("kind")
        if kind in {"overview", "diagnostics"}:
            panel.pop("scroll", None)
        elif kind == "module":
            panel["module_id"] = nearest(panel.get("module_id"))
            panel.pop("scroll", None)
            if not panel["module_id"]:
                continue
        elif kind == "function":
            panel["entity_id"] = entities.get(panel.get("entity_id"))
            panel.pop("scroll", None)
            if not panel["entity_id"]:
                continue
        elif kind == "source":
            path = (panel.get("location") or {}).get("path")
            target_id = entities.get(panel.get("entity_id"))
            if path in old_files and path in new_files and old_files[path] == new_files[path]:
                panel["entity_id"] = target_id
            elif target_id in following_entities:
                panel["entity_id"] = target_id
                panel["location"] = following_entities[target_id]["location"]
                panel.pop("scroll", None)
            else:
                continue
        else:
            # Relations have changed; never restore stale call evidence IDs.
            continue
        panels.append(panel)
    result.update(version_id=version_id, revision=result.get("revision", 0) + 1,
                  focus_id=focus, expanded_id=focus, show_other_modules=False,
                  function_origin=None, panels=panels[-20:], expanded_evidence={}, viewport=None, panel=None)
    return result
