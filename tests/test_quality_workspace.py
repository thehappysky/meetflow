import copy
from concurrent.futures import ThreadPoolExecutor

import pytest
from meetflow.auth import password_digest
from meetflow.database import (
    Account,
    Correction,
    QualityCase,
    QualityFeedback,
    QualityReview,
    SalesAnalysis,
    Source,
    row_dict,
)
from meetflow.domain import DomainError
from sqlalchemy import event, func, select
from test_access import PASSWORD, recording, submitted
from test_access import access as access  # noqa: PLC0414 -- shared authenticated fixture


def request(a, method, path, actor='manager.a', **kwargs):
    return getattr(a.client,method)('/api/workspace/quality'+path,headers=a.actors[actor],**kwargs)


def listing(a,actor='manager.a'):
    response=request(a,'get','/cases?scope=center',actor);assert response.status_code==200,response.text
    return response.json()['cases']


def detail(a,key,actor='manager.a',query=''):
    response=request(a,'get','/cases/'+key+query,actor);assert response.status_code==200,response.text
    return response.json()


def finish(a,key):
    with a.app.state.store.sessions() as session:
        active=session.get(QualityCase,key).active_analysis_id
    if active:a.app.state.sales_driver.process(active)
    return detail(a,key)


def central(a):
    rec,sub=submitted(a)
    return rec,sub,finish(a,listing(a)[0]['id'])


def test_private_minutes_do_not_generate_management_grading(access):
    a=access; rec=recording(a)
    assert a.app.state.quality.ensure('personal',rec['id']) is None
    with a.app.state.store.sessions() as session:
        assert session.scalar(select(func.count()).select_from(QualityCase))==0
    assert a.client.get('/api/workspace/recordings/'+rec['id'],headers=a.actors['sales.a']).json()['report']
    assert listing(a)==[]


def test_score_subject_is_the_submitting_sales_account_in_all_views(access):
    a=access;_,_,case=central(a)
    subject=case['subject']
    with a.app.state.store.sessions() as session:
        source=session.get(QualityCase,case['id'])
        assert subject['account_id']==source.owner_id
        assert session.get(Account,subject['account_id']).username=='sales.a'
    assert subject['name']=='sales.a' and subject['team_id']=='ta' and subject['team_name']=='一组'
    assert listing(a)[0]['subject']==subject
    for view in ('summary','details','full'):
        assert detail(a,case['id'],'boss','?view='+view)['subject']==subject


def test_list_is_one_scoped_projection_and_matches_case_summary(access):
    a=access;_,_,first=central(a)
    submitted(a,'sales.b','other-team-projection')
    service=a.app.state.quality
    with service.sessions() as session:
        manager=session.scalar(select(Account).where(Account.username=='manager.a'))
        user={key:getattr(manager,key) for key in ('id','role','company_id','team_id')}
        row=session.get(QualityCase,first['id'])
        expected=service.serialize(session,row,row.source_revision)
    statements=[];engine=service.sessions.kw['bind']
    def collect(_conn,_cursor,statement,*_):
        statements.append(statement)
    event.listen(engine,'before_cursor_execute',collect)
    try:
        result=service.listing(user,'center')
    finally:
        event.remove(engine,'before_cursor_execute',collect)
    assert len(statements)==1
    assert result['cases']==[expected]


def test_derived_failure_preserves_minutes_and_is_recorded_once(access):
    a=access
    def fail(_run_id):
        raise RuntimeError('injected derived-work failure')
    a.app.state.driver.on_report=fail
    rec=recording(a)
    assert a.app.state.store.latest_report(rec['run_id'])
    assert a.app.state.store.get_run(rec['run_id'])['status']=='waiting_review'
    from meetflow.database import RunEvent
    with a.app.state.store.sessions() as session:
        events=session.scalars(select(RunEvent).where(RunEvent.run_id==rec['run_id'],RunEvent.kind=='derived_prepare_failed')).all()
        assert len(events)==1 and events[0].payload['error']=='injected derived-work failure'


