import json
from pathlib import Path

import pytest
from clang import cindex
from codescape import analyzers
from codescape.analyzers import cpp_analysis
from codescape.cpp_environment import (
    compiler_configurations,
    preprocessing_lines,
    remap_path,
    safe_arguments,
    trusted_headers,
)
from codescape.snapshots import create_snapshot


def write(root, files):
    for path, text in files.items():
        target = root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8", newline="")


def test_default_snapshot_include_root_and_explicit_config_stay_distinct(tmp_path):
    files = {'include/owned/api.h':'int target(void);\n',
             'src/main.c':'#include <owned/api.h>\nint target(void){return 1;}\nint caller(void){return target();}\n'}
    write(tmp_path,files)
    indexed=cpp_analysis(tmp_path,files,'v')
    assert any(call.target_name == 'target' and call.resolution == 'confirmed' for call in indexed.calls)
    configured={**files,'compile_commands.json':json.dumps([{'directory':str(tmp_path),'file':'src/main.c',
                                                            'arguments':['cc','src/main.c']}])}
    write(tmp_path,configured)
    explicit=cpp_analysis(tmp_path,configured,'v')
    assert not any(call.resolution == 'confirmed' for call in explicit.calls)
    assert any(item.category == 'missing_dependency' for item in explicit.diagnostics)


def test_config_prefers_arguments_and_remaps_original_without_reading_it(tmp_path):
    original = r"C:\original-private-project"
    commands = [{"directory": original + r"\build", "file": r"..\src\owned.c",
        "arguments": ["untrusted-compiler.exe", "-I", r"..\include", "-D", "VALUE=7", "-std=c17",
            "-o", "must-not-write.exe", "-Xclang", "-load", "plugin.dll", "@private.rsp", "-I", r"C:\outside"],
        "command": "do-not-execute --different"}]
    files = {"src/owned.c": "int f(){return VALUE;}", "compile_commands.json": json.dumps(commands)}
    values, notes = compiler_configurations(tmp_path, files, original)
    args, includes, removed = values["src/owned.c"][0]
    assert args == ["-I", str(tmp_path / "include"), "-DVALUE=7", "-std=c17"]
    assert includes == [tmp_path, tmp_path / "include"] and removed > 0 and notes
    assert remap_path(r"..\..\outside", tmp_path, original, "build") is None


def test_command_is_tokenized_only_and_maximum_configurations_is_bounded(tmp_path):
    records = [{"file": "owned.c", "command": f'"never run.exe" -DVALUE={index} -I "include directory" && touch outside'}
        for index in range(20)]
    result, notes = compiler_configurations(tmp_path, {"owned.c": "int f(){return VALUE;}",
        "compile_commands.json": json.dumps(records)})
    assert len(result["owned.c"]) == 8
    assert all("touch" not in arg and "&&" not in arg for args, _, _ in result["owned.c"] for arg in args)
    assert notes


@pytest.mark.parametrize("directive", [
    '#include "../private.h"',
    '/* comment */ #include "../private.h"',
    '%:include "../private.h"',
    '#inc\\\nlude "../private.h"',
    '#inc??/\nlude "../private.h"',
    '#define FILE "../private.h"\n#include FILE',
    '#include_next "private.h"',
    '#import "private.h"',
])
def test_hidden_or_dynamic_external_include_never_starts_parser(tmp_path, monkeypatch, directive):
    files = {"unsafe.cpp": directive + "\nvoid owned(){}\n"}
    write(tmp_path, files)
    monkeypatch.setattr(cindex.Index, "create", lambda: pytest.fail("越界包含不能进入原生解析"))
    result = cpp_analysis(tmp_path, files, "v1")
    assert any(item.category == "missing_dependency" for item in result.diagnostics)


def test_missing_dependency_does_not_disable_unrelated_unit(tmp_path):
    files = {"unsafe.c": '#include "missing.h"\nvoid unparsed(){}\n',
        "safe.c": "int target(){return 1;}\nint caller(){return target();}\n"}
    write(tmp_path, files)
    result = cpp_analysis(tmp_path, files, "v1")
    assert len([call for call in result.calls if call.resolution == "confirmed"]) == 1
    assert {entity.name for entity in result.entities if entity.visible} == {"unparsed", "target", "caller"}


