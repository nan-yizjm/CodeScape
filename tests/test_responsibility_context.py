import json

from codescape.analyzers import python_analysis, typescript_analysis
from codescape.provider import Provider
from codescape.responsibility_context import non_node_implementations


def test_typescript_factory_is_reading_context_without_visible_node_or_rebound_call(tmp_path):
    files = {'x.ts': '''function target(value: number) { return value; }
export const parseFactory = () => (value: number) => target(value);
export const encode = (value: number) => target(value);
encode(1);
'''}
    (tmp_path / 'x.ts').write_bytes(files['x.ts'].encode('utf-8'))
    analysis = typescript_analysis(tmp_path, files, 'v')
    before = analysis.model_dump()
    context, visible = Provider(tmp_path)._context(analysis, files)
    payload = json.loads(context)
    hints = payload['non_node_implementations']
    assert hints['total_count'] == 2 and hints['omitted_count'] == 0
    assert any('parseFactory' in item['source_heading']['text'] for item in hints['items'])
    assert all(item['implementation_fully_supplied'] for item in hints['items'])
    assert len(visible) == 1 and analysis.model_dump() == before
    target = next(entity for entity in analysis.entities if entity.visible)
    assert all(call.caller_scope_id != target.scope_id for call in analysis.calls)
    assert all('entity_id' not in item for item in hints['items'])


def test_heading_cannot_read_outside_current_batch_or_claim_whole_implementation(tmp_path):
    full = {'x.py': '# private-outside-range\nfactory = lambda: (\n    1\n)\n'}
    analysis = python_analysis(full, 'v')
    supplied = {'x.py': 'factory = lambda: (\n'}
    hints = non_node_implementations(analysis, supplied, {'x.py': (2, 2)})
    assert hints['total_count'] == 1
    item = hints['items'][0]
    assert item['implementation_fully_supplied'] is False
    assert item['source_heading'] == {'path': 'x.py', 'start_line': 2, 'end_line': 2, 'text': 'factory = lambda: ('}
    assert 'private-outside-range' not in json.dumps(hints)


def test_nested_callback_is_not_promoted_to_top_level_responsibility():
    files = {'x.py': 'def outer():\n    callback = lambda: 1\n    return callback\n'}
    hints = non_node_implementations(python_analysis(files, 'v'), files, {'x.py': (1, 3)})
    assert hints['total_count'] == 0 and not hints['items']


def test_many_hidden_implementations_are_bounded_and_omission_is_explicit():
    files = {'x.py': ''.join(f'f{index} = lambda: {index}\n' for index in range(80))}
    analysis = python_analysis(files, 'v')
    hints = non_node_implementations(analysis, files, {'x.py': (1, 80)})
    assert hints['total_count'] == 80 and len(hints['items']) == 40 and hints['omitted_count'] == 40
    assert not any(entity.visible for entity in analysis.entities)


def test_large_heading_keeps_position_without_truncated_source_evidence():
    files = {'x.py': 'callback = lambda: "' + 'x' * 1100 + '"\n'}
    hints = non_node_implementations(python_analysis(files, 'v'), files, {'x.py': (1, 1)})
    assert hints['total_count'] == 1
    assert 'source_heading' not in hints['items'][0]
    assert hints['items'][0]['implementation_location']['path'] == 'x.py'
