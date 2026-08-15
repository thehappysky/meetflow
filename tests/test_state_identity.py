"""Typed adapters test contracts/recovery, never count as real model accuracy."""
import copy
import json

import pytest
from meetflow.domain import DomainError, MeetingInput
from meetflow.evidence_pipeline import EvidencePipeline, Extracted, IdentityPlan, speaker_hint
from meetflow.providers import tool_call
from test_evidence_pipeline import typed_store as typed_store  # noqa: PLC0414


def run_for(store, text, revision='review_v3'):
    store.settings.evidence_prompt_revision = revision
    project = store.create_project('isolated identity tests')
    return store.create_meeting(MeetingInput(project_id=project['id'], mode='live', transcript=text,
        meeting_time='2026-11-12T15:00:00+08:00'))['run']


class PausedAdapter:
    def __init__(self):
        self.roles = []

    def complete(self, role, messages, tools, info):
        self.roles.append(role)
        initial = json.loads(messages[1]['content'])
        if role in {'facts', 'coverage'}:
            value = {'summary':'原演示安排已暂停', 'mentions':[
                {'task':'安排演示','ref':'s0','kind':'commitment','owner_basis':'speaker','named_owner':None,'due_text':'明天'},
                {'task':'暂停演示','ref':'s1','kind':'paused','owner_basis':'unknown','named_owner':None}]}
        elif any('groups' in t['function']['parameters'].get('properties', {}) for t in tools):
            value = {'groups':[{'title':'安排演示','mentions':list(initial['mentions'])}]}
        elif role == 'reconcile':
            value = {'items':[{'title':'安排演示','mentions':list(initial['mentions']), 'owner_from':'p0m0',
                'date_from':None if 'previous' in initial else 'p0m0','conditions_from':[]}]}
        else:
            assert 'resolved_items' not in initial and 'items' not in initial
            value = {'reviews':[{'item':0,'state':'paused','owner_from':'p0m0','date_from':None,
                'conditions_from':[],'reason':'暂停保留历史承担人，无当前期限','evidence':['s0','s1']}]}
        return {'role':'assistant','tool_calls':[tool_call('submit_result',value)]}, {}


def test_paused_alias_is_one_history_and_blind_review_reused_for_pointer_repair(typed_store):
    store, _ = typed_store
    run = run_for(store, '销售李青：我明天安排演示。\n客户赵总：演示暂停。')
    adapter = PausedAdapter()
    pipeline = EvidencePipeline(store, run['id'], adapter)
    report = pipeline.analyze()
    assert len(report['actions']) == 1 and not report['questions']
    action = report['actions'][0]
    assert action['status'] == 'paused' and action['owner'] == '李青' and action['due'] is None
    assert adapter.roles.count('audit') == 1
    assert store.get_run(run['id'])['model_calls'] == 6
    assert pipeline.analyze() == report and len(adapter.roles) == 6


def test_actor_selection_preserves_named_other_and_does_not_assign_customer_request(typed_store):
    store, _ = typed_store
    run = run_for(store, '销售李青：我发报价，陈安安排演示。\n客户赵总：请改期。')
    pipeline = EvidencePipeline(store, run['id'])
    class Actors:
        def complete(self, role, messages, tools, info):
            value = {'summary':'原文责任分工', 'mentions':[
                {'task':'发报价','ref':'s0','kind':'commitment','owner_basis':'speaker','named_owner':None},
                {'task':'安排演示','ref':'s0','kind':'commitment','owner_basis':'named','named_owner':'陈安'},
                {'task':'发报价','ref':'s1','kind':'revision','owner_basis':'unknown','named_owner':None}]}
            return {'role':'assistant','tool_calls':[tool_call('submit_result',value)]}, {}
    pipeline.provider = Actors()
    result = pipeline.agent('facts',Extracted,{'spans':pipeline.pages[0]},pipeline.validate_extracted)
    assert [m.owner for m in result.mentions] == ['李青','陈安',None]
    assert speaker_hint('未知：我发报价。') is None
    assert speaker_hint('他转述了销售李青：明天发送。') is None


def test_identity_partition_cannot_drop_duplicate_or_cross_source_mentions():
    mentions = {'m1':{'task':'安排演示'},'m2':{'task':'暂停演示'}}
    for ids in (['m1'],['m1','m1','m2'],['m1','foreign']):
        with pytest.raises(DomainError):
            EvidencePipeline.validate_identity(IdentityPlan(groups=[{'title':'安排演示','mentions':ids}]),mentions)


def test_unverified_speaker_hint_does_not_overwrite_explicit_unknown_owner(typed_store):
    store, _ = typed_store
    run = run_for(store, '销售李青：发报价的负责人尚未确定。')
    pipeline = EvidencePipeline(store,run['id'])
    class Unknown:
        def complete(self,role,messages,tools,info):
            value={'summary':'负责人未知','mentions':[{'task':'发报价','ref':'s0','kind':'commitment',
                'owner_basis':'unknown','named_owner':None}]}
            return {'role':'assistant','tool_calls':[tool_call('submit_result',copy.deepcopy(value))]},{}
    pipeline.provider=Unknown()
    value=pipeline.agent('facts',Extracted,{'spans':pipeline.pages[0]},pipeline.validate_extracted)
    assert value.mentions[0].owner is None
