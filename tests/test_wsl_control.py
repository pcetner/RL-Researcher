from rl_researcher import atomic, wsl_control


def test_reconcile_preserves_supervisor_identity_and_saves_separate_request(tmp_path,monkeypatch):
    monkeypatch.setattr(wsl_control.Path,'home',lambda:tmp_path)
    root = tmp_path/'haws-integration/acceptance/fixture'
    directory = root/'.research/executions/run1'
    directory.mkdir(parents=True)
    supervisor = {'pid':123,'started':0}
    atomic.write_json(directory/'supervisor.json',supervisor)
    monkeypatch.setattr(wsl_control.checkpoint,'recover_committed',lambda path:None)
    calls = []
    monkeypatch.setattr(wsl_control.runner,'reconcile',lambda path:calls.append(path))
    monkeypatch.setattr(wsl_control.runner,'state',lambda path:{'state':'Failed'})
    request = {'root':str(root),'execution':'run1','operation':'reconcile','request_id':'one'}
    assert wsl_control.handle(request)=={'state':'Failed'}
    assert wsl_control.handle(request)=={'state':'Failed'}
    assert len(calls)==1
    assert atomic.read_json(directory/'supervisor.json')==supervisor
