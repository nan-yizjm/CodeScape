from codescape.analyzers import analyze

CPP = '#include <pybind11/pybind11.h>\nint actual(int n){return n;}\nPYBIND11_MODULE(native, m){m.def("public_name", &actual);}\n'


def indexed(tmp_path, python, cpp=CPP, extra=None):
    files = {'bindings.cpp':cpp,'consumer.py':python,**(extra or {})}
    for path,text in files.items():
        file = tmp_path / path
        file.parent.mkdir(parents=True,exist_ok=True)
        file.write_text(text,encoding='utf-8')
    return analyze(tmp_path,files,'v')


def test_pybind_contract_has_three_sides_and_no_fake_direct_call(tmp_path):
    result = indexed(tmp_path,'import native as bridge\ndef consume(): return bridge.public_name(2)\n')
    interfaces = [call for call in result.calls if call.kind == 'interface']
    assert len(interfaces) == 1
    interface = interfaces[0]
    assert interface.target_name == 'actual' and interface.resolution == 'confirmed'
    assert len(interface.interface_evidence) == 5
    assert [ref.path for ref in interface.interface_evidence] == ['consumer.py','consumer.py','bindings.cpp','bindings.cpp','bindings.cpp']
    assert [ref.start_line for ref in interface.interface_evidence] == [2,1,3,3,2]
    original = next(call for call in result.calls if call.expression == 'bridge.public_name' and call.kind == 'direct')
    assert original.target_id is None and original.resolution != 'confirmed'
    assert interface.caller_scope_id == original.caller_scope_id
    assert not next(entity for entity in result.entities if entity.name == 'PYBIND11_MODULE').visible


def test_pybind_from_import_preserves_explicit_alias(tmp_path):
    result = indexed(tmp_path,'from native import public_name as bound\ndef consume(): return bound(2)\n')
    assert len([call for call in result.calls if call.kind == 'interface']) == 1


def test_same_name_without_registration_does_not_create_bridge(tmp_path):
    result = indexed(tmp_path,'import native\ndef consume():return native.actual(2)\n')
    assert not any(call.kind == 'interface' for call in result.calls)


def test_obfuscated_macro_and_conditional_overload_cannot_confirm_contract(tmp_path):
    python = 'import native\ndef consume():return native.public_name(2)\n'
    for cpp in ('#define/**/PYBIND11_MODULE(a,b) replaced\n'+CPP,
        '#de\\\nfine PYBIND11_MODULE(a,b) replaced\n'+CPP,
        '%:define PYBIND11_MODULE(a,b) replaced\n'+CPP,
        CPP+'#if FEATURE\nint actual(double n){return (int)n;}\n#endif\n'):
        assert not any(call.kind == 'interface' for call in indexed(tmp_path,python,cpp).calls)


def test_reimport_delete_and_pattern_capture_reject_alias_contract(tmp_path):
    for python in ('import other as native\nimport native\nnative.public_name(2)\n',
        'import native\ndel native.public_name\nnative.public_name(2)\n',
        'import native\nmatch 1:\n case native: pass\nnative.public_name(2)\n',
        'import native\ntry: pass\nexcept Exception as native: pass\nnative.public_name(2)\n'):
        assert not any(call.kind == 'interface' for call in indexed(tmp_path,python).calls)


def test_shadowed_import_and_monkey_patch_are_not_bridge_proof(tmp_path):
    for python in ('import native\ndef consume(native):return native.public_name(2)\n',
        'import native\nnative.public_name=lambda n:n\nnative.public_name(2)\n',
        'import native\ndef consume():\n from other import native\n return native.public_name(2)\n',
        'import native\nexec("anything")\nnative.public_name(2)\n',
        'native.public_name(2)\nimport native\n'):
        result = indexed(tmp_path,python)
        assert not any(call.kind == 'interface' for call in result.calls)


def test_ambiguous_registration_overload_and_local_python_module_are_rejected(tmp_path):
    python = 'import native\ndef consume():return native.public_name(2)\n'
    for cpp in (CPP.replace('m.def("public_name", &actual);','m.def("public_name", &actual);m.def("public_name", &actual);'),
        CPP+'int actual(double n){return (int)n;}\n',
        CPP.replace('PYBIND11_MODULE','#if FEATURE\nPYBIND11_MODULE')+'#endif\n',
        '#define PYBIND11_MODULE(a,b) something_else\n'+CPP,
        CPP.replace('m.def("public_name", &actual);','m.def("public_name", &actual);m.attr("public_name") = 7;')):
        result = indexed(tmp_path,python,cpp)
        assert not any(call.kind == 'interface' for call in result.calls)
    result = indexed(tmp_path,python,extra={'native.py':'def public_name(n):return n\n'})
    assert not any(call.kind == 'interface' for call in result.calls)
