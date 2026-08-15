"""Behavior checks for report ownership, dates, frozen revisions and actual files."""
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO

from docx import Document
from meetflow.database import Account, PersonalRecording, SalesReport
from meetflow.domain import digest
from sqlalchemy import select
from test_access import access as access  # noqa: PLC0414 -- shared authenticated fixture
from test_access import recording
from test_business import custom_record, publish

BASE = "/api/workspace/reports"


def create(a, row, **changes):
    value = {"start": "2026-11-01", "end": "2026-11-30", "template": "weekly",
             "sources": [{"kind": "personal", "id": row["id"], "revision": row["version"]}],
             "request_key": "report-generate-key", **changes}
    return a.client.post(BASE, headers=a.actors["sales.a"], json=value)


def confirmation(a, draft):
    report = a.client.get(BASE + "/" + draft["id"], headers=a.actors["sales.a"]).json()
    return {"version": report["version"], "title": report["body"]["title"], "plan": "下周复核需求，需技术支持。",
        "entries": [{k: e.get(k, "") for k in ("key", "meeting_date", "summary", "next_steps", "risks", "note")}
                    for e in report["body"]["entries"]], "accept_unknown_dates": True}


def test_report_selection_ownership_date_scope_and_idempotency(access):
    a = access
    row = custom_record(a)
    draft = create(a, row)
    assert draft.status_code == 201, draft.text
    assert create(a, row).json()["id"] == draft.json()["id"]
    assert create(a, row, template="brief").status_code == 409
    assert create(a, row, start="2026-12-01", end="2026-12-30", request_key="other-period-key").status_code == 400
    for actor in ("sales.b", "sales.a2", "manager.a", "boss"):
        assert a.client.get(BASE+"/"+draft.json()["id"], headers=a.actors[actor]).status_code in {403,404}
    assert a.client.get(BASE, headers=a.actors["manager.a"]).status_code == 403
    foreign = recording(a, actor="sales.b")
    assert create(a, foreign, request_key="foreign-report-key").status_code == 404


def test_unknown_dates_require_explicit_confirmation_and_supplement_is_report_only(access):
    a = access
    row = custom_record(a, day="")
    draft = create(a, row).json()
    body = confirmation(a, draft)
    body["accept_unknown_dates"] = False
    endpoint = BASE+"/"+draft["id"]+"/confirm"
    assert a.client.post(endpoint, headers=a.actors["sales.a"], json=body).status_code == 400
    body["entries"][0]["meeting_date"] = "2026-12-01"
    assert a.client.post(endpoint, headers=a.actors["sales.a"], json=body).status_code == 400
    body["entries"][0]["meeting_date"] = "2026-11-12"
    assert a.client.post(endpoint, headers=a.actors["sales.a"], json=body).status_code == 200
    stored = a.client.get(BASE+"/"+draft["id"], headers=a.actors["sales.a"]).json()
    assert stored["body"]["entries"][0]["date_supplemented"]
    with a.app.state.store.sessions() as session:
        assert session.get(PersonalRecording,row["id"]).body["meeting_date"] == ""
    assert stored["stale_sources"] == []


def test_confirmed_report_is_frozen_and_word_pdf_have_same_content_id(access):
    a = access
    row = custom_record(a, "销售：我负责下周五发送报价。\n客户：预算未定，等法务核查。", day="")
    draft = create(a, row).json()
    endpoint = BASE+"/"+draft["id"]
    assert a.client.get(endpoint+"/export/docx",headers=a.actors["sales.a"]).status_code == 409
    body = confirmation(a,draft)
    body["entries"][0]["note"] = "销售本人判断：先复核预算；未经客户确认。"
    responses = []
    def confirm(_):
        return a.client.post(endpoint+"/confirm",headers=a.actors["sales.a"],json=body)
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses=list(pool.map(confirm,range(2)))
    assert [r.status_code for r in responses] == [200,200]
    result = a.client.get(endpoint,headers=a.actors["sales.a"]).json()
    assert result["version"] == 2
    original = result["body"]
    docx = a.client.get(endpoint+"/export/docx",headers=a.actors["sales.a"])
    pdf = a.client.get(endpoint+"/export/pdf",headers=a.actors["sales.a"])
    assert docx.status_code == pdf.status_code == 200
    assert docx.headers["X-Report-Content-ID"] == pdf.headers["X-Report-Content-ID"] == original["content_id"]
    assert pdf.content.startswith(b"%PDF-")
    text="\n".join(p.text for p in Document(BytesIO(docx.content)).paragraphs)
    assert "时间待确认的会谈" in text and "销售本人判断" in text and body["plan"] in text
    body["plan"]="不能覆盖已确认版本"
    assert a.client.post(endpoint+"/confirm",headers=a.actors["sales.a"],json=body).status_code == 409
    assert a.client.delete(f"/api/workspace/recordings/{row['id']}",headers=a.actors["sales.a"]).status_code == 200
    updated=a.client.get(endpoint,headers=a.actors["sales.a"]).json()
    assert updated["body"]==original and updated["stale_sources"]
    assert a.client.get(endpoint+"/export/pdf",headers=a.actors["sales.a"]).status_code==200
    assert a.client.get(endpoint+"/export/docx",headers=a.actors["sales.a2"]).status_code==404


