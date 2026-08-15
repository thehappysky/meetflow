import json
from concurrent.futures import ThreadPoolExecutor

from meetflow.database import Source, UploadReceipt
from sqlalchemy import delete
from test_access import access as access  # noqa: PLC0414 -- authenticated fixture
from test_business import custom_record, publish


def test_upload_retry_is_atomic_and_does_not_duplicate_text_recordings(access):
    a=access
    value={'transcript':'销售：我负责发送资料。','request_key':'same-upload-request'}
    def send(_):
        return a.client.post('/api/workspace/recordings',headers=a.actors['sales.a'],json=value)
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses=list(pool.map(send,range(2)))
    assert all(r.status_code==201 for r in responses),[r.text for r in responses]
    assert responses[0].json()['id']==responses[1].json()['id']
    assert len(a.client.get('/api/workspace/recordings',headers=a.actors['sales.a']).json())==1
    assert a.client.post('/api/workspace/recordings',headers=a.actors['sales.a'],json={**value,'title':'changed'}).status_code==409
    other=a.client.post('/api/workspace/recordings',headers=a.actors['sales.b'],json=value)
    assert other.status_code==201 and other.json()['id']!=responses[0].json()['id']


def test_same_audio_hash_is_only_deduplicated_within_owner_and_temporary_file_removed(access, monkeypatch):
    a=access
    monkeypatch.setattr('meetflow.workspace.audio_available',lambda _:True)
    # Transport-only fixture; no ASR is run or claimed by this test.
    audio=b'local synthetic transport payload, not a valid audio sample'
    def send(actor,key,title=''):
        return a.client.post('/api/workspace/audio',headers=a.actors[actor],
            data={'metadata':json.dumps({'request_key':key,'title':title})},files={'file':('sample.wav',audio,'audio/wav')})
    first=send('sales.a','audio-upload-key-1')
    assert first.status_code==201,first.text
    duplicate=send('sales.a','audio-upload-key-2','new metadata is not silently applied')
    assert duplicate.status_code==201 and duplicate.json()['duplicate']
    assert first.json()['id']==duplicate.json()['id']
    other=send('sales.b','audio-upload-key-1')
    assert other.status_code==201 and other.json()['id']!=first.json()['id']
    assert len(list(a.app.state.store.settings.data_dir.joinpath('audio').iterdir()))==2
    a.client.delete('/api/workspace/recordings/'+first.json()['id'],headers=a.actors['sales.a'])
    assert send('sales.a','audio-upload-key-3').status_code==409
    assert len(list(a.app.state.store.settings.data_dir.joinpath('audio').iterdir()))==2