def test_native_snapshot_content_and_safe_configuration_are_used(tmp_path):
    files = {"include/decl.h": "int target(void);\n",
        "impl.c": "int target(void){return 1;}\n",
        "caller.c": '#include "decl.h"\nint caller(void){return target()+VALUE;}\n'}
    files["compile_commands.json"] = json.dumps([{"file": "caller.c",
        "arguments": ["never-execute", "-I", "include", "-DVALUE=7"]}])
    write(tmp_path, files)
    (tmp_path / "caller.c").write_text("already modified physical original", encoding="utf-8")
    result = cpp_analysis(tmp_path, files, "v1")
    calls = [call for call in result.calls if call.resolution == "confirmed"]
    assert len(calls) == 1
    assert calls[0].target_name == "target"
    assert "compile_commands.json" not in result.readable_paths
    assert not any(item.path == "caller.c" and item.category == "missing_dependency" for item in result.diagnostics)


def test_multiple_compile_configurations_with_same_target_agree(tmp_path):
    files = {"owned.c": "int target(){return 1;}\nint caller(){return target();}\n",
        "compile_commands.json": json.dumps([{"file": "owned.c", "arguments": ["cc", "-DVALUE=1"]},
            {"file": "owned.c", "arguments": ["cc", "-DVALUE=2"]}])}
    write(tmp_path, files)
    result = cpp_analysis(tmp_path, files, "v1")
    assert len(result.entities) == 2 and len(result.calls) == 1
    assert result.calls[0].resolution == 'confirmed' and result.calls[0].target_name == 'target'
    assert len({scope.id for scope in result.scopes})==len(result.scopes)
    assert any(item.category == "configuration" for item in result.diagnostics)


def test_multiple_configurations_selecting_different_implementations_have_no_edge(tmp_path):
    files={'owned.c':'#if SELECT\nint chosen(){return 1;}\n#else\nint chosen(){return 2;}\n#endif\nint caller(){return chosen();}\n',
        'compile_commands.json':json.dumps([{'file':'owned.c','arguments':['cc','-DSELECT=1']},
            {'file':'owned.c','arguments':['cc','-DSELECT=0']}])}
    write(tmp_path,files)
    result=cpp_analysis(tmp_path,files,'v')
    assert len([entity for entity in result.entities if entity.name=='chosen'])==2
    assert len(result.calls)==1 and result.calls[0].resolution=='unresolved'
    assert result.calls[0].target_id is None
    assert any(item.category=='condition_unknown' and '冲突' in item.message for item in result.diagnostics)


@pytest.mark.parametrize('gap',['failed','overflow'])
def test_unverified_configurations_do_not_hide_behind_successful_configuration(tmp_path,gap):
    source='int target(){return 1;}\n#if SELECT\n#include "missing.h"\n#endif\nint caller(){return target();}\n'
    arguments=[['cc','-DSELECT=0'],['cc','-DSELECT=1']] if gap=='failed' else [['cc',f'-DVALUE={index}','-DSELECT=0'] for index in range(9)]
    files={'owned.c':source,'compile_commands.json':json.dumps([{'file':'owned.c','arguments':value} for value in arguments])}
    write(tmp_path,files)
    result=cpp_analysis(tmp_path,files,'v')
    assert len(result.entities)==2 and len(result.calls)==1
    assert result.calls[0].resolution=='unresolved' and result.calls[0].target_id is None
    assert any(item.category=='condition_unknown' for item in result.diagnostics)


def test_header_guards_do_not_turn_ordinary_implementations_into_feature_branches(tmp_path):
    files={'owned.h':'#ifndef OWNED_H\n#define OWNED_H\ninline int target(){return 1;}\ninline int helper(){return target();}\n#endif\n',
        'owned.cpp':'#include "owned.h"\nint caller(){return helper();}\n'}
    write(tmp_path,files)
    result=cpp_analysis(tmp_path,files,'v')
    assert len(result.entities)==3
    assert all(entity.condition is None for entity in result.entities)
    assert len(result.calls)==2 and all(call.resolution=='confirmed' for call in result.calls)


