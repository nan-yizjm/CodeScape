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
    assert any(item.category == 'native_index_timeout' for item in result.diagnostics)
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


@pytest.mark.parametrize('status,output,reason', [(1, b'', 'process_exit'),
    (-1073741819, b'', 'process_exit'), (0, b'not-json sentinel-secret', 'invalid_output')])
def test_worker_failure_distinguishes_observed_exit_and_invalid_output_without_echo(tmp_path, monkeypatch,
                                                                                  status, output, reason):
    closed = []

    class Process:
        pid = 99
        returncode = None

        async def communicate(self, payload):
            self.returncode = status
            return output, None

    class Job:
        def attach(self, pid):
            assert pid == 99

        def close(self):
            closed.append(True)

    async def spawn(*args, **kwargs):
        assert kwargs['stderr'] == asyncio.subprocess.DEVNULL
        return Process()

    monkeypatch.setattr(index_process.asyncio, 'create_subprocess_exec', spawn)
    monkeypatch.setattr(index_process, 'WorkerJob', Job)
    with pytest.raises(index_process.IndexProcessError) as caught:
        asyncio.run(index_process._invoke(tmp_path, {'files': {}}, 'v', 'full', 1))
    assert caught.value.reason == reason and closed == [True]
    assert 'sentinel-secret' not in str(caught.value)
    if status:
        assert caught.value.returncode == status and str(status) in str(caught.value)
        assert '未据此推断' in str(caught.value)


def test_isolation_failure_stops_without_unbounded_or_unprotected_fallback(tmp_path, monkeypatch):
    events = []

    class Process:
        pid = 99
        returncode = None

        def kill(self):
            events.append('kill')
            self.returncode = 1

        async def wait(self):
            events.append('wait')

    class Job:
        def attach(self, pid):
            raise RuntimeError('sentinel-secret')

        def close(self):
            events.append('close')

    async def spawn(*args, **kwargs):
        events.append('spawn')
        return Process()

    monkeypatch.setattr(index_process.asyncio, 'create_subprocess_exec', spawn)
    monkeypatch.setattr(index_process, 'WorkerJob', Job)
    with pytest.raises(index_process.IndexProcessError) as caught:
        asyncio.run(index_process.index_snapshot(tmp_path, {'files': {'owned.cpp': {}}}, 'v'))
    assert caught.value.reason == 'isolation_failed' and 'sentinel-secret' not in str(caught.value)
    assert events == ['spawn', 'close', 'kill', 'wait']


def test_cancel_during_failed_spawn_preserves_cancellation_and_never_falls_back(tmp_path, monkeypatch):
    async def scenario():
        started, release = asyncio.Event(), asyncio.Event()
        count = 0

        async def spawn(*args, **kwargs):
            nonlocal count
            count += 1
            started.set()
            await release.wait()
            raise OSError('sentinel-secret')

        monkeypatch.setattr(index_process.asyncio, 'create_subprocess_exec', spawn)
        task = asyncio.create_task(index_process.index_snapshot(tmp_path, {'files': {'owned.cpp': {}}}, 'v'))
        await started.wait()
        task.cancel()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert count == 1

    asyncio.run(scenario())


def test_fallback_failure_identifies_its_stage(tmp_path, monkeypatch):
    async def spawn(*args, **kwargs):
        raise OSError('sentinel-secret')

    monkeypatch.setattr(index_process.asyncio, 'create_subprocess_exec', spawn)
    with pytest.raises(index_process.IndexProcessError) as caught:
        asyncio.run(index_process.index_snapshot(tmp_path, {'files': {'owned.cpp': {}}}, 'v'))
    assert caught.value.reason == 'spawn_failed' and caught.value.mode == 'syntax_fallback'
    assert '语法回退' in str(caught.value) and 'sentinel-secret' not in str(caught.value)
