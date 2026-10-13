"""收窄已经提供且覆盖整个实现的引用；不生成新讲解、不补充未提供的源码依据。"""
from __future__ import annotations


def localize_explanation_evidence(architecture, analysis, expected, files, ranges):
    result = architecture.model_copy(deep=True)
    entities = {entity.id: entity for entity in analysis.entities if entity.visible and entity.id in expected}
    records = []

    def provided(ref):
        return (ref.path in files and 1 <= ref.start_line <= ref.end_line <= len(files[ref.path].splitlines())
                and ref.end_line - ref.start_line < 200
                and any(start <= ref.start_line <= ref.end_line <= end for start, end in ranges.get(ref.path, [])))

    for note in result.explanations:
        entity = entities.get(note.entity_id)
        if entity is None:
            continue
        location = entity.location
        if location.end_line - location.start_line >= 200 or len(note.evidence) >= 6:
            continue
        if any(provided(ref) and ref.path == location.path
               and location.start_line <= ref.start_line <= ref.end_line <= location.end_line
               for ref in note.evidence):
            continue
        candidates = [(index, ref) for index, ref in enumerate(note.evidence)
                      if provided(ref) and ref.path == location.path
                      and ref.start_line <= location.start_line <= location.end_line <= ref.end_line]
        if not candidates:
            continue
        _, original = min(candidates, key=lambda pair: (pair[1].end_line - pair[1].start_line, pair[0]))
        localized = original.model_copy(update={'start_line': location.start_line, 'end_line': location.end_line})
        note.evidence.insert(0, localized)
        records.append({'entity_id': entity.id, 'original': original.model_dump(),
                        'localized': localized.model_dump(), 'origin': 'application_range_localization',
                        'action': 'add_implementation_anchor'})
    return result, records
