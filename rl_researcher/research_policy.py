"""Research admission checks; historical recommendations never grant authority."""
import json


def authority_context(campaign):
    return {
        'revision': campaign['revision'],
        'eligibility': campaign.get('eligibility', {}),
        'incidents': [{k: row.get(k) for k in (
            'id', 'category', 'scope', 'status', 'reason', 'facts',
            'blocked_operations', 'next_action', 'resolved_at', 'resolution_evidence')}
            for row in campaign.get('incidents', [])],
        'rule': 'These current control records supersede historical recommendations about restrictions. '
                'Resolved incidents are not active restrictions. Preserve old conclusions as evidence. '
                'Do not reconstruct platform admission checks in experiment code or invent missing '
                'platform guarantees. Report a specific unresolved platform prerequisite to the '
                'supervisor instead of repeatedly running an administrative no-op.',
    }


def context_text(campaign):
    return '\nCURRENT AUTHORITATIVE CONTROL STATE:\n'+json.dumps(authority_context(campaign))


DESIGN_SCHEMA = ('Include experiment_kind (learning or diagnostic), measurements (nonempty list '
                 'of concrete observed outputs), and decision_rule (how different observed outcomes '
                 'would change the research decision). A diagnostic also needs incident_id referencing '
                 'an unresolved incident. Learning experiments must execute learner/environment '
                 'interactions. Inventory-only, deliberately raising an error, and immediately stopping '
                 'are not publication tests or learning experiments. ')


def validate_design(value, campaign):
    kind=value.get('experiment_kind')
    if kind not in {'learning','diagnostic'}:
        raise ValueError('Declare experiment_kind learning or diagnostic and an executable test')
    if not isinstance(value.get('measurements'),list) or not value['measurements'] or any(
            not isinstance(x,str) or not x.strip() for x in value['measurements']):
        raise ValueError('Name the concrete measurements this execution will collect')
    if not isinstance(value.get('decision_rule'),str) or not value['decision_rule'].strip():
        raise ValueError('Explain how different measured outcomes change a research decision')
    if kind=='diagnostic':
        incident=next((i for i in campaign.get('incidents',[]) if i['id']==value.get('incident_id')),None)
        if not incident or incident['status'] in {'resolved','task_abandoned'}:
            raise ValueError('Diagnostic requires a current unresolved incident; return to eligible learning work')
        if incident['category'] in {'capacity','resource','isolation','integrity','host','authority','accounting'}:
            raise ValueError('Platform/accounting diagnosis belongs to the supervisor, not a research experiment')
        if not campaign.get('rethink') or campaign['rethink'].get('status')!='active':
            raise ValueError('Diagnostic execution requires an active bounded investigation')


def validate_review(review):
    """An independent pass must explicitly attest executability and answerability."""
    if review.get('result')!='pass':
        return
    checks=review.get('answerability',{})
    if not isinstance(checks,dict) or any(checks.get(k) is not True for k in (
            'executes_test','collects_measurements','distinguishes_outcomes')):
        raise ValueError('Passing method review must confirm test execution, measurements and distinguishable outcomes')
    if not isinstance(checks.get('code_evidence'),str) or not checks['code_evidence'].strip():
        raise ValueError('Passing review must cite the executable functions implementing the test and measurements')


def remaining_calls(campaign):
    session=campaign['sessions'][-1]['id']
    return campaign['policy']['ai_call_limit']-1-sum(e['session']==session for e in campaign['ai_executions'])


def required_calls(phase):
    # Reserve the normal path through independent conclusion review before starting a cycle.
    return {'plan':5,'implement':4,'prepare':3,'review':3,'dispatch':2,'execute':2,
            'recover':2,'assess':2,'review_conclusion':1}.get(phase,1)
