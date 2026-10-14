import pytest
from codescape.analyzers import python_analysis
from codescape.models import Architecture, Module, Overview
from codescape.provider import repair_batch_architecture, validate_architecture


@pytest.mark.parametrize('existing_ids', [
    ['m-unassigned-2'],
    ['m-unassigned-3', 'm-unassigned-3-repair-1'],
])
def test_unassigned_fallback_cannot_collide_with_model_module_ids(existing_ids):
    analysis = python_analysis({'x.py': 'def known(): return 1\ndef missing(): return 2\n'}, 'v')
    entities = {entity.name: entity for entity in analysis.entities if entity.visible}
    expected = {entity.id for entity in entities.values()}
    modules = [Module(id=identifier, name='源码分组', summary='整理当前文件职责。', code_paths=['x.py'],
                      entity_ids=[entities['known'].id] if index == 0 else [])
               for index, identifier in enumerate(existing_ids)]
    value = Architecture(modules=modules,
        overview=Overview(introduction='人工验证样例', capabilities=['结构'], principle='读取源码', limitations=[]))
    repair_batch_architecture(value, analysis, expected, {'x.py'})
    validate_architecture(value, analysis, {'x.py'}, expected_entity_ids=expected)
    assert [module.id for module in value.modules[:-1]] == existing_ids
    assert value.modules[-1].id not in existing_ids and value.modules[-1].incomplete
    assert value.modules[-1].entity_ids == [entities['missing'].id]
    assert sum(len(module.entity_ids) for module in value.modules) == len(expected)
    assert any('未被模型明确归类' in item for item in value.overview.limitations)


def test_fallback_keeps_existing_parent_link_and_known_function_ownership():
    analysis = python_analysis({'x.py': 'def known(): return 1\ndef missing(): return 2\n'}, 'v')
    entities = {entity.name: entity for entity in analysis.entities if entity.visible}
    expected = {entity.id for entity in entities.values()}
    value = Architecture(modules=[
        Module(id='root', name='文件结构', summary='整理源码文件。', code_paths=['x.py']),
        Module(id='m-unassigned-3', parent_id='root', name='已有功能', summary='返回固定值。',
               code_paths=['x.py'], entity_ids=[entities['known'].id]),
    ], overview=Overview(introduction='人工验证样例', capabilities=['结构'], principle='读取源码', limitations=[]))
    repair_batch_architecture(value, analysis, expected, {'x.py'})
    validate_architecture(value, analysis, {'x.py'}, expected_entity_ids=expected)
    assert value.modules[1].id == 'm-unassigned-3' and value.modules[1].parent_id == 'root'
    assert value.modules[1].entity_ids == [entities['known'].id] and not value.modules[1].incomplete
    assert value.modules[-1].parent_id is None and value.modules[-1].incomplete
