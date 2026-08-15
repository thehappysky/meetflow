from meetflow.database import Run
from test_access import access as access  # noqa: PLC0414 -- authenticated fixture
from test_access import recording, submitted

BASE="/api/workspace/maintenance"


def test_private_trash_restore_keeps_center_and_scope(access):
    a=access
    row,source=submitted(a)
    assert a.client.delete('/api/workspace/recordings/'+row['id'],headers=a.actors['sales.a']).status_code==200
    assert a.client.get('/api/workspace/center/'+source['id'],headers=a.actors['manager.a']).status_code==200
    for actor in ('sales.b','sales.a2'):
        assert a.client.get(BASE+'/trash?kind=personal',headers=a.actors[actor]).json()==[]
    assert a.client.get(BASE+'/trash?kind=personal',headers=a.actors['boss']).status_code==403
    trash=a.client.get(BASE+'/trash?kind=personal',headers=a.actors['sales.a']).json()
    assert trash[0]['id']==row['id'] and trash[0]['removed_at']
    endpoint=BASE+'/personal/'+row['id']+'/restore'
    payload={'version':1,'reason':'恢复误移除的合成会谈'}
    assert a.client.post(endpoint,headers=a.actors['sales.b'],json=payload).status_code==404
    assert a.client.post(endpoint,headers=a.actors['sales.a'],json=payload).status_code==200
    assert a.client.post(endpoint,headers=a.actors['sales.a'],json=payload).status_code==409
    events=a.client.get(BASE+'/personal/'+row['id']+'/activity',headers=a.actors['sales.a']).json()
    assert any(e['action']=='恢复个人记录' for e in events)


def test_center_withdrawal_checks_version_and_only_responsible_manager_restores(access):
    a=access
    row,source=submitted(a)
    endpoint='/api/workspace/center/'+source['id']
    assert a.client.get(BASE+'/center/'+source['id']+'/impact',headers=a.actors['manager.b']).status_code==404
    assert a.client.post(endpoint+'/delete',headers=a.actors['manager.a'],json={'reason':'合成资料撤下','revision':2}).status_code==409
    assert a.client.post(endpoint+'/delete',headers=a.actors['manager.a'],json={'reason':'合成资料撤下','revision':1}).status_code==200
    assert a.client.get('/api/workspace/recordings/'+row['id'],headers=a.actors['sales.a']).status_code==200
    assert a.client.get('/api/workspace/business/dashboard',headers=a.actors['manager.a']).json()['sources']==[]
    assert a.client.get(BASE+'/trash?kind=center',headers=a.actors['manager.b']).json()==[]
    restore=BASE+'/center/'+source['id']+'/restore'
    value={'version':1,'reason':'经核对恢复合成中心证据'}
    for actor in ('sales.a','boss'):
        assert a.client.post(restore,headers=a.actors[actor],json=value).status_code==403
    assert a.client.post(restore,headers=a.actors['manager.a'],json=value).status_code==200
    assert a.client.get(endpoint,headers=a.actors['manager.a']).json()['revision']==1


def test_retry_is_owner_scoped_preserves_budget_and_rejects_duplicate(access):
    a=access
    row=recording(a)
    with a.app.state.store.sessions.begin() as session:
        run=session.get(Run,row['run_id'])
        run.status,run.model_calls,run.tool_calls='failed',4,7
    endpoint=BASE+'/personal/'+row['id']+'/retry'
    for actor in ('sales.a2','manager.a','boss'):
        assert a.client.post(endpoint,headers=a.actors[actor],json={'version':1}).status_code==404
    assert a.client.post(endpoint,headers=a.actors['sales.a'],json={'version':1}).status_code==200
    assert a.client.post(endpoint,headers=a.actors['sales.a'],json={'version':1}).status_code==409
    run=a.app.state.store.get_run(row['run_id'])
    assert (run['status'],run['model_calls'],run['tool_calls'])==('queued',4,7)
    with a.app.state.store.sessions.begin() as session:
        run=session.get(Run,row['run_id'])
        run.status,run.model_calls='failed',a.app.state.store.settings.max_model_calls
    assert a.client.post(endpoint,headers=a.actors['sales.a'],json={'version':1}).status_code==409


def test_queued_cancel_does_not_change_report_or_reset_calls(access):
    a=access
    row=recording(a)
    before=a.app.state.store.latest_report(row['run_id'])
    a.app.state.store.update_run(row['run_id'],status='queued')
    endpoint=BASE+'/personal/'+row['id']+'/cancel'
    assert a.client.post(endpoint,headers=a.actors['sales.a'],json={'version':1}).status_code==200
    run=a.app.state.store.get_run(row['run_id'])
    assert run['status']=='cancelled' and run['cancel_requested']
    assert a.app.state.store.latest_report(row['run_id'])==before
