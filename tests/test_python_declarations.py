import pytest
from codescape.analyzers import python_analysis


@pytest.mark.parametrize('import_line,decorator', [('from typing import overload','overload'),
    ('from typing_extensions import overload as signature','signature'), ('import typing as t','t.overload')])
def test_type_overloads_keep_locations_and_scopes_without_runtime_nodes(import_line, decorator):
    source = f'{import_line}\n@{decorator}\ndef parse(value: int): ...\n@{decorator}\ndef parse(value: str): pass\ndef parse(value): return value\n'
    indexed = python_analysis({'owned.py':source}, 'v')
    assert len(indexed.entities)==3 and len(indexed.scopes)==4
    assert [entity.location.start_line for entity in indexed.entities]==[3,5,6]
    assert [entity.visible for entity in indexed.entities]==[False,False,True]
    assert all(entity.hidden_reason=='类型重载声明' for entity in indexed.entities[:2])


def test_method_overload_only_hides_declaration_and_keeps_real_noop():
    indexed = python_analysis({'owned.py': 'import typing as t\nclass Codec:\n @t.overload\n def parse(self, value: int): ...\n def parse(self, value): return value\ndef noop(): pass\n'}, 'v')
    assert [(e.name,e.visible) for e in indexed.entities]==[('parse',False),('parse',True),('noop',True)]


@pytest.mark.parametrize('source', [
    'from typing import overload\noverload = custom\n@overload\ndef candidate(): ...\n',
    'import typing as t\nt.overload = custom\n@t.overload\ndef candidate(): ...\n',
    'from other import overload\n@overload\ndef candidate(): ...\n',
    'from typing import overload\ndef overload(fn): return fn\n@overload\ndef candidate(): ...\n',
    'import typing as t\nclass Codec:\n t = custom\n @t.overload\n def candidate(self): ...\n',
    'from typing import overload\n@overload\ndef candidate(): return action()\n',
])
def test_shadowed_unknown_or_implemented_decorator_is_not_assumed_declaration(source):
    indexed=python_analysis({'owned.py':source},'v')
    assert next(e for e in indexed.entities if e.name=='candidate').visible


def test_protocol_and_abstract_placeholders_hide_but_default_implementations_remain():
    indexed=python_analysis({'owned.py':
        'from typing import Protocol as Contract\nfrom abc import abstractmethod as abstract\n'
        'class Port(Contract):\n def declaration(self): ...\n def default(self): return 1\n'
        'class Base:\n @abstract\n def declaration(self): pass\n'
        ' @abstract\n def default(self): return 2\n'},'v')
    assert [(e.visible,e.hidden_reason) for e in indexed.entities]==[
        (False,'接口方法声明'),(True,None),(False,'抽象方法声明'),(True,None)]


def test_unknown_or_shadowed_protocol_base_does_not_hide_noop():
    for source in ['from other import Protocol\nclass Port(Protocol):\n def noop(self): ...\n',
        'from typing import Protocol\nProtocol=custom\nclass Port(Protocol):\n def noop(self): ...\n']:
        assert python_analysis({'owned.py':source},'v').entities[0].visible
