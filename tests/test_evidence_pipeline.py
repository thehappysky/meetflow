import json

import pytest
from meetflow.config import Settings
from meetflow.database import make_database
from meetflow.domain import DomainError, MeetingInput
from meetflow.evidence_pipeline import Audited, EvidencePipeline, Extracted, Reconciled, pages_for
from meetflow.orchestration import RunDriver
from meetflow.providers import tool_call
from meetflow.store import Store


@pytest.fixture
def typed_store(tmp_path):
    settings = Settings(data_dir=tmp_path, start_worker=False, meeting_workflow='evidence_v1',
                        model_name='test-adapter', model_api_key='test-only')
    engine, sessions = make_database(settings)
    store = Store(sessions, settings)
    project = store.create_project('isolated typed workflow')
    run = store.create_meeting(MeetingInput(project_id=project['id'], mode='live',
        transcript='销售王宁：我明天发送报价。', meeting_time='2026-11-12T15:00:00+08:00'))['run']
    yield store, run
    engine.dispose()


class TypedAdapter:
    def complete(self, role, messages, tools, info):
        initial = json.loads(messages[1]['content'])
        if role in {'facts', 'coverage'}:
            span = initial['spans'][0]
            value = {'summary': '王宁承诺明天发送报价', 'mentions': [{'task': '发送报价', 'quote': span['text'],
                'ref': span['id'], 'kind': 'commitment', 'owner': '王宁', 'due_text': '明天', 'condition': None}]}
        elif role == 'reconcile':
            value = {'items': [{'title': '发送报价', 'mentions': ['p0m0'], 'owner_from': 'p0m0',
                'date_from': 'p0m0', 'conditions_from': [], 'status': 'todo', 'confirmed': True}]}
        else:
            value = {'reviews': [{'item': 0, 'fields': ['owner', 'date', 'condition', 'status', 'commitment'],
                     'verdict': 'supported', 'reason': '依据原文核查通过',
                     'evidence': initial['resolved_items'][0]['evidence']}]}
        return {'role': 'assistant', 'tool_calls': [tool_call('submit_result', value)]}, {'prompt_tokens': 10, 'completion_tokens': 5}


def test_typed_three_agents_create_dated_report_without_formal_business_writes(typed_store):
    store, run = typed_store
    driver = RunDriver(store, TypedAdapter())
    try:
        driver.process(run['id'])
        result = store.get_run(run['id'])
        assert result['status'] == 'waiting_review', result['error']
        assert result['model_calls'] == 4
        body = store.latest_report(run['id'])['body']
        assert body['mode'] == 'live' and body['workflow'] == 'evidence_v1'
        assert body['actions'][0]['owner'] == '王宁'
        assert body['actions'][0]['due'] == '2026-11-13'
        assert {a['role'] for a in store.artifacts(run['id'])} == {'facts', 'coverage', 'reconcile', 'audit'}
    finally:
        driver.close()


def test_quote_validation_rejects_fabricated_owner_date_and_cross_run_evidence(typed_store):
    store, run = typed_store
    pipeline = EvidencePipeline(store, run['id'])
    ref = next(iter(pipeline.evidence))
    for updates in ({'ref': 'foreign-source'}, {'quote': '已签约'}, {'owner': '李总'}, {'due_text': '下周一'}):
        value = {'task': '发送报价', 'quote': '我明天发送报价', 'ref': ref, 'kind': 'commitment', **updates}
        with pytest.raises(DomainError):
            pipeline.validate_extracted(Extracted(summary='原文', mentions=[value]))


def test_group_rejects_cross_item_condition_missing_owner_and_suggestion_promotion():
    mentions = {'q': {'owner': '王宁', 'due_text': '明天', 'condition': None, 'kind': 'commitment'},
                't': {'owner': None, 'due_text': None, 'condition': '法务通过后', 'kind': 'condition'}}
    quote = {'title': '发送报价', 'mentions': ['q'], 'owner_from': 'q', 'date_from': 'q',
             'conditions_from': ['t'], 'status': 'todo', 'confirmed': True}
    trial = {'title': '安排试用', 'mentions': ['t'], 'owner_from': None, 'date_from': None,
             'conditions_from': ['t'], 'status': 'blocked', 'confirmed': True}
    with pytest.raises(DomainError, match='条件'):
        EvidencePipeline.validate_groups(Reconciled(items=[quote, trial]), mentions)
    quote.update(conditions_from=[], owner_from=None)
    with pytest.raises(DomainError, match='负责人'):
        EvidencePipeline.validate_groups(Reconciled(items=[quote, trial]), mentions)
    mentions['q'].update(kind='suggestion', owner=None)
    result = Reconciled(items=[quote, trial])
    EvidencePipeline.validate_groups(result, mentions)
    assert not result.items[0].confirmed
    assert result.items[1].status == 'blocked'


