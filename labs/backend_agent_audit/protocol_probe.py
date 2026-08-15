"""Measure the current production graph's protocol overhead with an explicit simulation provider."""
import json
import sys
import tempfile
import time
from collections import Counter
from pathlib import Path

from sqlalchemy import select

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'tests'))
from meetflow.database import Base, RunEvent
from meetflow.providers import DemoProvider
from test_access import access, recording


class CountingProvider(DemoProvider):
    def __init__(self):
        self.calls=[]

    def complete(self,role,messages,tools,info):
        self.calls.append({'role':role,'message_characters':len(json.dumps(messages,ensure_ascii=False)),
                           'tool_schema_characters':len(json.dumps(tools,ensure_ascii=False))})
        return super().complete(role,messages,tools,info)


def main():
    folder=ROOT/'test-results/backend-agent-audit/protocol'
    folder.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='meetflow-protocol-') as directory:
        fixture=access.__wrapped__(Path(directory));a=next(fixture)
        try:
            provider=CountingProvider();a.app.state.driver.provider=provider
            start=time.perf_counter();row=recording(a);elapsed=time.perf_counter()-start
            run=a.app.state.store.get_run(row['run_id'])
            with a.app.state.store.sessions() as session:
                events=session.scalars(select(RunEvent).where(RunEvent.run_id==row['run_id'])).all()
            tool_calls=[e.payload for e in events if e.kind=='tool']
            full_reads=[e for e in tool_calls if e['tool']=='read_source']
            value={'mode':'rule simulation of actual graph, NOT model accuracy or provider token usage',
                'status':run['status'],'wall_seconds':elapsed,'model_protocol_roundtrips':len(provider.calls),
                'per_role':dict(Counter(c['role'] for c in provider.calls)),
                'tool_calls':dict(Counter(c['tool'] for c in tool_calls)),
                'full_source_reads':len(full_reads),'unique_full_sources':len({e['args']['source_id'] for e in full_reads}),
                'message_characters':sum(c['message_characters'] for c in provider.calls),
                'tool_schema_characters':sum(c['tool_schema_characters'] for c in provider.calls),
                'model_input_tokens':None,'model_output_tokens':None,'calls':provider.calls}
            (folder/'metrics.json').write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8')
            print(json.dumps(value))
        finally:fixture.close()
    inventory={'tables':len(Base.metadata.tables),'json_columns':[], 'foreign_keys':0,'unique_constraints':0,
               'modules':{p.name:len(p.read_text(encoding='utf-8').splitlines()) for p in (ROOT/'backend/meetflow').glob('*.py')}}
    for table in Base.metadata.tables.values():
        inventory['foreign_keys']+=len(table.foreign_keys)
        inventory['unique_constraints']+=sum(c.__class__.__name__=='UniqueConstraint' for c in table.constraints)
        inventory['json_columns'] += [f'{table.name}.{c.name}' for c in table.columns if c.type.__class__.__name__=='JSON']
    (folder/'inventory.json').write_text(json.dumps(inventory,ensure_ascii=False,indent=2),encoding='utf-8')


if __name__=='__main__':main()
