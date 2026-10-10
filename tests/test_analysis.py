from pathlib import Path

from codescape.analyzers import cpp_analysis, python_analysis, typescript_analysis


def test_python_real_scope_and_recursion():
    result = python_analysis(
        {
            "x.py": "def target():\n    return target()\ndef outer():\n    def inner():\n        target()\n    callback = lambda: target()\n    return [target() for x in range(2)]\n"
        },
        "v1",
    )
    entities = {entity.name: entity for entity in result.entities}
    assert entities["target"].visible and not entities["inner"].visible
    assert not entities["<lambda>"].visible
    calls = [call for call in result.calls if call.target_id == entities["target"].id]
    assert len(calls) == 4
    assert all(call.caller_scope_id != entities["outer"].scope_id for call in calls)
    assert calls[0].caller_scope_id == entities["target"].scope_id
    assert any(scope.kind == "comprehension" for scope in result.scopes)


def test_python_alias_decorated_and_duplicate_are_not_confirmed():
    text = "def f(): pass\nalias = f\ndef deco(fn): return fn\n@deco\ndef wrapped(): pass\ndef twice(): pass\ndef twice(): pass\ndef use(callback):\n    alias()\n    wrapped()\n    twice()\n    callback()\n"
    result = python_analysis({"x.py": text}, "v1")
    calls = {call.expression: call for call in result.calls}
    assert all(calls[name].resolution != "confirmed" for name in ("alias", "wrapped", "twice", "callback"))


def test_python_import_and_exact_utf8_bytes():
    files = {
        "helper.py": "def helper(): pass\n",
        "main.py": "from helper import helper as run\ndef 调用():\n    run()\n    run()\n",
    }
    result = python_analysis(files, "v1")
    calls = [call for call in result.calls if call.resolution == "confirmed"]
    assert len(calls) == 2
    for call in calls:
        assert (
            files[call.location.path].encode()[call.location.start_byte : call.location.end_byte] == b"run()"
        )


def test_python_default_call_belongs_to_module():
    result = python_analysis({"x.py": "def f(): pass\ndef g(value=f()): pass\n"}, "v1")
    assert result.calls[0].caller_scope_id == result.scopes[0].id


def test_cpp_direct_callback_constructor_lambda_and_conditions(tmp_path: Path):
    text = "void target() {}\nstruct Item {\n Item() { target(); }\n ~Item() { target(); }\n};\nvoid caller() { target(); auto fn = [] { target(); }; fn(); }\nvoid callback(void (*fn)()) { fn(); }\n#if FEATURE\nvoid conditional() {}\n#else\nvoid conditional() {}\n#endif\n"
    (tmp_path / "x.cpp").write_bytes(text.encode())
    result = cpp_analysis(tmp_path, {"x.cpp": text}, "v1")
    assert len([entity for entity in result.entities if not entity.visible]) == 2
    assert any(scope.kind == "lambda" for scope in result.scopes)
    conditional = [entity for entity in result.entities if entity.name == "conditional"]
    assert len(conditional) == 2 and conditional[0].condition != conditional[1].condition
    caller = next(entity for entity in result.entities if entity.name == "caller")
    direct = [
        call
        for call in result.calls
        if call.caller_scope_id == caller.scope_id and call.target_name == "target"
    ]
    assert len(direct) == 1
    assert any(call.resolution == "excluded_indirect" for call in result.calls)


def test_cpp_large_tree_positions_survive_entity_allocation(tmp_path: Path):
    # The Windows 0.26.0 binding crashed on repeated node position access after
    # Pydantic allocation. Keep this workload independent of a fetched repository.
    text = '#include "unavailable.h"\r\n' + ''.join(
        f'/* 中文 {i} */\r\nint function_{i}(int x) {{ return x + {i}; }}\r\n'
        for i in range(350)
    )
    result = cpp_analysis(tmp_path, {"large.c": text}, "v1")
    assert len(result.entities) == 350
    encoded = text.encode()
    for entity in result.entities:
        source = encoded[entity.location.start_byte : entity.location.end_byte]
        assert source.startswith(f'int {entity.name}('.encode())
        assert entity.location.end_line == entity.location.start_line
    assert any(item.category == "missing_dependency" for item in result.diagnostics)


