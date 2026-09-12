"""Public defaults and explicit opt-out, without model calls or host config."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]


def probe(tmp_path, variant, config=None, env=None):
    home = tmp_path/'home'
    home.mkdir(exist_ok=True)
    if config is not None:
        (home/'.tellonce.config.json').write_text(config, encoding='utf-8')
    code = '''import sys,json
sys.path.insert(0,sys.argv[1])
import memory_upsert
print(json.dumps({'enabled':memory_upsert.hooks_enabled()}))
'''
    process = subprocess.run([sys.executable, '-I', '-B', '-c', code, str(ROOT/variant)],
        cwd=home, env={**{k:v for k,v in os.environ.items() if k not in {
            'PT_MEMORY_UPSERT_ENABLED','B5_MEMORY_UPSERT_ENABLED'}},
            'HOME':str(home), 'USERPROFILE':str(home), **(env or {})},
        capture_output=True, text=True, check=True, timeout=20)
    return json.loads(process.stdout)['enabled']


@pytest.mark.parametrize('variant',['lib','codex/shared_lib','copilot/lib'])
@pytest.mark.parametrize('config,env,expected',[
    (None,{},True), ('{}',{},True),
    ('{"memory_upsert_enabled":false}',{},False),
    ('{"memory_upsert_enabled":"false"}',{},False),
    (None,{'PT_MEMORY_UPSERT_ENABLED':'0'},False),
    (None,{'B5_MEMORY_UPSERT_ENABLED':'0'},False),
    ('{"memory_upsert_enabled":false}',{'PT_MEMORY_UPSERT_ENABLED':'1'},True),
    (None,{'PT_MEMORY_UPSERT_ENABLED':'0','B5_MEMORY_UPSERT_ENABLED':'1'},False),
    ('broken-json',{},False), ('[]',{},False),
])
def test_public_default_and_explicit_opt_out(tmp_path,variant,config,env,expected):
    assert probe(tmp_path,variant,config,env) is expected


def test_default_prompt_queues_and_explicit_disable_stops_it(tmp_path, monkeypatch):
    sys.path.insert(0, str(ROOT/'lib'))
    import memory_upsert
    import memory_upsert_hook
    from unittest import mock
    config = tmp_path/'config.json'
    monkeypatch.setattr(memory_upsert, 'CONFIG_PATH', config)
    for name in ('PT_MEMORY_UPSERT_ENABLED','B5_MEMORY_UPSERT_ENABLED',
                 'B5_RETRIEVE_RECURSION_GUARD','PT_MEMORY_UPSERT_DISABLED'):
        monkeypatch.delenv(name, raising=False)
    with mock.patch.object(memory_upsert_hook.pt_platform, 'is_child_session', return_value=False), \
         mock.patch.object(memory_upsert, 'enqueue', return_value={'status':'queued'}) as enqueue:
        event = {'session_id':'new-user', 'turn_id':'first', 'prompt':'Please keep your answers short.'}
        assert memory_upsert_hook.enqueue_from_hook(event, 'prompt')['status'] == 'queued'
        assert enqueue.call_args.kwargs['spawn_worker'] is True
        assert enqueue.call_args.kwargs['detect_signal'] is True
        status = memory_upsert.configure_hooks()
        assert status['status'] == 'enabled' and status['source'] == 'default'
        assert not config.exists()  # Status lookup does not write user settings.
        assert memory_upsert.configure_hooks(False)['status'] == 'disabled'
        enqueue.reset_mock()
        assert memory_upsert_hook.enqueue_from_hook(event, 'prompt')['status'] == 'disabled'
        enqueue.assert_not_called()


@pytest.mark.parametrize('setting,expected',[(None,True),(False,False),('false',False),(True,True)])
def test_copilot_displays_same_default_as_runtime(tmp_path,monkeypatch,setting,expected):
    import importlib.util
    import io
    from contextlib import redirect_stdout
    for name in ('PT_MEMORY_UPSERT_ENABLED','B5_MEMORY_UPSERT_ENABLED'):
        monkeypatch.delenv(name,raising=False)
    def module(name):
        spec=importlib.util.spec_from_file_location('default_test_'+name,ROOT/'copilot/lib'/(name+'.py'))
        value=importlib.util.module_from_spec(spec);spec.loader.exec_module(value)
        return value
    dashboard,mode=module('dashboard'),module('pt_mode')
    config={} if setting is None else {'memory_upsert_enabled':setting}
    path=tmp_path/'config.json'
    if setting is not None:path.write_text(json.dumps(config))
    monkeypatch.setattr(dashboard.path_config,'CONFIG_PATH',str(path))
    assert dashboard._memory_upsert_enabled() is expected
    output=io.StringIO()
    with redirect_stdout(output):mode._print_status(config)
    assert 'memory upsert judge  = '+str(expected) in output.getvalue()
