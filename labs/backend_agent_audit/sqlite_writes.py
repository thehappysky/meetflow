"""One-engine contention probe, not a PostgreSQL comparison or complete HTTP load test."""
import hashlib
import json
import math
import random
import statistics
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path

from meetflow.config import Settings
from meetflow.database import Audit, make_database
from sqlalchemy import func, select

ROOT=Path(__file__).resolve().parents[2]


def run():
    out=ROOT/'test-results/backend-agent-audit/sqlite-writes'/datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')
    out.mkdir(parents=True,exist_ok=False)
    manifest={'status':'running','script_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              'writers':[1,4,16],'repeats':3,'transactions_per_trial':256,'sqlite_busy_timeout_seconds':30,
              'scope':'isolated SQLite WAL audit-row inserts; not HTTP or complete application throughput'}
    (out/'manifest.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
    order=[(writers,repeat) for writers in (1,4,16) for repeat in range(3)]
    random.Random(20261112).shuffle(order)
    results=[]
    for writers,repeat in order:
        with tempfile.TemporaryDirectory(prefix='meetflow-write-audit-') as directory:
            engine,sessions=make_database(Settings(data_dir=Path(directory),database_url=f'sqlite:///{Path(directory)/"bench.db"}'))
            per_worker=math.ceil(256/writers)
            def worker(number, *, per_worker=per_worker, sessions=sessions):
                observations=[]
                for index in range(per_worker):
                    started=time.perf_counter();error=None
                    try:
                        with sessions.begin() as session:
                            session.add(Audit(action='benchmark',target_id=f'{number}:{index}',body={'synthetic':True}))
                    except Exception as exc:  # noqa: BLE001 -- count every failed write
                        error={'type':type(exc).__name__,'message':str(exc)}
                    observations.append({'seconds':time.perf_counter()-started,'error':error})
                return observations
            started=time.perf_counter()
            with ThreadPoolExecutor(max_workers=writers) as pool:
                observations=[r for batch in pool.map(worker,range(writers)) for r in batch]
            elapsed=time.perf_counter()-started
            with sessions() as session:
                persisted=session.scalar(select(func.count()).select_from(Audit))
            times=sorted(r['seconds'] for r in observations)
            row={'writers':writers,'repeat':repeat,'attempted':len(observations),'persisted':persisted,
                'failures':sum(r['error'] is not None for r in observations),'wall_seconds':elapsed,
                'writes_per_second':persisted/elapsed,'p50_ms':statistics.median(times)*1000,
                'p95_ms':times[math.ceil(.95*len(times))-1]*1000,'observations':observations}
            results.append(row)
            engine.dispose()
    (out/'results.json').write_text(json.dumps({'scope':'isolated local SQLite WAL, one insert per transaction, no HTTP/auth/LLM/ASR workload',
        'results':results},indent=2),encoding='utf-8')
    manifest['status']='completed'
    (out/'manifest.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
    print(json.dumps({'directory':str(out),'results':[{k:v for k,v in r.items() if k!='observations'} for r in results]}),flush=True)


if __name__=='__main__':run()
