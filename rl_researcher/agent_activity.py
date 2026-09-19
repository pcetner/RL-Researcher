"""Read-only projection of execution receipts and published agent messages."""
import json
import os
from . import atomic

TASKS={'plan':'Choosing the next research question','implement':'Preparing the proposed method',
       'review':'Reviewing the proposed method','assess':'Assessing experiment evidence',
       'review_conclusion':'Reviewing the conclusion','investigation':'Investigating a plateau'}


def published_messages(path):
    if not path.is_file():
        return []
    # Logs may be written concurrently. Ignore partial records and never expose
    # reasoning items, prompts, credentials, tools, or generated source code.
    with path.open('rb') as stream:
        size=stream.seek(0,2)
        stream.seek(max(0,size-2*1024*1024))
        data=stream.read(2*1024*1024)
    lines=data.splitlines()
    if size>2*1024*1024:
        lines=lines[1:]
    messages=[]
    for line in lines:
        try:
            event=json.loads(line)
            item=event.get('item',{})
            if event.get('type')!='item.completed' or item.get('type')!='agent_message':
                continue
            text=item.get('text','')
            try:
                value=json.loads(text.strip().removeprefix('```json').removesuffix('```').strip())
            except (ValueError,AttributeError):
                value=None
            if isinstance(value,dict):
                text='\n'.join(f'{key.replace("_"," ").capitalize()}: {value[key]}' for key in
                    ('question','rationale','finding','limitations','next_decision','result','findings','alternatives') if key in value)
            if isinstance(text,str) and text:
                messages.append(text[:4000])
        except (ValueError,TypeError,AttributeError):
            continue
    return messages[-2:]


def project(directory,campaign):
    entries={e['id']:dict(e,task='Recorded agent execution',messages=[]) for e in campaign.get('ai_executions',[])}
    for record in (directory/'research'/campaign['id']/'invocations').glob('*'):
        try:
            receipt=atomic.read_json(record/'execution.json')
            entry=entries.get(receipt.get('execution'))
            if not entry:
                continue
            request=atomic.read_json(record/'request.json')
            phase=request.get('request_id','').split(':')[-1]
            entry['task']=TASKS.get(phase,'Recorded agent execution')
            entry['messages']=published_messages(record/'events.jsonl')
            entry['message_observed_at']=(record/'events.jsonl').stat().st_mtime if (record/'events.jsonl').exists() else None
            if entry['state'] in {'running','unknown'} and not entry.get('termination_confirmed'):
                process=atomic.read_json(record/'process.json')
                if os.name=='nt':
                    from haws_core.processes import identity
                    entry['live']=bool(process.get('creation_identity') and identity(process['pid'])==process['creation_identity'])
        except (OSError,ValueError,KeyError):
            continue
    rows=[]
    for entry in entries.values():
        state=entry['state']
        if entry.get('live'): status='live'
        elif state in {'running','unknown'} and not entry.get('termination_confirmed'): status='unconfirmed'
        elif state=='unknown': status='stopped_usage_unknown'
        else: status=state
        rows.append({k:entry.get(k) for k in ('id','role','task','started_at','ended_at','deadline','messages','message_observed_at')}|{'status':status})
    return {'live':sum(r['status']=='live' for r in rows),
            'unconfirmed':sum(r['status']=='unconfirmed' for r in rows),
            'limit':campaign['policy']['max_agents'],'executions':rows}
