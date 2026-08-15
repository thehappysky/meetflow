import json

import pytest
from meetflow.agents import AgentRunner, role_result_model, validate_result
from meetflow.config import Settings
from meetflow.database import make_database
from meetflow.domain import ROLE_NAMES, DomainError, MeetingInput
from meetflow.providers import DemoProvider, tool_call
from meetflow.store import Store


@pytest.mark.parametrize('meeting_date,meeting_time', [
    ('2026-11-12', None),
    (None, '2026-11-12T14:30:00+08:00'),
    (None, None),
])
def test_agent_receives_actual_date_separately_without_inventing_meeting_time(tmp_path, meeting_date, meeting_time):
    settings = Settings(data_dir=tmp_path, start_worker=False)
    engine, sessions = make_database(settings)
    try:
        store = Store(sessions, settings)
        project = store.create_project('isolated date context')
        run = store.create_meeting(MeetingInput(project_id=project['id'], transcript='销售：我明天发送报价。',
                                                meeting_time=meeting_time))['run']
        if meeting_date:
            store.update_run(run['id'], manifest={**run['manifest'], 'meeting_date': meeting_date})

        class CaptureProvider:
            def complete(self, role, messages, tools, info):
                assert info['meeting_date'] == meeting_date
                assert info['meeting_time'] == meeting_time
                initial = json.loads(messages[1]['content'])
                assert initial['meeting_date'] == meeting_date
                assert initial['meeting_time'] == meeting_time
                return {'role': 'assistant', 'tool_calls': [tool_call('submit_result', {})]}, {}

        AgentRunner(store, CaptureProvider()).run(run['id'], 'planner', '核对日期', {})
    finally:
        engine.dispose()


def test_invalid_analysis_dag_is_returned_to_coordinator_for_repair(tmp_path):
    engine, sessions = make_database(Settings(data_dir=tmp_path, start_worker=False))
    try:
        store = Store(sessions, Settings(data_dir=tmp_path, start_worker=False))
        project = store.create_project('isolated coordinator feedback')
        run = store.create_meeting(MeetingInput(project_id=project['id'], transcript='销售：我来准备资料。'))['run']

        class RepairingProvider:
            calls = 0

            def complete(self, role, messages, tools, info):
                self.calls += 1
                if self.calls == 1:
                    value = {'delegations': [{'id': 'p', 'role': 'planner', 'goal': '提取行动'}]}
                else:
                    feedback = json.loads(messages[-1]['content'])
                    assert '审查任务' in feedback['error']
                    value = DemoProvider.plan([])
                return {'role': 'assistant', 'tool_calls': [tool_call('submit_result', value)]}, {}

        provider = RepairingProvider()
        result = AgentRunner(store, provider).run(run['id'], 'coordinator', '安排分析', {})
        assert provider.calls == 2
        assert any(job['role'] == 'auditor' for job in result['delegations'])
        assert store.artifacts(run['id']) == []  # runner never caches the rejected plan itself
    finally:
        engine.dispose()


def test_empty_meeting_summary_is_not_accepted_as_a_usable_report():
    with pytest.raises(DomainError, match='非空纪要'):
        validate_result({'claims': [{'text': '承诺发送资料', 'kind': 'commitment', 'evidence': ['s1']}]},
                        'meeting', {'s1': {'source_kind': 'transcript'}}, [])


@pytest.mark.parametrize('role', ROLE_NAMES)
def test_product_submission_schema_matches_role_permissions_and_returns_fresh_dict(role):
    schema = role_result_model(role).model_json_schema()
    exclusive = {'actions': 'planner', 'challenges': 'auditor', 'delegations': 'coordinator'}
    for name, allowed_role in exclusive.items():
        assert (name in schema['properties']) == (role == allowed_role)
    assert {'summary', 'claims', 'risks', 'questions'} <= set(schema['properties'])
    schema['properties'].clear()
    assert role_result_model(role).model_json_schema()['properties']
