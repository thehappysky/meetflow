"""Reproducible audio smoke benchmark; real ASR and explicitly demo-only minutes.

Usage: python scripts/run_audio_benchmark.py prepare|run [--url URL]
Reference CER serializes overlapping utterances by start time; not official M2MeT scoring.
"""
import argparse
import json
import shutil
import tarfile
import time
import unicodedata
import wave
from itertools import pairwise
from pathlib import Path, PurePosixPath

import httpx

ROOT = Path(__file__).resolve().parents[1] / "data" / "benchmarks"


def normalized(text):
    text = unicodedata.normalize("NFKC", text).lower()
    return "".join(char for char in text if char.isalnum())


def edits(reference, hypothesis):
    previous = list(range(len(hypothesis) + 1))
    for i, left in enumerate(reference, 1):
        current = [i]
        for j, right in enumerate(hypothesis, 1):
            current.append(min(current[-1] + 1, previous[j] + 1, previous[j - 1] + (left != right)))
        previous = current
    return previous[-1]


def safe_target(root, name):
    relative = PurePosixPath(name)
    target = (root / relative).resolve()
    if relative.is_absolute() or ".." in relative.parts or not target.is_relative_to(root.resolve()):
        raise ValueError("Unsafe archive member")
    return target


def prepare():
    import numpy as np
    from praatio import textgrid

    extracted = ROOT / "alimeeting" / "extracted"
    extracted.mkdir(parents=True, exist_ok=True)
    with tarfile.open(ROOT / "alimeeting" / "Eval_Ali.tar.gz", "r|gz") as archive:
        for member in archive:
            # Keep all annotations, but only far-field waveform copies. Full archive includes near-field audio.
            if not member.isfile() or not (member.name.lower().endswith(".textgrid") or
                                          "/Eval_Ali_far/audio_dir/" in member.name):
                continue
            target = safe_target(extracted, member.name)
            if target.is_file() and target.stat().st_size == member.size:
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.extractfile(member) as source, target.open("wb") as destination:
                shutil.copyfileobj(source, destination, 4 * 1024 * 1024)
            print(f"Extracted {member.name}", flush=True)
    far = extracted / "Eval_Ali" / "Eval_Ali_far"
    clips, meetings = [], []
    for audio in sorted((far / "audio_dir").glob("*.wav")):
        grids = list(far.rglob(audio.stem.rsplit("_", 1)[0] + ".TextGrid"))
        if len(grids) != 1:
            raise ValueError(f"Expected exactly one reference for {audio.name}: {grids}")
        grid = textgrid.openTextgrid(str(grids[0]), includeEmptyIntervals=False)
        intervals = sorted((float(entry.start), float(entry.end), entry.label, tier.name)
                           for tier in grid.tiers for entry in tier.entries if entry.label.strip())
        active = []
        for start, end, _, _ in intervals:
            if active and start <= active[-1][1]:
                active[-1][1] = max(active[-1][1], end)
            else:
                active.append([start, end])
        with wave.open(str(audio), "rb") as source:
            rate, channels, width = source.getframerate(), source.getnchannels(), source.getsampwidth()
            duration = source.getnframes() / rate
            if width != 2:
                raise ValueError("Expected PCM16")
            boundaries = [0.0, *[(left[1] + right[0]) / 2 for left, right in pairwise(active)], duration]
            meetings.append({"meeting": audio.stem, "seconds": duration, "channels": channels,
                             "reference": str(grids[0].relative_to(ROOT)), "speakers": len(grid.tiers)})
            for position, fraction in [("early", 0.05), ("middle", 0.45), ("late", 0.8)]:
                start = min(boundaries, key=lambda x: abs(x - duration * fraction))
                possible = [end for end in boundaries if end >= start + 20]
                if not possible:
                    raise ValueError("No valid end boundary")
                end = min(possible, key=lambda x: abs(x - start - 60))
                # Round boundaries to actual audio frames, keeping references wholly inside the clip.
                first, last = round(start * rate), round(end * rate)
                start, end = first / rate, last / rate
                source.setpos(first)
                frames = np.frombuffer(source.readframes(last - first), dtype="<i2").reshape(-1, channels)
                target = ROOT / "clips" / f"{audio.stem}_{position}.wav"
                target.parent.mkdir(parents=True, exist_ok=True)
                with wave.open(str(target), "wb") as output:
                    output.setnchannels(1)
                    output.setsampwidth(width)
                    output.setframerate(rate)
                    output.writeframes(frames[:, 0].copy().tobytes())
                selected = [(a, b, text, speaker) for a, b, text, speaker in intervals
                            if a >= start - 0.001 and b <= end + 0.001 and b > start and a < end]
                if not selected:
                    raise ValueError("Empty reference clip")
                if any(a < start < b or a < end < b for a, b, _, _ in intervals):
                    raise ValueError("Clip boundary intersects a reference utterance")
                clip = {"id": target.stem, "meeting": audio.stem, "position": position,
                        "path": str(target.relative_to(ROOT)), "offset": start, "seconds": end - start,
                        "source_channel": 0, "rate": rate, "reference": "".join(item[2] for item in selected),
                        "intervals": selected,
                        "has_overlap": any(b > c for (a, b, _, _), (c, d, _, _) in pairwise(selected))}
                clips.append(clip)
    if len(meetings) != 8 or len(clips) != 24:
        raise ValueError(f"Unexpected Eval layout: {len(meetings)} meetings / {len(clips)} clips")
    (ROOT / "clips.json").write_text(json.dumps({"meetings": meetings, "clips": clips},
                                               ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Prepared {len(clips)} clips / {sum(c['seconds'] for c in clips):.1f}s", flush=True)


def run(url):
    manifest = json.loads((ROOT / "clips.json").read_text(encoding="utf-8"))
    result_path = ROOT / "results.json"
    results = json.loads(result_path.read_text(encoding="utf-8")) if result_path.exists() else []
    completed = {row["id"] for row in results if row.get("passed")}
    with httpx.Client(base_url=url, timeout=180) as client:
        health = client.get("/api/health").raise_for_status().json()
        if not health["audio_available"]:
            raise ValueError("Configure local ASR and restart the app first")
        projects = client.get("/api/projects").raise_for_status().json()
        name = "录音测试 · AliMeeting Eval（公开会议，非销售）"
        project = next((p for p in projects if p["name"] == name), None)
        project = project or client.post("/api/projects", json={"name": name}).raise_for_status().json()
        for clip in manifest["clips"]:
            if clip["id"] in completed:
                continue
            begun = time.perf_counter()
            print(f"Testing {clip['id']} ({clip['seconds']:.1f}s)", flush=True)
            with (ROOT / clip["path"]).open("rb") as source:
                response = client.post("/api/meetings/audio", data={"project_id": project["id"],
                    "title": "公开会议抽测 · " + clip["id"], "mode": "demo"},
                    files={"file": (clip["id"] + ".wav", source, "audio/wav")})
            response.raise_for_status()
            key = response.json()["run"]["id"]
            detail = None
            for _ in range(1800):
                detail = client.get(f"/api/runs/{key}").raise_for_status().json()
                if detail["run"]["status"] not in {"queued", "running"}:
                    break
                time.sleep(1)
            elapsed = time.perf_counter() - begun
            spans = [s for s in detail["evidence"].values() if s["source_kind"] == "transcript"]
            hypothesis = "".join(span["text"] for span in spans)
            reference, hypothesis_clean = normalized(clip["reference"]), normalized(hypothesis)
            error_count = edits(reference, hypothesis_clean)
            timestamps_valid = bool(spans) and all(0 <= span["start"] <= span["end"] <= clip["seconds"] + 0.1
                                                    for span in spans)
            report = detail.get("report")
            refs = {ref for item in (report or {}).get("body", {}).get("claims", []) for ref in item["evidence"]}
            range_response = client.get(f"/api/meetings/{detail['meeting']['id']}/audio",
                                        headers={"Range": "bytes=0-1023"})
            exported = client.get(f"/api/runs/{key}/export")
            passed = (detail["run"]["status"] == "waiting_review" and timestamps_valid
                      and bool((report or {}).get("body", {}).get("summary"))
                      and bool(refs) and refs <= set(detail["evidence"])
                      and range_response.status_code == 206 and exported.status_code == 200
                      and "原文依据" in exported.text)
            events = client.get(f"/api/runs/{key}/event-log").raise_for_status().json()
            result = {"id": clip["id"], "run_id": key, "meeting_id": detail["meeting"]["id"],
                      "status": detail["run"]["status"], "passed": passed,
                      "seconds": clip["seconds"], "pipeline_seconds": elapsed,
                      "reference_chars": len(reference), "edit_distance": error_count,
                      "cer": error_count / len(reference) if reference else None,
                      "segments": len(spans), "timestamps_valid": timestamps_valid,
                      "range_status": range_response.status_code, "error": detail["run"]["error"],
                      "has_overlap": clip["has_overlap"], "events": events,
                      "asr_transcript": hypothesis, "mode": "real ASR + demo-only minutes"}
            results.append(result)
            result_path.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
            export_dir = ROOT / "exports"
            export_dir.mkdir(exist_ok=True)
            if exported.status_code == 200:
                (export_dir / (clip["id"] + ".md")).write_text(exported.text, encoding="utf-8")
            print(f"{clip['id']}: passed={passed} CER={result['cer']:.3f} elapsed={elapsed:.1f}s", flush=True)
            if detail["run"]["status"] == "failed":
                raise RuntimeError("Pipeline failed; inspect results.json before repeating the batch")
    print(f"Results saved: {result_path}", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["prepare", "run"])
    parser.add_argument("--url", default="http://127.0.0.1:8765")
    args = parser.parse_args()
    prepare() if args.action == "prepare" else run(args.url)