def test_sales_cannot_read_or_operate_any_quality_entry_even_with_real_ids(access):
    a=access; rec,sub,case=central(a)
    for actor in ('sales.a','sales.a2','sales.b'):
        for path in ('/cases','/cases?scope=personal','/policy','/cases/'+case['id'],
                     '/cases/'+case['id']+'?view=summary','/cases/'+case['id']+'?view=full',
                     '/cases/'+case['id']+'?analysis_id='+case['history'][0]['id']):
            assert request(a,'get',path,actor).status_code==403
        for suffix in ('context','review','retry','feedback'):
            assert request(a,'post','/cases/'+case['id']+'/'+suffix,actor,json={}).status_code==403
        assert request(a,'post','/policy',actor,json={}).status_code==403
    assert len(listing(a))==len(listing(a,'boss'))==1
    assert listing(a,'manager.b')==[]
    assert request(a,'get','/cases/'+case['id'],'manager.b').status_code==404
    assert request(a,'get','/cases?scope=personal').status_code==403
    assert a.client.get('/api/sales/template',headers=a.actors['sales.a']).status_code==403
    for kind,key in (('personal',rec['id']),('center',sub['id'])):
        response=a.client.get(f'/api/workspace/maintenance/{kind}/{key}/impact',headers=a.actors['sales.a']).json()
        assert 'quality_versions' not in response
    exported=a.client.get('/api/workspace/center/'+sub['id'],headers=a.actors['sales.a']).json()
    assert 'quality_context' not in exported and 'score' not in exported
    assert request(a,'get','/cases','admin').status_code==403
    assert request(a,'get','/cases/'+case['id'],'admin').status_code==403
    correction=a.client.post('/api/workspace/center/'+sub['id']+'/corrections',headers=a.actors['sales.a'],json={
        'base_revision':1,'title':'更正名称','customer':'合成客户','meeting_date':'','reason':'核对记录名称'}).json()
    with a.app.state.store.sessions.begin() as session:
        row=session.get(Correction,correction['id'])
        row.proposed={**row.proposed,'replacement':{'quality_context':{'stage':'管理专用背景'}}}
    sales_view=a.client.get('/api/workspace/corrections',headers=a.actors['sales.a']).json()[0]
    manager_view=a.client.get('/api/workspace/corrections',headers=a.actors['manager.a']).json()[0]
    assert 'quality_context' not in sales_view['proposed']['replacement']
    assert manager_view['proposed']['replacement']['quality_context']=={'stage':'管理专用背景'}


def test_unknown_speakers_require_manager_confirmation_after_submission(access):
    a=access
    with pytest.raises(DomainError,match='单段 4000'):
        a.app.state.quality.spans({'long':{'text':'字'*4001}},{})
    rec=a.client.post('/api/workspace/recordings',headers=a.actors['sales.a'],json={'transcript':'我们讨论目前遇到的问题。\n采购时间尚未确认。'}).json()
    a.app.state.driver.process(rec['run_id'])
    a.client.post('/api/workspace/recordings/'+rec['id']+'/submit',headers=a.actors['sales.a'],json={'version':1,'opportunity_id':a.opportunities['sales.a'],'request_key':'unknown-speaker-submit'})
    case=detail(a,listing(a)[0]['id']);assert case['status']=='needs_speakers' and case['report'] is None
    payload={'version':case['version'],'reason':'主管核对真实身份','speakers':{'s1':'sales','s2':'customer'}}
    assert request(a,'post',f"/cases/{case['id']}/context",json={**payload,'speakers':{'s99':'sales'}}).status_code==400
    assert request(a,'post',f"/cases/{case['id']}/context",json=payload).status_code==200
    changed=finish(a,case['id']);assert changed['report'] and not changed['complete']
    assert request(a,'post',f"/cases/{case['id']}/context",json={'version':changed['version'],'reason':'身份仍不确定','speakers':{'s1':'unknown','s2':'unknown'}}).status_code==200
    current=detail(a,case['id']);assert current['report'] is None
    assert detail(a,case['id'],query='?analysis_id='+changed['history'][0]['id'])['historical']


def test_center_quality_survives_private_changes_but_not_center_withdrawal(access):
    a=access;rec,sub,case=central(a)
    response=a.client.patch('/api/workspace/recordings/'+rec['id'],headers=a.actors['sales.a'],json={'version':1,'title':'私人改名','customer':'私人客户','meeting_date':''})
    assert response.status_code==200
    a.client.delete('/api/workspace/recordings/'+rec['id'],headers=a.actors['sales.a'])
    assert detail(a,case['id'])['report']==case['report']
    a.client.post('/api/workspace/center/'+sub['id']+'/delete',headers=a.actors['manager.a'],json={'reason':'撤下合成资料'})
    assert request(a,'get','/cases/'+case['id'],'boss').status_code==404


def test_summary_reason_and_evidence_views_disclose_progressively(access):
    a=access;_,_,case=central(a)
    with a.app.state.store.sessions.begin() as session:
        row=session.get(SalesAnalysis,case['history'][0]['id']); body=copy.deepcopy(row.report)
        body['decisions'][0]['reason']='仅展开原因后才出现的具体解释标记'
        body['decisions'][0]['reason_title']='需求核对信息待确认'
        row.report=body
    overview=detail(a,case['id'],query='?view=summary')
    assert overview['metrics'] and overview['decisions']
    assert overview['decisions'][0]['reason_title']=='需求核对信息待确认'
    for key in ('report','review','rules','spans','feedback','review_history'):
        assert key not in overview
    assert 'reason' not in overview['decisions'][0] and 'evidence' not in overview['decisions'][0]
    reasons=detail(a,case['id'],query='?view=details')
    assert reasons['decisions'][0]['reason']=='仅展开原因后才出现的具体解释标记'
    assert 'spans' not in reasons
    full=detail(a,case['id'],query='?view=full')
    assert full['spans'] and full['rules']


