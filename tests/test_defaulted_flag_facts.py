import pytest
from codescape.analyzers import python_analysis
from codescape.provider import Provider
from codescape.semantic_facts import defaulted_flag_facts

SOURCE = '''import os
def loading(default: bool = True):
    value = os.environ.get("SKIP_LOADING")
    if not value:
        return default
    return value.lower() in ("0", "false", "no")
'''


def test_source_branches_keep_overridable_default_and_comparison_separate():
    files = {'flags.py': SOURCE}
    analysis = python_analysis(files, 'v')
    fact = next(iter(defaulted_flag_facts(analysis, files).values()))
    assert fact['falsey_returns_parameter'] == 'default'
    assert fact['omitted_parameter_default'] is True
    assert fact['flag_name'] == 'SKIP_LOADING'
    assert fact['truthy_result_expression'] == "value.lower() in ('0', 'false', 'no')"
    assert fact['start_line'] == 2 and fact['end_line'] == 6
    assert '不执行导入' in fact['boundary']
    # Recognition describes source branches even for a locally supplied reader;
    # it must not assert a standard-library binding or confirm any new call.
    shadowed = SOURCE.replace('def loading(default: bool = True):', 'def loading(os, default=True):')
    other = python_analysis({'flags.py': shadowed}, 'v')
    assert defaulted_flag_facts(other, {'flags.py': shadowed})
    assert all(call.resolution != 'confirmed' for call in other.calls)


def test_keyword_only_false_default_and_not_in_do_not_inherit_positive_flag_semantics():
    text = SOURCE.replace('default: bool = True', '*, fallback=False').replace('return default', 'return fallback')
    text = text.replace(' in (', ' not in (')
    files = {'flags.py': text}
    fact = next(iter(defaulted_flag_facts(python_analysis(files, 'v'), files).values()))
    assert fact['omitted_parameter_default'] is False
    assert fact['falsey_returns_parameter'] == 'fallback'
    assert 'not in' in fact['truthy_result_expression']


@pytest.mark.parametrize('text', [
    SOURCE.replace('True', 'choose_default()'),
    SOURCE.replace('return default', 'return GLOBAL_DEFAULT'),
    SOURCE.replace('    if not value:', '    default = False\n    if not value:'),
    SOURCE.replace('"SKIP_LOADING"', '"SKIP_LOADING", "1"'),
    SOURCE.replace('value.lower()', 'value.lower("unexpected")'),
    SOURCE.replace('def loading', '@replace_result\ndef loading'),
])
def test_unknown_or_changed_patterns_do_not_produce_default_facts(text):
    files = {'flags.py': text}
    assert not defaulted_flag_facts(python_analysis(files, 'v'), files)


def test_model_context_never_attributes_partial_source_to_complete_function_facts():
    files = {'flags.py': SOURCE}
    analysis = python_analysis(files, 'v')
    provider = Provider.__new__(Provider)
    import json

    full, _ = provider._context(analysis, files)
    assert json.loads(full)['source_facts'][0]['kind'] == 'defaulted_flag_branch'
    prefix = '\n'.join(SOURCE.splitlines()[:-1]) + '\n'
    partial, _ = provider._context(analysis, {'flags.py': prefix}, {'flags.py': (1, 5)})
    assert json.loads(partial)['source_facts'] == []
    suffix, _ = provider._context(analysis, {'flags.py': '    return default\n'}, {'flags.py': (5, 5)})
    assert json.loads(suffix)['source_facts'] == []