def test_template_instances_share_definition_but_explicit_specialization_is_distinct(tmp_path):
    files={'owned.cpp':'template<class T> T convert(T value){return value;}\ntemplate<> int convert<int>(int value){return value+1;}\nint caller(){return convert(1)+int(convert(2.0))+int(convert(3.0f));}\n'}
    write(tmp_path,files)
    result=cpp_analysis(tmp_path,files,'v')
    assert len(result.entities)==3
    calls=[call for call in result.calls if call.expression=='convert']
    assert len(calls)==3 and all(call.resolution=='confirmed' for call in calls)
    counts={target:sum(call.target_id==target for call in calls) for target in {call.target_id for call in calls}}
    assert sorted(counts.values())==[1,2]


def test_same_branch_calls_keep_conditions_and_do_not_connect_mutually_exclusive_bodies(tmp_path):
    files={'owned.c':'#if SELECT\nint a(){return 1;}\nint caller(){return a();}\n#else\nint b(){return 2;}\nint caller(){return b();}\n#endif\n',
        'compile_commands.json':json.dumps([{'file':'owned.c','arguments':['cc','-DSELECT=1']},
            {'file':'owned.c','arguments':['cc','-DSELECT=0']}])}
    write(tmp_path,files)
    result=cpp_analysis(tmp_path,files,'v')
    entities={entity.id:entity for entity in result.entities}
    scopes={entity.scope_id:entity for entity in result.entities}
    assert len(result.calls)==2
    for call in result.calls:
        assert call.resolution=='confirmed'
        assert entities[call.target_id].condition==scopes[call.caller_scope_id].condition


def test_nested_alternatives_retain_outer_condition_and_ifdef_defined_semantics(tmp_path):
    files={'owned.c':'#ifdef OUTER\n#if INNER\nint a(){return 1;}\n#elif NEXT\nint b(){return 2;}\n#else\nint c(){return 3;}\nint caller(){return c();}\n#endif\n#endif\n',
        'compile_commands.json':json.dumps([{'file':'owned.c','arguments':['cc','-DOUTER=0','-DINNER=0','-DNEXT=0']}])}
    write(tmp_path,files)
    result=cpp_analysis(tmp_path,files,'v')
    assert len(result.calls)==1 and result.calls[0].resolution=='confirmed'
    target=next(entity for entity in result.entities if entity.name=='c')
    assert target.condition.startswith('(defined(OUTER)) &&')
    assert '!(INNER)' in target.condition and '!(NEXT)' in target.condition
    assert next(entity for entity in result.entities if entity.name=='caller').condition==target.condition


def test_system_declaration_cannot_read_outside_overlay_even_through_macro(tmp_path, monkeypatch):
    sdk = tmp_path / "trusted-sdk"
    include = sdk / "include"
    (include / "c++/v1").mkdir(parents=True)
    (include / "bridge.h").write_text("#include LEAK\n", encoding="utf-8")
    private = tmp_path / "private.h"
    private.write_text("int forbidden_private_symbol(){return 42;}\n", encoding="utf-8")
    root = tmp_path / "snapshot"
    root.mkdir()
    files = {"owned.c": "#include <bridge.h>\nint owned(){return 1;}\n",
        "compile_commands.json": json.dumps([{"file": "owned.c", "arguments": ["cc",
            '-DLEAK="' + private.as_posix() + '"']}])}
    write(root, files)
    monkeypatch.setattr(analyzers, "trusted_headers", lambda: ((include / "c++/v1", include),
        frozenset({"include/bridge.h"}), None))
    result = cpp_analysis(root, files, "v1")
    assert {entity.name for entity in result.entities} == {"owned"}
    assert any("file not found" in item.message for item in result.diagnostics)


