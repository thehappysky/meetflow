import json

import httpx
import pytest
from meetflow.config import Settings
from meetflow.domain import AgentResult, DomainError
from meetflow.providers import LiveProvider, tool_call

TOOLS = [{'type': 'function', 'function': {'name': 'submit_result', 'description': '提交',
          'parameters': AgentResult.model_json_schema()}}]


def test_structured_tool_decision_preserves_history_schema_and_budget():
    history = [{'role': 'system', 'content': '角色权限边界'}, {'role': 'user', 'content': '提取事实'},
               {'role': 'assistant', 'tool_calls': [tool_call('read_source', {'source_id': 's1'})]},
               {'role': 'tool', 'name': 'read_source', 'content': '{"spans":[]}', 'tool_call_id': 'old'}]
    before = json.dumps([history, TOOLS], ensure_ascii=False)

    def respond(request):
        body = json.loads(request.content)
        assert request.url == 'http://127.0.0.1:11434/api/chat'
        assert request.extensions['timeout']['read'] == 0.125
        assert 'tools' not in body
        assert body['think'] is False and body['stream'] is False
        assert body['options'] == {'num_predict': 512, 'num_ctx': 8192}
        assert body['format']['oneOf'][0]['properties']['name'] == {'const': 'submit_result'}
        assert 'Claim' in body['format']['$defs']
        assert '$defs' not in body['format']['oneOf'][0]['properties']['arguments']
        assert json.loads(body['messages'][-2]['content']) == {'name': 'read_source', 'arguments': {'source_id': 's1'}}
        assert json.loads(body['messages'][-1]['content'])['tool_observation'] == 'read_source'
        systems = [m['content'] for m in body['messages'] if m['role'] == 'system']
        assert len(systems) == 1 and '角色权限边界' in systems[0]
        return httpx.Response(200, json={'message': {'content': '{"name":"submit_result","arguments":{}}'},
                                       'prompt_eval_count': 55, 'eval_count': 13})

    settings = Settings(model_api_profile='ollama_json', model_name='test-local', model_api_key='',
                        model_base_url='http://127.0.0.1:11434/v1', model_timeout=0.125, max_output_tokens=512)
    message, usage = LiveProvider(settings, transport=httpx.MockTransport(respond)).complete('meeting', history, TOOLS, {})
    assert message['tool_calls'][0]['function']['name'] == 'submit_result'
    assert usage == {'prompt_tokens': 55, 'completion_tokens': 13}
    assert json.dumps([history, TOOLS], ensure_ascii=False) == before


@pytest.mark.parametrize('decision', ['not-json', '[]', '{"name":"delete_all","arguments":{}}',
                                      '{"name":[],"arguments":{}}', '{"name":"submit_result","arguments":[]}'])
def test_structured_invalid_response_never_falls_back(decision):
    settings = Settings(model_api_profile='ollama_json', model_name='test-local')
    provider = LiveProvider(settings, transport=httpx.MockTransport(lambda request: httpx.Response(
        200, json={'message': {'content': decision}})))
    with pytest.raises(DomainError):
        provider.complete('meeting', [], TOOLS, {})


def test_structured_http_error_never_falls_back():
    settings = Settings(model_api_profile='ollama_json', model_name='test-local')
    with pytest.raises(DomainError, match='503'):
        LiveProvider(settings, transport=httpx.MockTransport(lambda request: httpx.Response(503))).complete(
            'meeting', [], TOOLS, {})


def test_oversized_history_is_refused_before_transport_instead_of_silent_context_truncation():
    settings = Settings(model_api_profile='ollama_json', model_name='test-local', model_context_window=8192,
                        max_output_tokens=512)
    def should_not_send(request):
        raise AssertionError('Oversized history was sent to the model')
    with pytest.raises(DomainError, match='完整资料'):
        LiveProvider(settings, transport=httpx.MockTransport(should_not_send)).complete('meeting',
            [{'role': 'user', 'content': '长' * 4000}], TOOLS, {})


def test_configured_tokenizer_avoids_byte_only_false_refusal_and_still_blocks_real_overflow(tmp_path):
    tokenizers = pytest.importorskip('tokenizers')
    tokenizer = tokenizers.Tokenizer(tokenizers.models.WordLevel({'[UNK]': 0, '中文': 1}, unk_token='[UNK]'))
    tokenizer.pre_tokenizer = tokenizers.pre_tokenizers.Whitespace()
    path = tmp_path / 'test-tokenizer.json'
    tokenizer.save(str(path))
    calls = []
    def respond(request):
        calls.append(request)
        return httpx.Response(200, json={'message': {'content': '{"name":"submit_result","arguments":{}}'}})
    settings = Settings(model_api_profile='ollama_json', model_name='test-local', model_context_window=8192,
                        max_output_tokens=512, model_tokenizer_path=str(path))
    provider = LiveProvider(settings, transport=httpx.MockTransport(respond))
    assert provider.complete('meeting', [{'role': 'user', 'content': '中文 ' * 2000}], TOOLS, {})[0]['tool_calls']
    with pytest.raises(DomainError, match='完整资料'):
        provider.complete('meeting', [{'role': 'user', 'content': '中文 ' * 10000}], TOOLS, {})
    assert len(calls) == 1