def test_center_revision_preserves_old_results_and_legacy_feedback(access):
    a=access;_,sub,old=central(a)
    with a.app.state.store.sessions.begin() as session:
        case=session.get(QualityCase,old['id'])
        session.add(QualityFeedback(id='legacy-note',case_id=case.id,analysis_id=case.active_analysis_id,
            requester_id=case.owner_id,body={'rule_id':'needs','reason':'旧版遗留反馈','evidence':['s1']}))
    change=a.client.post('/api/workspace/center/'+sub['id']+'/corrections',headers=a.actors['sales.a'],json={'base_revision':1,'title':'日期核实后','customer':'客户A','meeting_date':'2026-11-10','reason':'核对日历'}).json()
    assert a.client.post('/api/workspace/corrections/'+change['id']+'/review',headers=a.actors['manager.a'],json={'approve':True}).status_code==200
    current=listing(a);assert len(current)==1 and current[0]['source_revision']==2
    historical=detail(a,old['id']);assert historical['stale'] and historical['feedback'][0]['status']=='superseded'
    assert request(a,'post','/cases/'+old['id']+'/review',json={'version':old['version'],'report_hash':old['report_hash']}).status_code==409


def test_policy_versions_preserve_analyses_and_manager_cannot_publish(access):
    a=access;_,_,original=central(a)
    cards=request(a,'get','/policy','admin').json()['cards'];assert len(cards)==5
    card=next(c for c in cards if c['stage']=='通用复盘')
    payload={k:copy.deepcopy(card[k]) for k in ('name','rules','product_notes')};payload.update(base_id=card['id'],name='企业规则新版')
    assert request(a,'post','/policy',json=payload).status_code==403
    wrong=copy.deepcopy(payload);wrong['rules'][0]['weight']=24
    assert request(a,'post','/policy','admin',json=wrong).status_code==400
    assert request(a,'post','/policy','admin',json=payload).status_code==201
    assert request(a,'post','/policy','admin',json=payload).status_code==409
    assert detail(a,original['id'])['policy']==original['policy']
    assert request(a,'post','/cases/'+original['id']+'/context',json={'version':original['version'],'reason':'主管应用新版规则'}).status_code==200
    new=finish(a,original['id']);assert new['policy']['version']==2
    assert detail(a,original['id'],query='?analysis_id='+original['history'][0]['id'])['policy']['version']==1


def test_manager_review_guards_concurrency_and_keeps_raw_machine_report(access):
    a=access;_,_,case=central(a)
    payload={'version':case['version'],'report_hash':case['report_hash'],'decisions':[{'rule_id':'needs','status':'met','reason':'','evidence':['s1']}]}
    assert request(a,'post','/cases/'+case['id']+'/review',json=payload).status_code==400
    payload['decisions'][0].update(reason='主管核对依据',evidence=['s2'])
    assert request(a,'post','/cases/'+case['id']+'/review',json=payload).status_code==400
    payload['decisions'][0].update(status='unmet',evidence=['s1'])
    assert request(a,'post','/cases/'+case['id']+'/review',json=payload).status_code==400
    payload['decisions'][0].update(status='unknown',reason='资料不完整，保留未知')
    assert request(a,'post','/cases/'+case['id']+'/review','boss',json=payload).status_code==403
    assert request(a,'post','/cases/'+case['id']+'/review','manager.b',json=payload).status_code==404
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert list(pool.map(lambda _:request(a,'post','/cases/'+case['id']+'/review',json=payload).status_code,range(2)))==[200,200]
    assert detail(a,case['id'])['report']==case['report']
    reviewed=detail(a,case['id'])
    assert reviewed['subject']==case['subject']  # The reviewer does not become the evaluated salesperson.
    assert next(d for d in reviewed['decisions'] if d['rule_id']=='needs')['reason_title']==''
    overview=detail(a,case['id'],query='?view=summary')
    assert next(d for d in overview['decisions'] if d['rule_id']=='needs')['reason_title']=='需求澄清：依据不足，待确认'
    with a.app.state.store.sessions() as session:
        assert session.scalar(select(func.count()).select_from(QualityReview))==1


