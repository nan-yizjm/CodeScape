import hashlib

import tree_sitter_cpp
from codescape.analyzers import cpp_analysis
from codescape.cpp_syntax import exception_wrappers, recover_parse, recovery_macros
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


def exception_fixture():
    return {'macros.hpp': '#if DISABLED\n#define WRAP_TRY\n#define WRAP_CATCH(location)\n'
            '#else\n#define WRAP_TRY try\n#define WRAP_CATCH(location) catch (...) { report(location); }\n#endif\n',
            'owned.cpp': 'void source_loc() {}\nvoid flush() {}\n'
                         'void sink() {\n for (;;) {\n  if (enabled()) {\n'
                         '   WRAP_TRY { output(); }\n'
                         '   WRAP_CATCH(source_loc())\n  }\n }\n'
                         ' if (should_flush()) { flush(); }\n}\n'
                         'void after() { flush(); }\n'}


def test_exception_wrappers_keep_whole_body_and_tail_call_scope(tmp_path):
    files = exception_fixture()
    for name, source in files.items():
        (tmp_path/name).write_text(source, encoding='utf-8', newline='')
    indexed = cpp_analysis(tmp_path, files, 'v', syntax_only=True)
    sink = next(entity for entity in indexed.entities if entity.name == 'sink')
    assert (sink.location.start_line, sink.location.end_line) == (3, 11)
    calls = [call for call in indexed.calls if call.caller_scope_id == sink.scope_id]
    assert {call.expression for call in calls} >= {'output', 'source_loc', 'should_flush', 'flush'}
    tail = next(call for call in calls if call.expression == 'flush')
    assert tail.location.start_line == 10 and tail.resolution != 'confirmed'
    assert not any(call.expression == 'report' for call in calls)
    after = next(entity for entity in indexed.entities if entity.name == 'after')
    assert all(call.caller_scope_id == after.scope_id for call in indexed.calls if call.location.start_line == 12)
    assert files == {name:(tmp_path/name).read_text(encoding='utf-8') for name in files}
    scope = next(scope for scope in indexed.scopes if scope.id == sink.scope_id)
    assert scope.location == sink.location


def test_unsupported_or_undefined_exception_macro_is_not_recovered():
    for source in [
        '#define WRAP_TRY do\n#define WRAP_CATCH(x) catch (...) { report(x); } trailing()\n',
        '#define WRAP_TRY try\n#undef WRAP_TRY\n#define WRAP_CATCH(x) catch (...) {}\n#undef WRAP_CATCH\n',
        '#define WRAP_TRY try\n#define WRAP_TRY(x) catch (...) {}\n',
    ]:
        assert exception_wrappers({'owned.hpp':source}) == (set(),set())


def test_exception_recovery_preserves_literal_directive_and_argument_bytes():
    files = exception_fixture()
    source = files['owned.cpp'] + 'const char *s = R"tag(WRAP_TRY {\n WRAP_CATCH(test())\n )tag";\n'
    raw = source.encode()
    parser = Parser(Language(tree_sitter_cpp.language()))
    recovered, changed, _ = recover_parse(parser, parser.parse(raw), raw,
        recovery_macros(files), exception_wrappers(files))
    assert changed
    assert not recovered.root_node.has_error
    # 调用标识与实参不被掩掉；因此内层调用仍具有自己的源码位置。
    pending = [recovered.root_node]
    calls, literals = [], []
    while pending:
        node = pending.pop()
        if node.type == 'call_expression':
            calls.append(raw[node.start_byte:node.end_byte])
        if node.type == 'raw_string_literal':
            literals.append(raw[node.start_byte:node.end_byte])
        pending.extend(node.named_children)
    assert b'WRAP_CATCH(source_loc())' in calls and b'source_loc()' in calls
    assert literals == [b'R"tag(WRAP_TRY {\n WRAP_CATCH(test())\n )tag"']


def test_destructor_wrapper_does_not_swallow_following_methods_or_their_calls(tmp_path):
    files = exception_fixture()
    files['owned.cpp'] = 'class Worker {\npublic:\n ~Worker() {\n'
    files['owned.cpp'] += '  WRAP_TRY { stop(); }\n  WRAP_CATCH(location())\n }\n'
    files['owned.cpp'] += ' void post() { send(); }\n int count() const { return 1; }\n};\n'
    indexed = cpp_analysis(tmp_path,files,'v',syntax_only=True)
    assert sorted(e.name for e in indexed.entities if e.visible)==['count','post']
    destructor = next(e for e in indexed.entities if e.name=='~Worker')
    assert not destructor.visible and destructor.location.end_line==6
    post = next(e for e in indexed.entities if e.name=='post')
    calls = {c.expression:c for c in indexed.calls}
    assert calls['stop'].caller_scope_id==destructor.scope_id
    assert calls['location'].caller_scope_id==destructor.scope_id
    assert calls['send'].caller_scope_id==post.scope_id
