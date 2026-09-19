import json
from rl_researcher import agent_activity,atomic


def test_only_public_messages_are_projected(tmp_path):
    log=tmp_path/'events.jsonl'
    events=[{'type':'item.completed','item':{'type':'reasoning','text':'PRIVATE'}},
            {'type':'item.completed','item':{'type':'agent_message','text':json.dumps({'rationale':'Published explanation','implementation':'SOURCE'})}}]
    log.write_text('\n'.join(json.dumps(e) for e in events)+'\n{"partial":',encoding='utf-8')
    assert agent_activity.published_messages(log)==['Rationale: Published explanation']


def test_live_and_unknown_usage_are_distinct(tmp_path,monkeypatch):
    monkeypatch.setattr(agent_activity.os,'name','nt')
    monkeypatch.setattr('haws_core.processes.identity',lambda pid:99)
    record=tmp_path/'research/c/invocations/call';record.mkdir(parents=True)
    atomic.write_json(record/'execution.json',{'execution':'live'})
    atomic.write_json(record/'request.json',{'request_id':'0:0:review'})
    atomic.write_json(record/'process.json',{'pid':10,'creation_identity':99})
    campaign={'id':'c','policy':{'max_agents':3},'ai_executions':[
        {'id':'live','role':'reviewer','state':'running'},
        {'id':'lost','role':'worker','state':'running'},
        {'id':'stopped','role':'coordinator','state':'unknown','termination_confirmed':True}]}
    result=agent_activity.project(tmp_path,campaign)
    assert result['live']==1 and result['unconfirmed']==1
    assert result['executions'][0]['task']=='Reviewing the proposed method'
    assert result['executions'][2]['status']=='stopped_usage_unknown'