def test_source_and_followup_changes_block_stale_draft_but_allow_new_revision(access):
    a=access
    row=custom_record(a)
    draft=create(a,row).json()
    body=confirmation(a,draft)
    item=a.client.get("/api/workspace/business/followups?scope=personal",headers=a.actors["sales.a"]).json()[0]
    assert a.client.patch("/api/workspace/business/followups/"+item["id"],headers=a.actors["sales.a"],
        json={"version":item["version"],"status":"done","note":"已向客户发送合成资料"}).status_code==200
    assert a.client.post(BASE+"/"+draft["id"]+"/confirm",headers=a.actors["sales.a"],json=body).status_code==409
    fresh=create(a,row,request_key="fresh-report-key").json()
    fresh_body=confirmation(a,fresh)
    assert "已完成反馈" in fresh_body["entries"][0]["next_steps"]
    assert a.client.post(BASE+"/"+fresh["id"]+"/confirm",headers=a.actors["sales.a"],json=fresh_body).status_code==200
    child=create(a,row,parent_id=fresh["id"],request_key="child-report-key").json()
    assert child["parent_id"]==fresh["id"]


def test_sources_deduplicate_submitted_recording_and_fallback_after_private_removal(access):
    a=access
    row=custom_record(a)
    source=publish(a,row)
    url=BASE+"/sources?start=2026-11-01&end=2026-11-30"
    candidates=a.client.get(url,headers=a.actors["sales.a"]).json()
    assert len(candidates)==1 and candidates[0]["kind"]=="personal"
    a.client.delete(f"/api/workspace/recordings/{row['id']}",headers=a.actors["sales.a"])
    candidates=a.client.get(url,headers=a.actors["sales.a"]).json()
    assert len(candidates)==1 and candidates[0]["kind"]=="center"
    assert candidates[0]["id"]==source["id"]


def test_saved_draft_preserves_mapping_and_unknown_date_edit_until_confirmation(access):
    a=access
    row=custom_record(a,day="")
    draft=create(a,row).json()
    payload=confirmation(a,draft)
    payload["entries"][0]["summary"]="销售核对的摘要，尚非客户签署确认。"
    payload["entries"][0]["meeting_date"]="2026-11-12"
    endpoint=BASE+"/"+draft["id"]
    saved=a.client.patch(endpoint+"/draft",headers=a.actors["sales.a"],json=payload)
    assert saved.status_code==200
    assert a.client.patch(endpoint+"/draft",headers=a.actors["sales.a"],json=payload).status_code==409
    data=a.client.get(endpoint,headers=a.actors["sales.a"]).json()
    assert data["confirmed_at"] is None
    assert data["body"]["entries"][0]["mapped"]["meeting_date"]==""
    assert "会谈摘要" in data["body"]["entries"][0]["edits"]
    payload["version"]=data["version"]
    payload["entries"][0]["meeting_date"]=""  # User may decide that the provisional date is still uncertain.
    assert a.client.post(endpoint+"/confirm",headers=a.actors["sales.a"],json=payload).status_code==200
    data=a.client.get(endpoint,headers=a.actors["sales.a"]).json()
    assert "会谈摘要" in data["body"]["entries"][0]["edits"]



