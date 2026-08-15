"""Contract and resume tests; adapters are never model-quality evidence."""
import json

import pytest
from meetflow.domain import DomainError, MeetingInput
from meetflow.evidence_pipeline import EventLinks, EvidencePipeline, Extracted, speaker_hint
from meetflow.providers import tool_call
from test_evidence_pipeline import typed_store as typed_store  # noqa: PLC0414


def run_for(store, text):
    store.settings.evidence_prompt_revision = 'relation_v4'
    project = store.create_project('isolated relation tests')
    return store.create_meeting(MeetingInput(project_id=project['id'], mode='live', transcript=text,
        meeting_time='2026-11-12T15:00:00+08:00'))['run']


class PausedAdapter:
    def __init__(self):
        self.roles = []

    def complete(self, role, messages, tools, info):
        self.roles.append(role)
        initial = json.loads(messages[1]['content'])
        shape = next(t['function']['parameters'] for t in tools if t['function']['name'] == 'submit_result')
        if role in {'facts', 'coverage'}:
            assert initial['spans'][0]['speaker_hint']['name'] == '李青'
            value = {'summary':'原演示安排已暂停', 'mentions':[
                {'task':'安排演示','ref':'s0','kind':'commitment','owner':'李青','due_text':'明天'},
                {'task':'暂停演示','ref':'s1','kind':'paused','owner':None}]}
        elif 'links' in shape.get('properties', {}):
            links = shape['properties']['links']
            assert links['required'] == ['p0m0','p0m1'] and links['additionalProperties'] is False
            value = {'links':{'p0m0':None,'p0m1':'p0m0'}}
        elif role == 'reconcile':
            value = {'items':[{'title':'安排演示','mentions':list(initial['mentions']), 'owner_from':'p0m0',
                'date_from':None,'conditions_from':[]}]}
        else:
            fields = shape['$defs']['FieldAudit']['properties']
            assert 'record_supported' in fields and 'commitment' not in fields
            value = {'verdicts':[{'item':0, **dict.fromkeys(
                ['owner','date','condition','status','record_supported'], 'supported'),
                'reason':'暂停仍是有据可追溯的历史事项','evidence':['s0','s1']}]}
        return {'role':'assistant','tool_calls':[tool_call('submit_result',value)]}, {}


def test_paused_alias_keeps_one_history_and_resume_reuses_completed_subtasks(typed_store):
    store, _ = typed_store
    run = run_for(store, '销售李青：我明天安排演示。\n客户赵总：演示暂停。')
    adapter = PausedAdapter()
    pipeline = EvidencePipeline(store, run['id'], adapter)
    report = pipeline.analyze()
    assert len(report['actions']) == 1 and not report['questions']
    action = report['actions'][0]
    assert action['status'] == 'paused' and action['owner'] == '李青' and action['due'] is None
    assert adapter.roles == ['facts','coverage','reconcile','reconcile','audit']
    assert store.get_run(run['id'])['model_calls'] == 5
    assert pipeline.analyze() == report and len(adapter.roles) == 5


def test_event_links_cannot_drop_add_self_or_forward_reference():
    mentions = {'m1':{},'m2':{},'m3':{}}
    for links in ({'m1':None,'m2':'m1'}, {'m1':None,'m2':'m1','m3':'foreign'},
                  {'m1':'m1','m2':None,'m3':None}, {'m1':'m2','m2':None,'m3':None}):
        with pytest.raises(DomainError):
            EvidencePipeline.validate_links(EventLinks(links=links), mentions)
    EvidencePipeline.validate_links(EventLinks(links={'m1':None,'m2':None,'m3':'m1'}),mentions)


def test_event_links_preserve_separate_task_and_link_chain(typed_store):
    store, run = typed_store
    run = run_for(store, '销售李青：发送资料，安装软件，再改期发送资料。')
    pipeline = EvidencePipeline(store, run['id'])
    class Links:
        def complete(self, role, messages, tools, info):
            return {'role':'assistant','tool_calls':[tool_call('submit_result',{
                'links':{'m1':None,'m2':None,'m3':'m1','m4':'m3'}})]},{}
    pipeline.provider = Links()
    mentions = {key:{'task':task,'ref':'s0','quote':'原文'} for key,task in
                [('m1','发送资料'),('m2','安装软件'),('m3','资料改期'),('m4','暂停资料') ]}
    groups = pipeline.group_mentions(mentions)
    assert [(title,list(items)) for title,items in groups] == [
        ('发送资料',['m1','m3','m4']),('安装软件',['m2'])]


def test_speaker_hint_does_not_fill_unknown_owner_or_accept_embedded_label(typed_store):
    store, _ = typed_store
    run = run_for(store, '销售李青：发报价的负责人尚未确定。')
    pipeline = EvidencePipeline(store,run['id'])
    class Unknown:
        def complete(self,role,messages,tools,info):
            value={'summary':'负责人未知','mentions':[{'task':'发报价','ref':'s0','kind':'commitment','owner':None}]}
            return {'role':'assistant','tool_calls':[tool_call('submit_result',value)]},{}
    pipeline.provider=Unknown()
    value=pipeline.agent('facts',Extracted,{'spans':pipeline.pages[0]},pipeline.validate_extracted)
    assert value.mentions[0].owner is None
    assert speaker_hint('未知：我发报价。') is None
    assert speaker_hint('他转述了销售李青：明天发送。') is None