def test_retry_preserves_budget_and_failures_are_explicit(access):
    a=access;submitted(a);key=listing(a)[0]['id']
    class Failing:
        def complete(self,*_):raise RuntimeError('test-only unavailable')
    a.app.state.sales_driver.provider=Failing();failed=finish(a,key)
    assert failed['status']=='failed' and failed['report'] is None
    with a.app.state.store.sessions() as session:
        active=session.get(QualityCase,key).active_analysis_id;before=session.get(SalesAnalysis,active).model_calls
    assert request(a,'post','/cases/'+key+'/retry','boss').status_code==403
    assert request(a,'post','/cases/'+key+'/retry').status_code==200
    a.app.state.sales_driver.provider=None;assert finish(a,key)['report']
    with a.app.state.store.sessions.begin() as session:
        analysis=session.get(SalesAnalysis,active);assert analysis.model_calls>before
        analysis.status='failed';analysis.model_calls=a.app.state.store.settings.max_model_calls
    assert request(a,'post','/cases/'+key+'/retry').status_code==409


def test_other_company_cannot_read_center_quality_or_publish_borrowed_rules(access):
    a=access;_,_,case=central(a)
    with a.app.state.store.sessions.begin() as session:
        for role in ('boss','admin'):
            session.add(Account(id='external-'+role,company_id='c2',username='external.'+role,name='外部'+role,role=role,password_hash=password_digest(PASSWORD)))
    outsider=a.login('external.boss')
    assert a.client.get('/api/workspace/quality/cases/'+case['id'],headers=outsider).status_code==404
    assert a.client.get('/api/workspace/quality/cases',headers=outsider).json()['cases']==[]
    card=request(a,'get','/policy','admin').json()['cards'][0]
    assert a.client.post('/api/workspace/quality/policy',headers=a.login('external.admin'),json={'base_id':card['id'],'name':card['name'],'rules':card['rules'],'product_notes':''}).status_code==409


def test_legacy_private_quality_is_inaccessible_and_pending_work_is_suspended(access):
    a=access;rec,_,case=central(a)
    with a.app.state.store.sessions.begin() as session:
        original=session.get(QualityCase,case['id']);analysis=session.get(SalesAnalysis,original.active_analysis_id)
        legacy=SalesAnalysis(**{**row_dict(analysis),'id':'legacy-analysis','status':'queued'})
        session.add(legacy);session.flush()
        session.add(QualityCase(id='legacy-private',scope_key='personal:'+rec['id']+':v1',company_id=original.company_id,
            owner_id=original.owner_id,team_id=original.team_id,source_kind='personal',source_id=rec['id'],source_revision=1,
            body=original.body,analysis_ids=[legacy.id],active_analysis_id=legacy.id))
    a.app.state.quality.suspend_private()
    with a.app.state.store.sessions() as session:
        assert session.get(SalesAnalysis,'legacy-analysis').status=='cancelled'
    assert request(a,'get','/cases/legacy-private','sales.a').status_code==403
    assert request(a,'get','/cases/legacy-private').status_code==404
    assert len(listing(a))==1


def test_question_answers_version_notes_without_reinference_or_mutating_center(access, monkeypatch):
    a = access
    rec = recording(a)
    store = a.app.state.store
    body = copy.deepcopy(store.latest_report(rec["run_id"])["body"])
    body["questions"] = [{"key": "date", "text": "实际会谈日期是什么？", "evidence": [], "affected_actions": [], "options": []}]
    store.save_report(rec["run_id"], body)
    submitted = a.client.post(f"/api/workspace/recordings/{rec['id']}/submit", headers=a.actors["sales.a"], json={
        "version": 1, "customer": "合成客户", "opportunity_id": a.opportunities["sales.a"], "request_key": "clarify-test-submit"}).json()
    original = store.source_map(rec["run_id"])
    path = f"/api/workspace/recordings/{rec['id']}/clarifications"
    assert a.client.post(path, headers=a.actors["manager.a"], json={"version": 1, "answers": {"date": "11月10日"}}).status_code == 404
    assert a.client.post(path, headers=a.actors["sales.a"], json={"version": 1, "answers": {"foreign": "伪造其他问题"}}).status_code == 400
    monkeypatch.setattr(store.settings, 'start_worker', True)
    def forbid_inference(*args):
        raise AssertionError('saving a seller note must not resubmit AI work')
    monkeypatch.setattr(a.app.state.driver, 'submit', forbid_inference)
    response = a.client.post(path, headers=a.actors["sales.a"], json={"version": 1, "answers": {"date": "核对后为11月10日"}})
    assert response.status_code == 201, response.text
    assert response.json()["version"] == 2 and response.json()["run_id"] == rec["run_id"]
    assert store.source_map(rec["run_id"]) == original
    central = a.client.get(f"/api/workspace/center/{submitted['id']}", headers=a.actors["manager.a"]).json()
    assert central["clarifications"] == []
    with store.sessions() as session:
        assert session.scalar(select(Source).where(Source.title == "销售补充（未经客户确认）")) is not None
    assert store.latest_report(rec["run_id"])["body"] == body
    assert a.client.post(path, headers=a.actors["sales.a"], json={"version": 1, "answers": {"date": "网络重试"}}).status_code == 409
