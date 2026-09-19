import pytest
import json
from types import SimpleNamespace
from rl_researcher import atomic,deployment
from rl_researcher.campaigns import Campaigns


@pytest.mark.parametrize('change',['none','driver','build','event','unreadable'])
def test_host_gate_rejects_changed_or_unhealthy_host(monkeypatch,change):
    baseline={'since':'2026-09-16T18:08:00','drivers':{'GPU':'1'},'build':{'CurrentBuild':'19045','UBR':6466}}
    observed={'drivers':[{'DeviceName':'GPU','DriverVersion':'2' if change=='driver' else '1'}],
              'build':dict(baseline['build']), 'graphics_errors':int(change=='event')}
    if change=='build': observed['build']['UBR']+=1
    monkeypatch.setattr(deployment.subprocess,'run',lambda *a,**k:SimpleNamespace(
        returncode=int(change=='unreadable'),stdout=json.dumps(observed).encode()))
    if change=='none': deployment.host_preflight(baseline)
    else:
        with pytest.raises(ValueError): deployment.host_preflight(baseline)


def test_authorize_uses_attested_capabilities_and_source_changes_block_start(tmp_path,monkeypatch):
    broker=Campaigns(tmp_path)
    source=tmp_path/'source.py'
    source.write_text('qualified')
    capabilities={k:{'passed':True,'evidence':'Test fixture'} for k in ('isolation','identity','usage_bound','stop','recovery')}
    capabilities['mode']='autonomous'
    atomic.write_json(broker.directory/'runtime-assessment.json',{'ready':True,'fingerprints':{str(source):atomic.digest(source)},'capabilities':capabilities})
    monkeypatch.setattr(deployment,'install',lambda *a:{'qualification':False})
    monkeypatch.setattr(deployment,'preflight',lambda *a:None)
    monkeypatch.setattr(deployment,'host_preflight',lambda *a:None)
    draft=broker.command(dict(request_id='draft',operation='prepare',objective='Test',constraints=['Fixture']))
    request=dict(request_id='authorize',operation='authorize',campaign=draft['campaign'],expected_revision=draft['revision'],runtime={'untrusted':'ignored'})
    authorized=broker.command(request)
    assert broker.command(request)==authorized
    state=broker.store.inspect(broker.owner,draft['campaign'])
    assert state['runtime']==capabilities
    assert not state['sessions']
    source.write_text('unqualified change')
    assert not broker.runtime_status()['ready']
    with pytest.raises(ValueError,match='Runtime checks'):
        broker.command(dict(request_id='start',operation='start',campaign=draft['campaign'],expected_revision=authorized['revision']))


def test_io_errors_are_not_lock_contention(tmp_path):
    from rl_researcher import lock
    with lock.exclusive(tmp_path/'lock'):
        with pytest.raises(lock.LockBusy):
            with lock.exclusive(tmp_path/'lock'): pass
    assert not isinstance(OSError('disk failed'),lock.LockBusy)