def test_seller_supplements_map_to_report_without_rewriting_old_confirmation_or_center(access):
    a = access
    row = custom_record(a)
    store = a.app.state.store
    report = store.latest_report(row['run_id'])['body']
    report['questions'] = [{'key':'owner','text':'负责人需要核对','evidence':[],'options':[],'affected_actions':[]}]
    store.save_report(row['run_id'],report)
    old = create(a,row).json()
    old_confirmed = a.client.post(BASE+'/'+old['id']+'/confirm',headers=a.actors['sales.a'],
        json=confirmation(a,old)).json()
    assert old_confirmed['content_id']
    old_confirmed = a.client.get(BASE+'/'+old['id'],headers=a.actors['sales.a']).json()
    center = publish(a,row)
    answer = '由本人跟进，未经客户确认'
    changed = a.client.post(f"/api/workspace/recordings/{row['id']}/clarifications",headers=a.actors['sales.a'],
        json={'version':1,'answers':{'owner':answer}})
    assert changed.status_code == 201, changed.text
    assert changed.json()['run_id'] == row['run_id'] and changed.json()['version'] == 2
    assert store.latest_report(row['run_id'])['body'] == report
    row['version'] = 2
    fresh = create(a,row,request_key='supplements-mapped').json()
    supplements = fresh['body']['entries'][0]['supplements']
    assert supplements == [{'question':'负责人需要核对','answer':answer,'context':'当前分析的本人补充'}]
    confirmed = a.client.post(BASE+'/'+fresh['id']+'/confirm',headers=a.actors['sales.a'],
        json=confirmation(a,fresh))
    assert confirmed.status_code == 200, confirmed.text
    exported = a.client.get(BASE+'/'+fresh['id']+'/export/docx',headers=a.actors['sales.a'])
    assert answer in '\n'.join(p.text for p in Document(BytesIO(exported.content)).paragraphs)
    unchanged = a.client.get(BASE+'/'+old['id'],headers=a.actors['sales.a']).json()
    assert unchanged['body'] == old_confirmed['body'] and unchanged['stale_sources']
    center_read = a.client.get('/api/workspace/center/'+center['id'],headers=a.actors['manager.a']).json()
    assert center_read['clarifications'] == []



def test_legacy_report_source_fingerprint_survives_additive_transport_metadata(access):
    a = access
    row = custom_record(a)
    draft = create(a,row).json()
    reports = a.app.state.reports
    with reports.sessions.begin() as session:
        account = session.scalar(select(Account).where(Account.username=='sales.a'))
        user = {k:getattr(account,k) for k in ('id','name','role','team_id','company_id')}
        source = reports.sources(session,user)['personal:'+row['id']]
        legacy = {k:v for k,v in source.items() if k not in {'clarifications','run_id'}}
        stamp = digest({'source':legacy,'followups':reports.followups(session,user,source)})
        saved = session.get(SalesReport,draft['id'])
        body = dict(saved.body)
        body['entries'] = [dict(e,stamp=stamp) for e in body['entries']]
        saved.body = body
    detail = a.client.get(BASE+'/'+draft['id'],headers=a.actors['sales.a']).json()
    assert detail['stale_sources']==[]
    response = a.client.post(BASE+'/'+draft['id']+'/confirm',headers=a.actors['sales.a'],json=confirmation(a,draft))
    assert response.status_code==200,response.text



def test_note_history_stays_visible_but_is_not_current_after_transcript_revision(access):
    a = access
    row = custom_record(a)
    store = a.app.state.store
    body = store.latest_report(row['run_id'])['body']
    body['questions'] = [{'key':'date:0','text':'原报价日期待确认','evidence':[],'options':[],'affected_actions':[]}]
    store.save_report(row['run_id'],body)
    path = '/api/workspace/recordings/'+row['id']
    answer = '旧会谈的本人补充'
    first = a.client.post(path+'/clarifications',headers=a.actors['sales.a'],json={'version':1,'answers':{'date:0':answer}})
    assert first.status_code==201,first.text
    changed = a.client.post(path+'/transcript',headers=a.actors['sales.a'],json={'version':2,
        'text':'销售：我下周五负责安排新的产品演示。','reason':'修正合成转写内容'})
    assert changed.status_code==201,changed.text
    store_run = changed.json()['run_id']
    a.app.state.driver.process(store_run)
    current = store.latest_report(store_run)['body']
    current['questions'] = [{'key':'date:0','text':'新演示日期待确认','evidence':[],'options':[],'affected_actions':[]}]
    store.save_report(store_run,current)
    row['version'] = 3
    prior = create(a,row,request_key='historical-note-context').json()
    assert prior['body']['entries'][0]['supplements'][0]['context']=='历史补充，适用性待核对'
    # An identical answer may be explicitly reconfirmed for another analysis.
    again = a.client.post(path+'/clarifications',headers=a.actors['sales.a'],json={'version':3,'answers':{'date:0':answer}})
    assert again.status_code==201,again.text
    row['version'] = 4
    fresh = create(a,row,request_key='reconfirmed-note-context').json()
    assert [s['context'] for s in fresh['body']['entries'][0]['supplements']]==[
        '历史补充，适用性待核对','当前分析的本人补充']
