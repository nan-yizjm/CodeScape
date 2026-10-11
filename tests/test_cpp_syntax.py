import hashlib

import tree_sitter_cpp
from codescape.analyzers import cpp_analysis
from codescape.cpp_syntax import recover_parse, recovery_macros
from tree_sitter import Language, Parser


def fixture():
    return {'macros.h': '#define API __declspec(dllexport)\n#define BEGIN namespace owned {\n#define END }\n',
            'owned.hpp': 'BEGIN\nclass API Logger {\npublic:\n Logger() {}\n'
                         ' int value() const { return 1; }\n'
                         ' void send() { value(); }\n};\nEND\n'}


def test_export_decorated_record_recovers_methods_without_class_function(tmp_path):
    files = fixture()
    original = {path: hashlib.sha256(text.encode()).hexdigest() for path, text in files.items()}
    for path, text in files.items():
        (tmp_path/path).write_text(text, encoding='utf-8', newline='')
    result = cpp_analysis(tmp_path, files, 'v', syntax_only=True)
    assert sorted(entity.name for entity in result.entities if entity.visible) == ['send', 'value']
    constructor = next(entity for entity in result.entities if entity.name == 'Logger')
    assert not constructor.visible and constructor.hidden_reason == '构造或析构'
    assert all(entity.location.start_line >= 4 for entity in result.entities)
    caller = next(entity for entity in result.entities if entity.name == 'send')
    call = next(call for call in result.calls if call.expression == 'value')
    assert call.caller_scope_id == caller.scope_id and call.location.start_line == 6
    assert call.resolution != 'confirmed'
    assert any(note.category == 'syntax_recovery' for note in result.diagnostics)
    assert original == {path: hashlib.sha256((tmp_path/path).read_bytes()).hexdigest() for path in files}


def test_unknown_macro_record_never_becomes_phantom_function(tmp_path):
    files = {'owned.hpp': 'BEGIN\nclass UNKNOWN Logger { public: void send() {} };\nEND\n'}
    result = cpp_analysis(tmp_path, files, 'v', syntax_only=True)
    assert not any(entity.name == 'Logger' and entity.visible for entity in result.entities)
    assert any(note.category == 'parse_failed' for note in result.diagnostics)


def test_recovery_keeps_literal_comment_and_directive_bytes():
    files = fixture()
    source = (files['owned.hpp'] + '// class API Fake {\n'
              'const char* s = R"tag(class API Fake {\nBEGIN\nEND\n)tag";\n'
              '#define DOC class API Fake {\n').encode()
    parser = Parser(Language(tree_sitter_cpp.language()))
    parsed = parser.parse(source)
    recovered, changed, namespace = recover_parse(parser, parsed, source, recovery_macros(files))
    assert changed and namespace
    pending = [recovered.root_node]
    literals = []
    while pending:
        node = pending.pop()
        if node.type in {'raw_string_literal', 'comment', 'preproc_def'}:
            literals.append(source[node.start_byte:node.end_byte])
        pending.extend(node.named_children)
    assert any(b'R"tag(class API Fake {\nBEGIN\nEND\n)tag"' == value for value in literals)
    assert any(b'// class API Fake {' == value for value in literals)
    assert any(b'#define DOC class API Fake {' in value for value in literals)


def test_behavior_macros_are_not_export_annotations():
    annotations, namespaces = recovery_macros({'x.hpp':
        '#define API int changed;\n#define BEGIN namespace n { work();\n#define END }; run();\n'})
    assert not annotations and not namespaces
