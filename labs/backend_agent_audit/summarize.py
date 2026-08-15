"""Recompute public aggregates from all saved trials, without editing original runs."""
import hashlib
import json
from pathlib import Path

from agent_experiment import evaluate
from topology_experiment import metrics as topology_metrics

ROOT=Path(__file__).resolve().parents[2]
RAW=ROOT/'test-results/backend-agent-audit'
OUT=Path(__file__).with_name('results')


def load(path):
    return json.loads(path.read_text(encoding='utf-8'))


def model_identity(manifest):
    row=next((m for m in manifest['models']['models'] if m['name']==manifest['model']),None)
    return {k:row[k] for k in ('name','digest','details')} if row else None


def main():
    OUT.mkdir(exist_ok=True)
    runs=[]
    for folder in sorted((RAW/'agents').iterdir()):
        manifest=load(folder/'manifest.json')
        cases={c['id']:c for c in load(folder/'cases.json')}
        rows=[json.loads(line) for line in (folder/'predictions.jsonl').read_text(encoding='utf-8').splitlines()]
        policy_stats={}
        for policy in sorted({r['policy'] for r in rows}):
            items=[r for r in rows if r['policy']==policy]
            improved=damaged=violations=full_correct=0
            for r in items:
                gold=cases[r['case_id']]['gold'];pred=r['prediction']
                if pred:
                    violations+=pred['action_present']!=(pred['status']!='none') or not pred['action_present'] and (pred['owner'] is not None or pred['due'] is not None)
                    full_correct+=all(pred[k]==gold[k] for k in ('action_present','owner','due','status')) and set(pred['evidence'])==set(gold['evidence']) and set(pred['conditions'])==set(gold['conditions'])
                if policy!='one_pass':
                    before=r['intermediate'][0] if r['intermediate'] else None
                    before_slots=evaluate(before,gold)['slots'];after_slots=evaluate(pred,gold)['slots']
                    improved+=sum(not before_slots[k] and after_slots[k] for k in before_slots)
                    damaged+=sum(before_slots[k] and not after_slots[k] for k in before_slots)
            policy_stats[policy]={'trials':len(items),'distinct_cases':len({r['case_id'] for r in items}),
                'fully_exact_including_evidence_conditions':int(full_correct),'contradictory_fields':int(violations),
                'core_slots_repaired':improved,'core_slots_damaged':damaged,
                'core_net_repair_rate':(improved-damaged)/(4*len(items)) if policy!='one_pass' else None}
        runs.append({'directory':str(folder.relative_to(ROOT)),'model':manifest['model'],
            'model_identity':model_identity(manifest),
            'status':manifest['status'],'data_sha256':manifest['data_sha256'],'plan_sha256':manifest['plan_sha256'],
            'raw_metrics':load(folder/'metrics.json'),'additional_diagnostics':policy_stats,
            'prediction_sha256':hashlib.sha256((folder/'predictions.jsonl').read_bytes()).hexdigest()})
    queries=[{'directory':str(p.parent.relative_to(ROOT)),'manifest':load(p.parent/'manifest.json'),'metrics':load(p)}
             for p in sorted((RAW/'queries').glob('*/metrics.json'))]
    graph=[{'directory':str(p.parent.relative_to(ROOT)),'manifest':load(p.parent/'manifest.json'),'result':load(p)}
           for p in sorted((RAW/'current-graph').glob('*/result.json'))]
    for run in graph:
        trace_path=ROOT/run['directory']/'trace.jsonl'
        counts=[json.loads(line).get('usage',{}).get('completion_tokens')
                for line in trace_path.read_text(encoding='utf-8').splitlines()]
        run['observed_completion_counts']=counts
        run['calls_above_requested_generation_cap']=sum(
            n is not None and n>run['manifest']['generation_cap_per_call'] for n in counts)
    writes=[{'directory':str(p.parent.relative_to(ROOT)),
             'manifest':load(p.parent/'manifest.json') if (p.parent/'manifest.json').exists() else {'status':'exploratory; initial script hash not captured'},
             'results':[{k:v for k,v in r.items() if k!='observations'} for r in load(p)['results']]}
            for p in sorted((RAW/'sqlite-writes').glob('*/results.json'))]
    environment=load(RAW/'environment.json')
    topologies=[]
    for path in sorted((RAW/'topologies').glob('*/metrics.json')):
        manifest=load(path.parent/'manifest.json')
        rows=[json.loads(line) for line in (path.parent/'predictions.jsonl').read_text(encoding='utf-8').splitlines()]
        computed=topology_metrics(rows)
        assert computed==load(path), 'Persisted topology metrics do not match raw trials'
        gold={c['id']:c['gold'] for c in load(path.parent/'cases.json')}
        stage_diagnostics={}
        for policy in computed:
            selected=[r for r in rows if r['policy']==policy]
            lost_first=found_first=discarded_correct=0
            for row in selected:
                first=evaluate(row['drafts'][0]['prediction'],gold[row['case_id']])
                second=evaluate(row['drafts'][1]['prediction'],gold[row['case_id']])
                final=row['evaluation']
                lost_first+=sum(first['slots'][k] and not final['slots'][k] for k in first['slots'])
                found_first+=sum(not first['slots'][k] and final['slots'][k] for k in first['slots'])
                discarded_correct+=(first['all_slots_correct'] or second['all_slots_correct']) and not final['all_slots_correct']
            stage_diagnostics[policy]={'posthoc_explanatory_not_new_selection_metric':True,
                'core_slots_repaired_relative_to_current_expert':found_first,
                'core_slots_damaged_relative_to_current_expert':lost_first,
                'at_least_one_core_correct_expert_but_final_core_wrong':discarded_correct}
        topologies.append({'directory':str(path.parent.relative_to(ROOT)),
            'manifest':{k:v for k,v in manifest.items() if k!='models'},'model_identity':model_identity(manifest),
            'metrics':computed,'stage_diagnostics':stage_diagnostics,
            'prediction_sha256':hashlib.sha256((path.parent/'predictions.jsonl').read_bytes()).hexdigest(),
            'unchecked_full_error_cases':sorted({r['case_id'] for r in rows if not r['review_triggered'] and not r['fully_exact']}),
            'case_results':[{'case_id':r['case_id'],'policy':r['policy'],'repeat':r['repeat'],
                'status':r['status'],'core_correct':r['evaluation']['all_slots_correct'],
                'fully_exact':r['fully_exact'],'review_triggered':r['review_triggered']} for r in rows]})
    summary={'status':'development audit, no independent business gold or holdout',
        'environment_snapshot':{k:environment[k] for k in ('captured_utc','scope','python','platform','packages','ollama_version')},
        'agents':runs,'queries':queries,'multi_agent_topologies':topologies,
        'production_graph_diagnostics':graph,'protocol_simulation':load(RAW/'protocol/metrics.json'),
        'schema_inventory':load(RAW/'protocol/inventory.json'),'capacity':load(RAW/'capacity.json'),'sqlite_writes':writes}
    (OUT/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'agent_runs':len(runs),'trials':sum(sum(v['trials'] for v in r['additional_diagnostics'].values()) for r in runs),
                      'queries':len(queries),'full_graph_probes':len(graph),
                      'topology_runs':len(topologies),'topology_trials':sum(sum(m['cases'] for m in r['metrics'].values()) for r in topologies)}))


if __name__=='__main__':main()