def test_transcript_revision_keeps_old_source_and_center_until_review_and_freezes_proposal(access):
    a=access
    original=custom_record(a)
    center=publish(a,original)
    original_run=a.app.state.store.get_run(original['run_id'])
    url='/api/workspace/recordings/'+original['id']+'/transcript'
    edit={'version':1,'text':'销售：我负责下周五发送修订报价。\n客户：演示改为暂停，尚未签约。','reason':'核对合成文字，修订口误'}
    for actor in ('sales.a2','manager.a','boss'):
        assert a.client.post(url,headers=a.actors[actor],json=edit).status_code==404
    response=a.client.post(url,headers=a.actors['sales.a'],json=edit)
    assert response.status_code==201,response.text
    next_run=response.json()['run_id']
    run=a.app.state.store.get_run(next_run)
    transcript=[s for s in run['manifest']['sources'] if s['kind']=='transcript']
    assert len(transcript)==1 and transcript[0]['version']==2
    assert all(s['start'] is None for s in transcript[0]['body']['spans'])
    assert a.app.state.store.get_run(original['run_id'])['manifest']==original_run['manifest']
    with a.app.state.store.sessions() as session:
        old=next(s for s in original_run['manifest']['sources'] if s['kind']=='transcript')
        assert session.get(Source,old['id']).body==old['body']
    a.app.state.driver.process(next_run)
    old_center=a.client.get('/api/workspace/center/'+center['id'],headers=a.actors['manager.a']).json()
    assert old_center['revision']==1 and '修订报价' not in old_center['report']['summary']
    proposal=a.client.post('/api/workspace/center/'+center['id']+'/corrections',headers=a.actors['sales.a'],json={
        'base_revision':1,'title':'合成转写更正','customer':'合成客户','meeting_date':'2026-11-10','reason':'采用个人修订后的新转写纪要',
        'use_personal_revision':True}).json()
    # A later private revision cannot silently replace the frozen proposal awaiting review.
    response=a.client.post(url,headers=a.actors['sales.a'],json={**edit,'version':2,'text':'销售：这是之后的另一版私人草稿，未申请替换中心。'})
    assert response.status_code==201
    a.app.state.driver.process(response.json()['run_id'])
    requests=a.client.get('/api/workspace/corrections',headers=a.actors['manager.a']).json()
    assert '修订报价' in requests[0]['proposed']['replacement']['report']['summary']
    reviewed=a.client.post('/api/workspace/corrections/'+proposal['id']+'/review',headers=a.actors['manager.a'],json={'approve':True})
    assert reviewed.status_code==200,reviewed.text
    new=a.client.get('/api/workspace/center/'+center['id'],headers=a.actors['manager.a']).json()
    assert new['revision']==2 and '修订报价' in new['report']['summary'] and '私人草稿' not in new['report']['summary']
    historical=a.client.get('/api/workspace/center/'+center['id']+'/versions/1',headers=a.actors['manager.a']).json()
    assert historical['report']==old_center['report'] and historical['evidence']==old_center['evidence']
    assert a.client.get('/api/workspace/center/'+center['id']+'/versions/1',headers=a.actors['manager.b']).status_code==404
    detail=a.client.get('/api/workspace/recordings/'+original['id'],headers=a.actors['sales.a']).json()
    assert len(detail['transcript_history'])==2


def test_existing_audio_gets_owner_fingerprint_without_reprocessing_or_resetting_sources(access,monkeypatch):
    a=access
    monkeypatch.setattr('meetflow.workspace.audio_available',lambda _:True)
    def upload(key):
        return a.client.post('/api/workspace/audio',headers=a.actors['sales.a'],data={'metadata':json.dumps({'request_key':key})},
            files={'file':('legacy.wav',b'synthetic legacy transport bytes','audio/wav')})
    first=upload('original-upload-key').json()
    before=a.app.state.store.get_run(first['run_id'])
    with a.app.state.store.sessions.begin() as session:
        session.execute(delete(UploadReceipt))  # Isolated fixture: emulate the pre-receipt database.
    a.app.state.workspace.index_existing_uploads()
    a.app.state.workspace.index_existing_uploads()
    repeated=upload('new-upload-request').json()
    assert repeated['id']==first['id'] and repeated['duplicate']
    assert a.app.state.store.get_run(first['run_id'])==before


def test_batch_submission_reports_partial_results_and_retry_does_not_duplicate_success(access):
    a=access
    first,second=custom_record(a),custom_record(a)
    items=[{'recording_id':r['id'],'version':r['version'],'request_key':'batch-submit-key-'+str(i),
        'opportunity_id':a.opportunities['sales.a'] if i==0 else None} for i,r in enumerate((first,second))]
    endpoint='/api/workspace/submissions/batch'
    response=a.client.post(endpoint,headers=a.actors['sales.a'],json={'items':items})
    assert response.status_code==200,response.text
    assert len(response.json()['succeeded'])==1 and len(response.json()['failed'])==1
    items[1]['opportunity_id']=a.opportunities['sales.a']
    retry=a.client.post(endpoint,headers=a.actors['sales.a'],json={'items':items}).json()
    assert len(retry['succeeded'])==2 and retry['failed']==[]
    assert len(a.client.get('/api/workspace/center',headers=a.actors['manager.a']).json())==2
    assert a.client.post(endpoint,headers=a.actors['boss'],json={'items':items}).status_code==403
