"""Compare the exact baseline service from Git with the current service on isolated synthetic rows."""
import argparse
import copy
import hashlib
import json
import platform
import random
import statistics
import subprocess
import sys
import tempfile
import time
import types
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import event, insert, select

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests"))
from meetflow.database import (
    Account,
    PersonalRecording,
    QualityCase,
    QualityReview,
    Run,
    SalesAnalysis,
    SalesConversation,
    Submission,
    row_dict,
)
from test_access import access, submitted


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def fingerprint(value):
    return hashlib.sha256(value).hexdigest()


def baseline_class(revision):
    source = subprocess.check_output(["git", "show", f"{revision}:backend/meetflow/quality_workspace.py"], cwd=ROOT)
    module = types.ModuleType("meetflow._audit_baseline_quality")
    module.__package__ = "meetflow"
    sys.modules[module.__name__] = module
    exec(compile(source, f"git:{revision}:quality_workspace.py", "exec"), module.__dict__)  # noqa: S102 -- exact audited local Git baseline
    return module.QualityWorkspace, fingerprint(source)


def seed(a, size):
    templates = []
    for actor in ("sales.a", "sales.b"):
        rec, sub = submitted(a, actor, "benchmark-"+actor)
        with a.app.state.store.sessions() as session:
            key = session.scalar(select(QualityCase.id).where(QualityCase.source_id == sub["id"]))
            analysis = session.get(QualityCase, key).active_analysis_id
        a.app.state.sales_driver.process(analysis)
        with a.app.state.store.sessions() as session:
            recording = session.get(PersonalRecording, rec["id"])
            qc = session.get(QualityCase, key)
            sa = session.get(SalesAnalysis, qc.active_analysis_id)
            templates.append({cls: row_dict(row) for cls, row in [
                (PersonalRecording, recording), (Run, session.get(Run, recording.run_id)),
                (Submission, session.get(Submission, sub["id"])), (QualityCase, qc), (SalesAnalysis, sa),
                (SalesConversation, session.get(SalesConversation, sa.conversation_id))]})
    data = defaultdict(list)
    for i in range(2, size):
        rows = copy.deepcopy(templates[i % 2])
        identities = {cls: f"bench-{cls.__name__}-{i:06}" for cls in rows}
        for cls, body in rows.items():
            body["id"] = identities[cls]
            if "created_at" in body:
                body["created_at"] = f"2026-11-12T00:00:{i//10000:02}.{i%10000:06}+00:00"
        rows[Run]["manifest"]["benchmark"] = True
        rows[PersonalRecording]["run_id"] = identities[Run]
        rows[Submission].update(recording_id=identities[PersonalRecording], request_key=f"bench-request-{i}")
        rows[Submission]["snapshot"]["run_id"] = identities[Run]
        rows[SalesAnalysis]["conversation_id"] = identities[SalesConversation]
        rows[QualityCase].update(source_id=identities[Submission], scope_key=f"center:{identities[Submission]}:v1",
                                analysis_ids=[identities[SalesAnalysis]], active_analysis_id=identities[SalesAnalysis])
        # Frozen bodies deliberately include long non-list fields, so the projection's byte savings are measurable.
        spans = rows[QualityCase]["body"]["spans"]
        for j in range(20):
            span = {"id": f"extra{j}", "source_ref": f"bench:{i}:{j}", "text": "合成背景，用于数据库负载而非模型评价。" * 20,
                    "speaker": "sales", "start": None, "end": None}
            spans.append(span)
            rows[Submission]["snapshot"]["evidence"][span["source_ref"]] = {
                "id": span["source_ref"], "text": span["text"], "source_kind": "transcript", "source_version": 1}
        rows[SalesAnalysis]["snapshot"]["conversation"]["body"]["spans"] = [{k:v for k,v in span.items() if k != "source_ref"} for span in spans]
        if i % 3 == 0:
            rows[SalesAnalysis]["status"] = "reviewed"
            data[QualityReview].append({"id": f"bench-review-{i}", "case_id": identities[QualityCase],
                "analysis_id": identities[SalesAnalysis], "actor_id": "admin", "request_hash": f"bench-review-hash-{i}",
                "body": {"metrics": rows[SalesAnalysis]["report"]["metrics"], "decisions": rows[SalesAnalysis]["report"]["decisions"], "note": "synthetic benchmark"}})
        for cls, row in rows.items():
            data[cls].append(row)
    with a.app.state.store.sessions.begin() as session:
        for cls in (Run, PersonalRecording, SalesConversation, SalesAnalysis, Submission, QualityCase, QualityReview):
            if data[cls]:
                session.execute(insert(cls), data[cls])


