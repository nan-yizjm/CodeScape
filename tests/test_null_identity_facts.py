import pytest
from codescape.analyzers import python_analysis
from codescape.models import Architecture, FunctionExplanation, Module, Overview
from codescape.semantic_facts import null_identity_facts, validate_null_wording


def fixture():
    files = {'owned.py': 'def outer(value):\n    def inner(other):\n        return other is None\n    return bool(value)\n\ndef path(mod):\n    if mod.__file__ is not None:\n        return mod.__file__\n    return None\n'}
    return files, python_analysis(files, 'v')


def test_none_identity_keeps_own_scope_and_does_not_conflate_truthiness():
    files, analysis = fixture()
    facts = null_identity_facts(analysis, files)
    names = {e.name: e for e in analysis.entities if e.visible}
    assert names['outer'].id not in facts
    fact = facts[names['path'].id]
    assert fact['guards'] == [{'expression': 'mod.__file__ is not None', 'start_line': 7, 'end_line': 7}]
    inner = next(e for e in analysis.entities if 'inner' in e.qualified_name)
    assert not inner.visible and inner.id not in facts  # 局部函数不提升为外层事实。


def test_none_wording_is_explicit_but_not_a_general_semantic_proof():
    files, analysis = fixture()
    entity = next(e for e in analysis.entities if e.name == 'path')
    note = FunctionExplanation(entity_id=entity.id, summary='读取模块路径。', details='文件属性非空时返回路径。',
        evidence=[{'path':'owned.py','start_line':7,'end_line':8}])
    value = Architecture(modules=[Module(id='m',name='路径',summary='读取路径',code_paths=['owned.py'],entity_ids=[entity.id])],
                         overview=Overview(introduction='样例',capabilities=['路径'],principle='读取',limitations=[]),explanations=[note])
    facts = null_identity_facts(analysis, files)
    with pytest.raises(ValueError, match='None身份'):
        validate_null_wording(value, facts)
    note.details = '文件属性不是None时返回它；空字符串也满足不是None的条件。'
    validate_null_wording(value, facts)
    note.details = '读取模块文件属性并按条件返回。'
    validate_null_wording(value, facts)
