from agent_experiment import aggregate, evaluate, overlap


def test_failed_trial_is_not_dropped_or_counted_as_correct_nulls():
    gold={'action_present':False,'owner':None,'due':None,'status':'none','evidence':[],'conditions':[]}
    result=evaluate(None,gold)
    assert not any(result['slots'].values()) and not result['all_slots_correct']


def test_duplicating_citations_cannot_inflate_evidence_score():
    assert overlap(['s1','s1','wrong'],['s1','s2'])=={'tp':1,'fp':1,'fn':1}


def test_missing_predictions_stay_in_aggregate_denominator():
    gold={'action_present':True,'owner':'A','due':None,'status':'todo','evidence':['s1'],'conditions':[]}
    common={'policy':'p','wall_seconds':1.0,'input_tokens':10,'output_tokens':10,'model_calls':1}
    rows=[{**common,'status':'completed','evaluation':evaluate(gold,gold)},
          {**common,'status':'failed','evaluation':evaluate(None,gold)}]
    result=aggregate(rows)['p']
    assert result['cases']==2 and result['successful']==1 and result['all_slots_correct']==1
    assert result['evidence']=={'tp':1,'fp':0,'fn':1,'f1':2/3}
