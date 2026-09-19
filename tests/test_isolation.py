import time

from rl_researcher import atomic, lock, runner, runlog


def test_reconciliation_does_not_assert_termination_of_live_worker(tmp_path, monkeypatch):
    manifest = {'id':'synthetic', 'started':time.time(), 'isolation':{'kind':'bubblewrap-v1'},
                'definition':{'trials':[{'id':'trial','label':'Synthetic'}]}}
    atomic.write_json(tmp_path/'manifest.json', manifest)
    runlog.Journal(tmp_path).append('state', {'worker_pid':123, 'worker_start_ticks':'10'})
    monkeypatch.setattr(lock,'linux_process_identity',lambda pid:{'state':'S','start_ticks':'10'})
    runner.reconcile(tmp_path)
    state = runner.state(tmp_path)
    assert state['state']=='Stopping' and state['worker_exited'] is False
    assert 'unconfirmed' in state['error']


def test_reused_pid_does_not_hold_old_execution_open(tmp_path, monkeypatch):
    manifest = {'id':'synthetic', 'started':time.time(), 'isolation':{'kind':'bubblewrap-v1'},
                'definition':{'trials':[{'id':'trial','label':'Synthetic'}]}}
    atomic.write_json(tmp_path/'manifest.json', manifest)
    runlog.Journal(tmp_path).append('state', {'worker_pid':123, 'worker_start_ticks':'10'})
    monkeypatch.setattr(lock,'linux_process_identity',lambda pid:{'state':'S','start_ticks':'20'})
    runner.reconcile(tmp_path)
    state = runner.state(tmp_path)
    assert state['state']=='Failed' and state['worker_exited'] is True
