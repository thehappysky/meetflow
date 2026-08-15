import pytest
from meetflow.domain import DomainError
from meetflow.sales_workflow import compare_sales_decisions


@pytest.fixture
def snapshot():
    return {'conversation': {'body': {'spans':[{'id':'s1','text':'实际发言','speaker':'sales'}]}},
            'playbook': {'body': {'rules':[{'id':'promise'}]}}}


def check(suggested='unmet', evidence=None):
    return {'checks':[{'rule_id':'promise',
                      'suggested_status':suggested,'reason':'核对专家判断与原文','evidence':evidence or ['s1']}]}


def test_agreeing_with_a_supported_negative_decision_does_not_create_dispute(snapshot):
    upstream = {'0:product':{'decisions':[{'rule_id':'promise','status':'unmet'}]}}
    result = compare_sales_decisions(check(), snapshot, upstream)
    assert result['challenges'] == []
    assert result['checks'][0]['suggested_status'] == 'unmet'
    assert result['checks'][0]['verdict'] == 'supported'


def test_auditor_must_bind_actual_latest_decision_and_authorized_source(snapshot):
    upstream = {'0:product':{'decisions':[{'rule_id':'promise','status':'unmet'}]},
                '1:product':{'decisions':[{'rule_id':'promise','status':'met'}]}}
    with pytest.raises(DomainError, match='不属于'):
        compare_sales_decisions(check('unknown',['foreign']), snapshot, upstream)
    result = compare_sales_decisions(check('unknown'), snapshot, upstream)
    assert result['challenges'][0]['rule_id'] == 'promise'
    assert result['checks'][0]['upstream_status'] == 'met'


def test_missing_upstream_rule_or_duplicate_checks_cannot_pass(snapshot):
    with pytest.raises(DomainError, match='上游'):
        compare_sales_decisions(check(), snapshot, {})
    value = check()
    value['checks'] *= 2
    with pytest.raises(DomainError, match='遗漏'):
        compare_sales_decisions(value, snapshot, {'0:product':{'decisions':[{'rule_id':'promise','status':'unmet'}]}})