def test_cpp_external_include_never_enters_native_parser(tmp_path: Path, monkeypatch):
    from clang import cindex

    text = '#include "../private.h"\nvoid f() {}\n'
    (tmp_path / "x.cpp").write_bytes(text.encode())

    def forbidden(*args, **kwargs):
        raise AssertionError("不应启动 libclang")

    monkeypatch.setattr(cindex.Index, "create", forbidden)
    result = cpp_analysis(tmp_path, {"x.cpp": text}, "v1")
    assert len(result.entities) == 1 and result.diagnostics


def test_cpp_multiple_functions_on_one_line_and_overload(tmp_path):
    text = "int f(int x) {return x;} double f(double x) {return x;} int use() {return f(1);}\n"
    (tmp_path / "x.cpp").write_bytes(text.encode())
    result = cpp_analysis(tmp_path, {"x.cpp": text}, "v1")
    assert len(result.entities) == 3
    assert len({entity.id for entity in result.entities}) == 3
    confirmed = [call for call in result.calls if call.resolution == "confirmed"]
    assert len(confirmed) == 1
    target = next(entity for entity in result.entities if entity.id == confirmed[0].target_id)
    assert "int x" in target.signature


def test_cpp_out_of_line_constructor_never_becomes_visible(tmp_path):
    text = "void target() {}\nstruct Item { Item(); ~Item(); };\nItem::Item() { target(); }\nItem::~Item() { target(); }\n"
    (tmp_path / "x.cpp").write_bytes(text.encode())
    result = cpp_analysis(tmp_path, {"x.cpp": text}, "v")
    assert len(result.entities) == 3
    assert sum(entity.visible for entity in result.entities) == 1
    assert len([call for call in result.calls if call.resolution == "confirmed"]) == 2


def test_typescript_direct_alias_and_hidden_scope(tmp_path: Path):
    text = "export function target() {}\nconst alias = target;\nexport function outer(cb: () => void) { target(); alias(); cb(); const inner = () => target(); }\n"
    (tmp_path / "x.ts").write_bytes(text.encode())
    result = typescript_analysis(tmp_path, {"x.ts": text}, "v1")
    assert not result.diagnostics
    outer = next(entity for entity in result.entities if entity.name == "outer")
    direct = [call for call in result.calls if call.resolution == "confirmed"]
    assert len(direct) == 2
    assert sum(call.caller_scope_id == outer.scope_id for call in direct) == 1
    assert next(call for call in result.calls if call.expression == "alias").resolution == "excluded_indirect"
    assert next(call for call in result.calls if call.expression == "cb").resolution != "confirmed"


def test_typescript_reassignment_and_overload(tmp_path):
    text = "function mutable() {} function other() {} mutable = other; mutable();\nfunction overloaded(x: number): number;\nfunction overloaded(x: string): string;\nfunction overloaded(x: number | string) {return x;}\nfunction use() { return overloaded(1); }\n"
    (tmp_path / "x.ts").write_bytes(text.encode())
    result = typescript_analysis(tmp_path, {"x.ts": text}, "v")
    assert next(call for call in result.calls if call.expression == "mutable").resolution != "confirmed"
    assert next(call for call in result.calls if call.expression == "overloaded").resolution == "confirmed"


def test_cpp_missing_dependency_retains_calls_in_real_hidden_scopes(tmp_path):
    text = '#include "not_available.h"\nvoid target(){}\nvoid caller(){ target(); auto callback=[](){target();}; callback(); }\n'
    (tmp_path / 'owned.cpp').write_text(text,encoding='utf-8')
    result = cpp_analysis(tmp_path, {'owned.cpp':text}, 'v')
    assert len(result.calls) == 3
    caller = next(entity for entity in result.entities if entity.name == 'caller')
    calls = [call for call in result.calls if call.expression == 'target']
    assert len(calls) == 2 and sum(call.caller_scope_id == caller.scope_id for call in calls) == 1
    scopes = {scope.id:scope for scope in result.scopes}
    assert any(scopes[call.caller_scope_id].kind == 'lambda' for call in calls)
    assert all(call.resolution != 'confirmed' and call.target_id is None for call in result.calls)
    assert all(text.encode()[call.location.start_byte:call.location.end_byte].endswith(b'()') for call in result.calls)


