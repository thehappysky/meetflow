"""Sensitivity calculations, NOT measured production throughput or queue-engine benchmarks."""
import json
import math
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]


def scenarios():
    return [{"recordings_per_day":count,"minutes_per_recording":minutes,
        "audio_hours_per_day":count*minutes/60,
        "assumed_rtf":rtf,"asr_worker_hours":count*minutes/60*rtf,
        "asr_workers_for_8h_at_60pct":math.ceil(count*minutes/60*rtf/(8*.6)),
        "storage_gb_per_30_days_at_128kbps":count*minutes*60*128000/8/1e9*30}
        for count in (50,100,300) for minutes in (15,30,60) for rtf in (.05,.2,.5)]


if __name__=='__main__':
    out=ROOT/'test-results/backend-agent-audit/capacity.json'
    out.parent.mkdir(parents=True,exist_ok=True)
    out.write_text(json.dumps({"mode":"hypothetical sensitivity, not measured ASR/queue capacity",
        "scope":"one company,20-100 salespeople; user has not fixed recording duration",
        "excluded":["LLM latency","burst arrivals","network upload","retries","ASR quality","backups","audio transcoding"],
        "scenarios":scenarios()},ensure_ascii=False,indent=2),encoding='utf-8')
    print(str(out))