def test_pages_preserve_all_text_even_one_oversized_span():
    spans = [{'id': 's1', 'text': '前' * 9000}, {'id': 's2', 'text': '末' * 5000}]
    pages = pages_for(spans, 6000)
    assert len(pages) == 3
    for span in spans:
        assert ''.join(part['text'] for page in pages for part in page if part['id'] == span['id']) == span['text']
    assert max(sum(len(p['text']) for p in page) for page in pages) <= 6000


def test_completed_agent_artifacts_are_reused_after_interruption(typed_store):
    store, run = typed_store
    pipeline = EvidencePipeline(store, run['id'], TypedAdapter())
    first = pipeline.analyze()
    calls = store.get_run(run['id'])['model_calls']
    assert pipeline.analyze() == first
    assert store.get_run(run['id'])['model_calls'] == calls


def test_coverage_ablation_remains_multi_agent_and_freezes_configuration(typed_store):
    store, original = typed_store
    store.settings.evidence_coverage = False
    run = store.create_meeting(MeetingInput(project_id=original['project_id'], mode='live',
        transcript='销售王宁：我明天发送报价。', meeting_time='2026-11-12T15:00:00+08:00'))['run']
    store.settings.evidence_coverage = True
    body = EvidencePipeline(store, run['id'], TypedAdapter()).analyze()
    assert not body['coverage_check']
    assert {a['role'] for a in store.artifacts(run['id'])} == {'facts', 'reconcile', 'audit'}
    assert store.get_run(run['id'])['model_calls'] == 3


def test_mid_agent_messages_and_consumed_budget_survive_retry(typed_store):
    store, run = typed_store

    class Interrupted(TypedAdapter):
        facts_calls = 0

        def complete(self, role, messages, tools, info):
            if role == 'facts':
                self.facts_calls += 1
                if self.facts_calls == 1:
                    return {'role': 'assistant', 'tool_calls': [tool_call('read_evidence', {'ref': 's0'})]}, {}
                raise RuntimeError('injected provider interruption')
            return super().complete(role, messages, tools, info)

    with pytest.raises(RuntimeError, match='interruption'):
        EvidencePipeline(store, run['id'], Interrupted()).analyze()
    assert store.get_run(run['id'])['model_calls'] == 2
    tool_calls = store.get_run(run['id'])['tool_calls']

    class Resumed(TypedAdapter):
        def complete(self, role, messages, tools, info):
            if role == 'facts':
                assert any(m.get('name') == 'read_evidence' for m in messages)
            return super().complete(role, messages, tools, info)

    assert EvidencePipeline(store, run['id'], Resumed()).analyze()['actions'][0]['owner'] == '王宁'
    assert store.get_run(run['id'])['model_calls'] == 6
    assert store.get_run(run['id'])['tool_calls'] == tool_calls


def test_saved_model_submission_is_consumed_without_repeating_inference(typed_store, monkeypatch):
    store, run = typed_store
    original_save = store.save_conversation
    interrupted = False

    def save_then_interrupt(*args):
        nonlocal interrupted
        original_save(*args)
        if not interrupted and args[3][-1]['role'] == 'assistant':
            interrupted = True
            raise RuntimeError('injected crash after model response was persisted')

    monkeypatch.setattr(store, 'save_conversation', save_then_interrupt)
    with pytest.raises(RuntimeError, match='persisted'):
        EvidencePipeline(store, run['id'], TypedAdapter()).analyze()
    monkeypatch.setattr(store, 'save_conversation', original_save)

    class NoRepeatedFacts(TypedAdapter):
        def complete(self, role, *args):
            assert role != 'facts'
            return super().complete(role, *args)

    assert EvidencePipeline(store, run['id'], NoRepeatedFacts()).analyze()['actions']
    assert store.get_run(run['id'])['model_calls'] == 4


def test_stale_worker_cannot_publish_and_new_owner_recovers(typed_store):
    from meetflow.database import WorkLease
    from meetflow.leases import LeaseManager, current_lease
    from sqlalchemy import update
    store, run = typed_store

    class Superseded(TypedAdapter):
        def complete(self, role, messages, tools, info):
            old = current_lease()
            assert old is not None
            with store.sessions.begin() as session:
                session.execute(update(WorkLease).where(WorkLease.id == old.key).values(expires_at=0))
            with LeaseManager(store.sessions, store.settings.data_dir).own(old.key) as successor:
                assert successor and successor.generation > old.generation
            return super().complete(role, messages, tools, info)

    driver = RunDriver(store, Superseded())
    try:
        driver.process(run['id'])
        assert store.latest_report(run['id']) is None
        assert store.get_run(run['id'])['status'] == 'running'
        driver.provider = TypedAdapter()
        driver.process(run['id'])
        assert store.get_run(run['id'])['status'] == 'waiting_review'
        assert store.get_run(run['id'])['model_calls'] == 5
    finally:
        driver.close()


def test_optional_missing_condition_does_not_suppress_action_but_uncertain_assertion_does():
    audit = Audited(reviews=[{'item': 0, 'fields': ['condition'], 'verdict': 'unknown',
                             'reason': '原文没有条件', 'evidence': ['s0']}])
    value = {'owner': '王宁', 'due': '2026-11-13', 'conditions': []}
    assert EvidencePipeline.blocking_reviews(audit, [value]) == []
    value['conditions'] = ['有未经支持的条件']
    assert EvidencePipeline.blocking_reviews(audit, [value])