def test_cpp_explicitly_qualified_virtual_call_is_static_binding(tmp_path):
    text = 'struct Base {virtual int value(){return 1;}};\nint caller(Base& item){ return item.Base::value()+item.value(); }\n'
    (tmp_path / 'owned.cpp').write_text(text,encoding='utf-8')
    result = cpp_analysis(tmp_path, {'owned.cpp':text}, 'v')
    qualified = next(call for call in result.calls if call.expression == 'item.Base::value')
    dynamic = next(call for call in result.calls if call.expression == 'item.value')
    assert qualified.resolution == 'confirmed' and qualified.target_name == 'Base::value'
    assert dynamic.resolution == 'excluded_indirect' and dynamic.target_id is None


def test_cpp_unique_operator_call_and_ambiguous_overload(tmp_path):
    text = 'struct Value { int operator+(int n){return n;} };\nint caller(Value& item){return item+1;}\nint choose(int n){return n;} int choose(long n){return (int)n;}\nint ambiguous(){return choose(1.2);}\n'
    (tmp_path / 'owned.cpp').write_text(text,encoding='utf-8')
    result = cpp_analysis(tmp_path, {'owned.cpp':text}, 'v')
    # An ill-formed translation unit cannot prove even the otherwise valid operator.
    assert any(entity.name == 'operator+' and entity.visible for entity in result.entities)
    assert all(call.resolution != 'confirmed' for call in result.calls)
    good = text[:text.index('int choose')]
    (tmp_path / 'owned.cpp').write_text(good,encoding='utf-8')
    parsed = cpp_analysis(tmp_path, {'owned.cpp':good}, 'v')
    assert any(call.target_name == 'Value::operator+' and call.resolution == 'confirmed' for call in parsed.calls)


def test_cpp_macro_keeps_physical_spelling_and_expansion_bytes(tmp_path):
    text = '// 中文 😀\r\n#define CALL(v) target(v)\r\nint target(int v){return v;}\r\nint caller(){\r\n\treturn CALL(2);\r\n}\r\n'
    (tmp_path / 'owned.c').write_bytes(text.encode())
    result = cpp_analysis(tmp_path, {'owned.c':text}, 'v')
    call = next(call for call in result.calls if call.expression == 'CALL')
    assert call.resolution == 'confirmed' and call.target_name == 'target'
    assert call.spelling_location.start_line == 2 and call.expansion_location.start_line == 5
    spelling, expansion = call.spelling_location, call.expansion_location
    assert text.encode()[spelling.start_byte:spelling.end_byte] == b'target(v)'
    assert text.encode()[expansion.start_byte:expansion.end_byte] == b'CALL(2)'


def test_cpp_macro_multiple_expanded_calls_do_not_fabricate_single_evidence(tmp_path):
    text = '#define BOTH() target()+target()\nint target(){return 1;}\nint caller(){return BOTH();}\n'
    (tmp_path / 'owned.c').write_bytes(text.encode())
    result = cpp_analysis(tmp_path, {'owned.c':text}, 'v')
    macro = next(call for call in result.calls if call.expression == 'BOTH')
    assert macro.resolution == 'unresolved' and macro.target_id is None
    assert macro.expansion_location and not macro.spelling_location
    assert any(item.category == 'macro_location' for item in result.diagnostics)


def test_cpp_fallback_callback_shadow_does_not_allow_same_name_target(tmp_path):
    text = '#include "unavailable.h"\nvoid target(){}\nvoid caller(void(*target)()){target(); auto alias=target;alias();}\n'
    (tmp_path / 'owned.cpp').write_bytes(text.encode())
    result = cpp_analysis(tmp_path, {'owned.cpp':text}, 'v')
    assert {call.expression for call in result.calls} == {'target','alias'}
    assert all(call.resolution == 'excluded_indirect' and call.target_id is None for call in result.calls)