def run(args):
    folder = Path(args.output) / datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    folder.mkdir(parents=True, exist_ok=False)
    baseline, baseline_hash = baseline_class(args.baseline_revision)
    manifest = {"status": "running", "args": vars(args), "baseline_source_sha256": baseline_hash,
        "candidate_source_sha256": fingerprint((ROOT/"backend/meetflow/quality_workspace.py").read_bytes()),
        "script_sha256": fingerprint(Path(__file__).read_bytes()),
        "plan_sha256": fingerprint(Path(__file__).with_name("PLAN.md").read_bytes()),
        "platform": platform.platform(), "python": sys.version, "backend": "SQLite WAL, isolated synthetic fixture",
        "git_head": subprocess.check_output(["git","rev-parse","HEAD"],cwd=ROOT,text=True).strip()}
    save(folder/"manifest.json", manifest)
    results, failures = [], []
    for size in args.sizes:
        with tempfile.TemporaryDirectory(prefix="meetflow-query-audit-") as directory:
            fixture = access.__wrapped__(Path(directory))
            a = next(fixture)
            try:
                seed(a, size)
                original = baseline(a.app.state.workspace, a.app.state.sales, a.app.state.sales_driver)
                current = a.app.state.quality
                engine = a.app.state.store.sessions.kw["bind"]
                counter = {"selects": 0}
                def count(_connection, _cursor, statement, *_args, counter=counter):
                    if statement.lstrip().upper().startswith(("SELECT", "WITH")):
                        counter["selects"] += 1
                event.listen(engine, "before_cursor_execute", count)
                with a.app.state.store.sessions() as session:
                    actors = {name: {k: getattr(account, k) for k in ("id","role","company_id","team_id")}
                        for name in ("manager.a", "boss") for account in [session.scalar(select(Account).where(Account.username == name))]}
                for actor, user in actors.items():
                    # Equivalence is mandatory, not inferred from matching lengths.
                    left, right = original.listing(user,"center"), current.listing(user,"center")
                    if left != right:
                        save(folder/f"mismatch-{size}-{actor}.json", {"baseline": left,"candidate": right})
                        raise AssertionError("Candidate response differs from baseline")
                    for repeat in range(args.repeats):
                        order = [("baseline", original), ("bulk_projection", current)]
                        random.Random(size*100+repeat).shuffle(order)
                        for label, service in order:
                            counter["selects"] = 0
                            start = time.perf_counter()
                            output = service.listing(user, "center")
                            payload = json.dumps(output, ensure_ascii=False, separators=(",", ":")).encode()
                            result = {"size":size,"actor":actor,"strategy":label,"repeat":repeat,
                                "wall_ms":1000*(time.perf_counter()-start),"selects":counter["selects"],
                                "bytes":len(payload),"returned":len(output["cases"]),"payload_sha256":fingerprint(payload)}
                            results.append(result)
                            with (folder/"trace.jsonl").open("a",encoding="utf-8") as stream:
                                stream.write(json.dumps(result)+"\n")
                event.remove(engine, "before_cursor_execute", count)
            except Exception as exc:  # noqa: BLE001 -- retain failed benchmark sizes, never omit them
                failures.append({"size":size,"type":type(exc).__name__,"message":str(exc)})
                save(folder/"failures.json", failures)
            finally:
                fixture.close()
        print(json.dumps({"completed_size":size,"failures":len(failures)}),flush=True)
    metrics=[]
    for size in args.sizes:
        for actor in ("manager.a","boss"):
            for strategy in ("baseline","bulk_projection"):
                rows=[r for r in results if r["size"]==size and r["actor"]==actor and r["strategy"]==strategy]
                if not rows:
                    continue
                times=sorted(r["wall_ms"] for r in rows)
                metrics.append({"size":size,"actor":actor,"strategy":strategy,"samples":len(rows),
                    "p50_ms":statistics.median(times),"p95_ms":times[max(0,int(.95*len(times)+.999)-1)],
                    "selects":sorted({r["selects"] for r in rows}),"bytes":rows[0]["bytes"],"returned":rows[0]["returned"]})
    save(folder/"metrics.json",metrics)
    manifest.update(status="completed" if not failures else "completed_with_failures", failures=len(failures))
    save(folder/"manifest.json",manifest)
    print(json.dumps({"folder":str(folder),"metrics":metrics,"failures":failures}),flush=True)


if __name__ == "__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("--baseline-revision",default="e0fa85a")
    parser.add_argument("--sizes",type=int,nargs="+",default=[10,100,500])
    parser.add_argument("--repeats",type=int,default=7)
    parser.add_argument("--output",default="test-results/backend-agent-audit/queries")
    run(parser.parse_args())