def test_partial_audit_field_patches_accumulate_without_losing_previous_checks(typed_store):
    store, run = typed_store

    class Patches(TypedAdapter):
        def complete(self, role, messages, tools, info):
            if role != 'audit':
                return super().complete(role, messages, tools, info)
            prior = any('accepted_partial' in m.get('content', '') for m in messages if m['role'] == 'tool')
            fields = ['condition', 'status', 'commitment'] if prior else ['owner', 'date']
            return {'role': 'assistant', 'tool_calls': [tool_call('submit_result', {'reviews': [{
                'item': 0, 'fields': fields, 'verdict': 'supported', 'reason': '逐字段有原文支持', 'evidence': ['s0']}]})]}, {}

    report = EvidencePipeline(store, run['id'], Patches()).analyze()
    assert report['actions'][0]['owner'] == '王宁'
    audit = next(r['body'] for r in store.artifacts(run['id']) if r['role'] == 'audit')
    assert {field for review in audit['reviews'] for field in review['fields']} == {'owner', 'date', 'condition', 'status', 'commitment'}
    assert store.get_run(run['id'])['model_calls'] == 5


def test_cross_page_revision_and_factual_risk_survive_with_full_character_coverage(typed_store):
    store, original_run = typed_store
    text = '销售王宁：我明天发送报价。\n' + ('客户：讨论现状，没有新的安排。\n' * 140) + '销售王宁：报价改为下周一发送。\n客户：安全评审尚未通过。'
    run = store.create_meeting(MeetingInput(project_id=original_run['project_id'], mode='live',
        transcript=text, meeting_time='2026-11-12T15:00:00+08:00'))['run']

    class Paged(TypedAdapter):
        def complete(self, role, messages, tools, info):
            initial = json.loads(messages[1]['content'])
            if role == 'coverage':
                value = initial['initial_extraction']
            elif role == 'facts':
                mentions, observations = [], []
                for span in initial['spans']:
                    if '发送报价' in span['text']:
                        mentions.append({'task': '发送报价', 'quote': span['text'], 'ref': span['id'],
                            'kind': 'commitment', 'owner': '王宁', 'due_text': '明天'})
                    elif '报价改为' in span['text']:
                        mentions.append({'task': '发送报价', 'quote': span['text'], 'ref': span['id'],
                            'kind': 'revision', 'owner': '王宁', 'due_text': '下周一'})
                    if '安全评审' in span['text']:
                        observations.append({'quote': span['text'], 'ref': span['id'], 'kind': 'risk'})
                value = {'summary': '本页已核查', 'mentions': mentions, 'observations': observations}
            elif role == 'reconcile':
                keys = list(initial['mentions'])
                value = {'items': [{'title': '发送报价', 'mentions': keys, 'owner_from': keys[0],
                                   'date_from': keys[-1], 'conditions_from': []}]}
            else:
                return super().complete(role, messages, tools, info)
            return {'role': 'assistant', 'tool_calls': [tool_call('submit_result', value)]}, {}

    report = EvidencePipeline(store, run['id'], Paged()).analyze()
    assert report['source_processing']['pages'] >= 2
    assert report['source_processing']['input_chars'] == report['source_processing']['processed_chars']
    assert report['actions'][0]['due'] == '2026-11-16'
    assert report['risks'][0]['text'] == '客户：安全评审尚未通过。'


def test_meeting_note_policy_preserves_real_customer_deliverables():
    from meetflow.evidence_pipeline import MEETING_NOTES
    assert '记录建议' in MEETING_NOTES
    assert '发送会议纪要' not in MEETING_NOTES
    assert '整理需求文档' not in MEETING_NOTES


def test_revision_label_normalization_requires_exact_existing_task_and_preserves_cancel_deliverable():
    from meetflow.evidence_pipeline import canonical_task
    names = {'发送报价', '预订'}
    assert canonical_task({'task': '改期发送报价', 'kind': 'revision'}, names) == '发送报价'
    assert canonical_task({'task': '发送报价改期', 'kind': 'revision'}, names) == '发送报价'
    assert canonical_task({'task': '取消预订', 'kind': 'commitment'}, names) == '取消预订'
    assert canonical_task({'task': '改期发送合同', 'kind': 'revision'}, names) == '改期发送合同'


def test_oversized_entity_history_fails_explicitly_instead_of_silently_truncating(typed_store):
    store, run = typed_store
    pipeline = EvidencePipeline(store, run['id'], TypedAdapter())
    mention = {'task': '发送报价', 'quote': '我明天发送报价', 'ref': 's0', 'kind': 'commitment',
               'owner': '王宁', 'due_text': '明天', 'condition': None}
    with pytest.raises(DomainError, match='未截断历史'):
        pipeline.reconcile_task('发送报价', {f'm{i}': mention for i in range(21)}, 0)
