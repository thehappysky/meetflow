"""Publish counters only; raw model messages, credentials and business data stay local."""
import hashlib
import json
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def read(path):
    return json.loads(path.read_text(encoding='utf8'))


def main():
    cohorts = []
    for folder in sorted((ROOT / 'test-results/backend-agent-audit/live-workflow').glob('20261114T*')):
        manifest = read(folder / 'manifest.json')
        if manifest['status'] != 'completed':
            continue
        rows = read(folder / 'results.json')
        cohorts.append({'run':folder.name, 'revision':manifest.get('requested_revision'),
            'model':manifest['model'], 'case_set':manifest['case_set'],
            'cases':len(rows), 'reports':sum(bool(r.get('report')) for r in rows),
            'all_checks_passed':sum(bool(r.get('checks')) and all(r['checks'].values()) for r in rows),
            'model_calls':sum(r['model_calls'] for r in rows),
            'input_tokens':sum(r.get('transport_input_tokens',0) for r in rows),
            'output_tokens':sum(r.get('transport_output_tokens',0) for r in rows),
            'wall_seconds':sum(r['wall_seconds'] for r in rows),
            'limits':manifest['limits'], 'request_timeout':manifest['model_timeout'],
            'source_hashes':{k:v for k,v in manifest['source_hashes'].items() if 'evidence_pipeline' in k or 'cases.json' in k},
            'results':[{'case':r['case'], 'status':r['status'], 'checks':r.get('checks',{}),
                'model_calls':r['model_calls'], 'wall_seconds':r['wall_seconds']} for r in rows]})
    folder = ROOT / 'test-results/company-capacity/20261114T023008066853Z'
    capacity = read(folder / 'manifest.json')
    speech_folder = ROOT / 'test-results/speech-comparison/20261113T051328986460Z'
    speech = read(speech_folder / 'results.json')
    assert len(speech) == 24 and all(row['status'] == 'completed' for row in speech)
    audio_seconds = sum(row['seconds'] for row in speech)
    elapsed = sum(row['wall_seconds'] for row in speech)
    observed_rtf = elapsed / audio_seconds
    speech_baseline_cer = sum(row['edits'] for row in speech) / sum(row['reference_chars'] for row in speech)
    asr_candidates = []
    for path in sorted((ROOT / 'test-results/speech-comparison').glob('20261114T*')):
        manifest = read(path / 'manifest.json')
        if manifest['status'] == 'running':
            continue
        rows = read(path / 'results.json')
        completed = [row for row in rows if row['status'] == 'completed']
        duration = sum(row['wall_seconds'] for row in rows)
        cer = sum(row['edits'] for row in completed) / sum(row['reference_chars'] for row in completed) if completed else None
        gates = {'all_24_completed':len(completed) == len(speech) == len(rows),
            'same_annotation_manifest':manifest['clips_sha256'] == read(speech_folder / 'manifest.json')['clips_sha256'],
            'all_timestamps_valid':bool(completed) and all(row['timestamps_valid'] for row in completed),
            'cer_improvement_at_least_5pp':cer is not None and speech_baseline_cer - cer >= .05,
            'wall_time_at_most_2x':duration <= elapsed * 2}
        asr_candidates.append({'run':path.name,'status':manifest['status'],'asr':manifest['asr'],
            'clips_completed':len(completed),'requested_clips':manifest['count'],'weighted_cer':cer,
            'baseline_weighted_cer':speech_baseline_cer,'wall_seconds':duration,
            'baseline_seconds':elapsed,'gates':gates,'passes_engineering_gate':all(gates.values()),
            'results':[{k:row.get(k) for k in ('id','status','seconds','wall_seconds','edits','reference_chars','timestamps_valid')}
                for row in rows]})
    payload = {'scope':'Authored development regression, one local host; no independent business accuracy',
        'limitations':['The same 15+7 cases recur; run counts are not independent meeting counts.',
            'Report completion is distinct from all narrow checks passing.',
            'Metadata load has inference disabled and is not ASR/LLM throughput or an enterprise SLA.',
            'V4 request timeout was 90 seconds; V3/state_v2 used 180 seconds.'],
        'cohorts':cohorts,
        'asr_candidates':asr_candidates,
        'metadata_load':{'run':folder.name, **{k:capacity[k] for k in
            ('status','sales_accounts','teams','inference_enabled','checks','groups')}},
        'asr_projection':{'basis_run':speech_folder.name, 'clips':24, 'public_non_sales_meetings':8,
            'model':'Whisper small CPU int8', 'audio_seconds':audio_seconds,
            'observed_seconds':elapsed, 'observed_rtf':observed_rtf,
            'scope':'Projection from short clips, not measured 15/30/60-minute recordings or end-to-end capacity',
            'excludes':['LLM','queueing','upload','retries','speaker identification','long-audio performance changes'],
            'scenarios':[{'recordings_per_day':n,'minutes_each':minutes,
                'audio_hours':n*minutes/60, 'projected_asr_worker_hours':n*minutes/60*observed_rtf,
                'projected_workers_for_8h_at_60pct':math.ceil(n*minutes/60*observed_rtf/(8*.6))}
                for n in (50,100,300) for minutes in (15,30,60)]},
        'docx_pagination':{'engine':'Isolated project-local LibreOffice 26.8.3',
            'input_sha256':hashlib.sha256((ROOT / 'test-results/backend-runtime/20261113T154011973969Z/confirmed.docx').read_bytes()).hexdigest(),
            'pages':2, 'visually_inspected':True, 'clipping_or_missing_glyphs_seen':False,
            'microsoft_word_client_checked':False}}
    runtime = {}
    for name, run, keys in [
        ('supplement-runtime','20261114T043317774396Z',('status','checks','before','after','docx_sha256')),
        ('main-live','20261114T043335444483Z',('status','checks','meeting_mode','quality_mode','wall_seconds')),
        ('backend-runtime','20261114T043411881798Z',('status','checks','model_calls','database_tables','server_starts','report_mode'))]:
        manifest = read(ROOT / 'test-results' / name / run / 'manifest.json')
        runtime[name] = {'run':run, **{k:manifest[k] for k in keys}}
    runtime['supplement-pagination'] = read(ROOT / 'test-results/report-supplement-render-20261114/qa.json')
    payload['actual_runtime'] = runtime
    target = ROOT / 'labs/backend_agent_audit/results/continuation_summary.json'
    target.write_text(json.dumps(payload,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
    print(json.dumps({'output':str(target.relative_to(ROOT)), 'cohorts':len(cohorts)}))


if __name__ == '__main__':
    main()
