from codescape.analyzers import cpp_analysis
from codescape.context_tools import SnapshotTools
from codescape.macro_context import macro_reads


def source():
    return {'owned.cpp': 'void owned() { WRAP_TRY { throw 1; } WRAP_CATCH }\n',
            'owned.hpp': '#ifndef OWNED_HEADER\n#define OWNED_HEADER\n#ifdef NO_EXCEPTIONS\n#define WRAP_TRY\n#define WRAP_CATCH\n#else\n#define WRAP_TRY try\n#define WRAP_CATCH catch (...) { throw; }\n#endif\n#endif\n'}


def test_original_macro_condition_branches_are_supplied_without_new_entities(tmp_path):
    files = source()
    analysis = cpp_analysis(tmp_path, files, 'v', syntax_only=True)
    requests = macro_reads(analysis, files, {'owned.cpp': (1, 1)})
    assert len(requests) == 1
    reader = SnapshotTools(analysis, files)
    item = reader.read(requests)[0]
    assert item['path'] == 'owned.hpp' and item['start_line'] == 1 and item['end_line'] == 10
    assert item['text'] == files['owned.hpp']
    assert reader.read_ranges == {'owned.hpp': [(1, 10)]}
    assert all(entity.location.path == 'owned.cpp' for entity in analysis.entities if entity.visible)
    assert not macro_reads(analysis, files, {'owned.cpp': (1, 1), 'owned.hpp': (1, 10)})


def test_undef_and_oversized_conditional_are_not_assumed(tmp_path):
    files = source()
    files['owned.hpp'] += '#undef WRAP_TRY\n#undef WRAP_CATCH\n'
    analysis = cpp_analysis(tmp_path, files, 'v', syntax_only=True)
    assert not macro_reads(analysis, files, {'owned.cpp': (1, 1)})
    files = source()
    files['owned.hpp'] = '#ifdef MODE\n'+'// filler\n'*201+'#define WRAP_TRY try\n#define WRAP_CATCH catch (...) { throw; }\n#endif\n'
    analysis = cpp_analysis(tmp_path, files, 'v', syntax_only=True)
    assert not macro_reads(analysis, files, {'owned.cpp': (1, 1)})


def test_comment_fake_directives_and_other_language_are_not_auto_reads(tmp_path):
    files = source()
    files['fake.hpp'] = '/*\n#define WRAP_TRY try\n#define WRAP_CATCH catch (...) { throw; }\n*/\n'
    analysis = cpp_analysis(tmp_path, files, 'v', syntax_only=True)
    assert all(request.reference.path != 'fake.hpp' for request in macro_reads(analysis, files, {'owned.cpp': (1, 1)}))
    assert not macro_reads(analysis, files, {'owned.hpp': (1, 10)})
