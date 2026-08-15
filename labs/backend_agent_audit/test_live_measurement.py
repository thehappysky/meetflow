from copy import deepcopy

from live_workflow_probe import CASES, check_report


def test_protocol_report_does_not_pass_wrong_owner_date_or_evidence():
    artifacts = [{'role': role} for role in ('coordinator', 'meeting', 'planner', 'auditor')]
    report = {'summary': '销售将发送报价', 'actions': [{'title': '发送报价', 'owner': '王宁',
              'due': '2026-11-13', 'status': 'todo', 'evidence': ['s1']}]}
    assert all(check_report(CASES[0], report, artifacts, {'s1': {}}).values())
    incorrect = deepcopy(report)
    incorrect['actions'][0].update(owner='未知', due='2026-11-14', evidence=['not-in-snapshot'])
    flags = check_report(CASES[0], incorrect, artifacts, {'s1': {}})
    assert flags['report_present'] and not flags['quote_owner'] and not flags['quote_due']
    assert not flags['action_references_exist']


def test_unconfirmed_suggestion_becoming_action_is_counted_as_error():
    report = {'summary': '演示待定', 'claims': [{'kind': 'suggestion'}],
              'actions': [{'title': '安排演示', 'evidence': ['s1']}]}
    flags = check_report(CASES[2], report, [], {'s1': {}})
    assert flags['uncertainty_preserved'] and not flags['no_firm_demo']


def test_missing_report_stays_in_failure_denominator():
    assert check_report(CASES[0], None, [], {}) == {'report_present': False}


def test_zero_item_meeting_does_not_require_useless_reconciliation_but_real_items_do():
    from summarize_coverage import applicable_role_check
    result = {'artifacts': [{'role':'facts','body':{'mentions':[]}},
                            {'role':'coverage','body':{'mentions':[]}}]}
    assert applicable_role_check(result, True)
    result['artifacts'][1]['body']['mentions'] = [{'task':'发送报价'}]
    assert not applicable_role_check(result, True)
    result['artifacts'].extend([{'role':'reconcile','body':{}}, {'role':'audit','body':{}}])
    assert applicable_role_check(result, True)