def test_trusted_standard_library_is_not_a_project_entity(tmp_path):
    directories, _, error = trusted_headers()
    if error:
        pytest.skip("该开发环境尚未准备随包声明资源；安装验证必须单列执行")
    assert directories
    files = {"owned.cpp": "#include <vector>\nint target(){return 1;}\nint caller(){std::vector<int> v{target()};return v[0];}\n"}
    write(tmp_path, files)
    result = cpp_analysis(tmp_path, files, "v1")
    assert {entity.name for entity in result.entities if entity.visible} == {"target", "caller"}
    assert any(call.target_name == "target" and call.resolution == "confirmed" for call in result.calls)
    assert not any(item.category == "missing_dependency" for item in result.diagnostics)


def test_metadata_copied_as_data_with_no_scripts_and_source_unchanged(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    files = {"owned.c": "int owned(){return 1;}\n", "compile_commands.json": "[]",
        "README.md": "# 应用自有验证\n", "install.ps1": "must never execute", ".env": "must never upload"}
    write(source, files)
    manifest = create_snapshot(source, tmp_path / "workspace")
    assert set(manifest["files"]) == {"owned.c", "compile_commands.json", "README.md"}
    assert manifest["files"]["compile_commands.json"]["kind"] == "metadata"
    assert manifest["files"]["owned.c"]["kind"] == "source"
    assert all((source / path).read_text(encoding="utf-8") == text for path, text in files.items())


def test_preprocessing_keeps_literal_content_and_blocks_comments():
    assert preprocessing_lines('/* x */ #include "safe.h" // comment')[0].strip() == '#include "safe.h"'
    args, _, removed = safe_arguments(["cc", "-fplugin=evil.dll", "-include", "../private.h",
        "-isysroot", "outside", "-DVALID=1", "-UOLD"], Path("C:/snapshot"), None, ".")
    assert args == ["-DVALID=1", "-UOLD"] and removed == 5


def test_relative_include_can_return_to_another_snapshot_directory(tmp_path):
    files = {"include/decl.h": "int target(void);\n", "impl.c": "int target(void){return 1;}\n",
        "sub/caller.c": '#include "../include/decl.h"\nint caller(void){return target();}\n'}
    write(tmp_path, files)
    result = cpp_analysis(tmp_path, files, "v1")
    assert any(call.resolution == "confirmed" and call.target_name == "target" for call in result.calls)


def test_macro_qualified_return_type_does_not_become_function_name(tmp_path):
    files = {'owned.cpp':'#include <vector>\n#define API inline\nnamespace app {\n'
        'namespace level { enum E {off}; }\n'
        'struct Logger { level::E flush_level()const; const std::vector<int>& sinks()const; std::vector<int> data; };\n'
        'API level::E Logger::flush_level() const {return level::off;}\n'
        'API const std::vector<int>& Logger::sinks() const {return data;}\n'
        'int caller(const Logger& item){return item.flush_level();}\n}\n'}
    write(tmp_path,files)
    result = cpp_analysis(tmp_path,files,'v1')
    recovered = [entity for entity in result.entities if entity.location.start_line in {6,7}]
    assert {entity.qualified_name for entity in recovered} == {'app::Logger::flush_level','app::Logger::sinks'}
    assert all('level::E' not in entity.name and 'vector' not in entity.name for entity in recovered)
    assert not any(item.category == 'symbol_name_unresolved' for item in result.diagnostics)
    assert any(call.resolution == 'confirmed' and call.target_name == 'app::Logger::flush_level' for call in result.calls)
    scopes = {scope.id:scope for scope in result.scopes}
    assert all(scopes[entity.scope_id].name == entity.qualified_name for entity in recovered)
    fallback = cpp_analysis(tmp_path,files,'v1',syntax_only=True)
    assert len(fallback.entities) == len(result.entities)
    assert any(item.category == 'symbol_name_unresolved' for item in fallback.diagnostics)
    assert any('<声明名称待确认>' in entity.qualified_name for entity in fallback.entities)
    assert all(call.resolution != 'confirmed' for call in fallback.calls)
