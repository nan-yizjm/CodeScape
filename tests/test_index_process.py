import asyncio

import pytest
from codescape import index_process
from codescape.snapshots import create_snapshot, read_snapshot


def test_actual_native_timeout_preserves_syntax_and_other_languages(tmp_path):
    source=tmp_path/'source'
    source.mkdir()
    (source/'owned.cpp').write_text('int target(){return 1;}\nint caller(){return target();}\n',encoding='utf-8')
    (source/'owned.py').write_text('raise RuntimeError("不得执行")\ndef pyTarget():return 1\ndef pyCaller():return pyTarget()\n',encoding='utf-8')
    manifest=create_snapshot(source,tmp_path/'project')
    root=tmp_path/'project/snapshots'/manifest['id']
    original=read_snapshot(root,manifest)
    result=asyncio.run(index_process.index_snapshot(root,manifest,'v',timeout=0.001))
    assert len([entity for entity in result.entities if entity.visible]) == 4
    assert any(call.resolution == 'confirmed' and call.target_name == 'pyTarget' for call in result.calls)
    assert not any(call.resolution == 'confirmed' and call.target_name == 'target' for call in result.calls)
    assert any(call.expression == 'target' and call.resolution == 'unresolved' for call in result.calls)
    assert any(item.category == 'native_index_incomplete' for item in result.diagnostics)
    assert read_snapshot(root,manifest) == original


def test_cancel_does_not_start_fallback(monkeypatch,tmp_path):
    modes=[]
    async def cancelled(root,manifest,version,mode,timeout):
        modes.append(mode)
        raise asyncio.CancelledError
    monkeypatch.setattr(index_process,'_invoke',cancelled)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(index_process.index_snapshot(tmp_path,{'files':{'owned.cpp':{}}},'v'))
    assert modes == ['full']


def test_two_failures_stop_without_loop_or_fake_success(monkeypatch,tmp_path):
    modes=[]
    async def failed(root,manifest,version,mode,timeout):
        modes.append(mode)
        raise ValueError('固定解析故障')
    monkeypatch.setattr(index_process,'_invoke',failed)
    with pytest.raises(ValueError,match='固定解析故障'):
        asyncio.run(index_process.index_snapshot(tmp_path,{'files':{'owned.cpp':{}}},'v'))
    assert modes == ['full','syntax_fallback']
