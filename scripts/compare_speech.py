"""Real fixed-clip speech diagnostic. Raw audio/text stay in ignored test-results."""
import argparse
import hashlib
import json
import platform
import time
from datetime import UTC, datetime
from pathlib import Path

from meetflow.config import Settings
from meetflow.speech import transcribe
from run_audio_benchmark import edits, normalized

ROOT = Path(__file__).resolve().parents[1]


def speaker_error(clip, turns):
    """10ms diagnostic DER, optimal label matching, collar .25, no ref overlap."""
    import numpy as np
    step, collar = .01, .25
    times = np.arange(step / 2, clip['seconds'], step)
    refs = sorted({r[3] for r in clip['intervals']})
    systems = sorted({r['speaker'] for r in turns})
    reference = np.zeros((len(refs), len(times)), dtype=bool)
    hypothesis = np.zeros((len(systems), len(times)), dtype=bool)
    valid = np.ones(len(times), dtype=bool)
    for start, end, _, speaker in clip['intervals']:
        start, end = start - clip['offset'], end - clip['offset']
        reference[refs.index(speaker)] |= (times >= start) & (times < end)
        valid &= (abs(times - start) > collar) & (abs(times - end) > collar)
    valid &= reference.sum(axis=0) <= 1
    for turn in turns:
        hypothesis[systems.index(turn['speaker'])] |= (times >= turn['start']) & (times < turn['end'])
    weights = [[int((reference[i] & hypothesis[j] & valid).sum()) for j in range(len(systems))]
               for i in range(len(refs))]
    # DP over the small reference-speaker set; extra predicted clusters stay unmatched.
    states = {0: (0, [])}
    for j in range(len(systems)):
        updated = dict(states)
        for mask, (score, mapping) in states.items():
            for i in range(len(refs)):
                if mask & (1 << i):
                    continue
                newmask, value = mask | (1 << i), score + weights[i][j]
                if value > updated.get(newmask, (-1, []))[0]:
                    updated[newmask] = value, [*mapping, (i, j)]
        states = updated
    correct = max(states.values(), key=lambda state: state[0])[0]
    nref, nsys = reference.sum(axis=0)[valid], hypothesis.sum(axis=0)[valid]
    miss = int(np.maximum(nref - nsys, 0).sum())
    false = int(np.maximum(nsys - nref, 0).sum())
    confusion = int(np.minimum(nref, nsys).sum()) - correct
    denominator = int(nref.sum())
    return {'miss_frames': miss, 'false_frames': false, 'confusion_frames': confusion,
            'reference_frames': denominator, 'excluded_seconds': float((~valid).sum() * step),
            'diagnostic_der': (miss + false + confusion) / denominator if denominator else None,
            'reference_speakers': len(refs), 'predicted_speakers': len(systems)}


def main(args):
    destination = ROOT / 'test-results/speech-comparison' / datetime.now(UTC).strftime('%Y%m%dT%H%M%S%fZ')
    destination.mkdir(parents=True, exist_ok=False)
    source = ROOT / 'data/benchmarks/clips.json'
    clips = json.loads(source.read_text(encoding='utf8'))['clips'][:args.limit]
    config = Settings(asr_backend=args.backend, asr_device='cpu', speaker_diarization=args.backend == 'sensevoice')
    manifest = {'status': 'running', 'protocol': 'docs/32 C1', 'platform': platform.platform(),
        'asr': 'SenseVoice int8 CPU, VAD max15sec' if args.backend == 'sensevoice' else 'Whisper small CPU int8, configured frozen weights',
        'diarization': 'pyannote 3.0, eres2net, threshold .5, auto count' if config.speaker_diarization else 'not run',
        'clips_sha256': hashlib.sha256(source.read_bytes()).hexdigest(), 'count': len(clips),
        'sources': {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in
                    [Path(__file__), ROOT / 'backend/meetflow/speech.py', ROOT / 'docs/32-completion-plan.md']},
        'models': json.loads((config.speech_model_dir / 'assets.json').read_text(encoding='utf8')),
        'limitations': '24 correlated clips from 8 public non-sales meetings; diagnostic not official CER/DER; developer machine background load'}
    (destination / 'manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf8')
    (destination / 'source_bundle.json').write_text(json.dumps({str(p.relative_to(ROOT)): p.read_text(encoding='utf8')
        for p in [Path(__file__), ROOT / 'backend/meetflow/speech.py', ROOT / 'backend/meetflow/audio.py',
                  ROOT / 'docs/32-completion-plan.md']}, ensure_ascii=False), encoding='utf8')
    results = []
    for clip in clips:
        start = time.perf_counter()
        try:
            if args.backend == 'sensevoice':
                spans, turns = transcribe(ROOT / 'data/benchmarks' / clip['path'], config, with_turns=True)
            else:
                from meetflow.audio import transcribe_spans
                spans, turns = transcribe_spans(ROOT / 'data/benchmarks' / clip['path'], config), []
            ref, hyp = normalized(clip['reference']), normalized(''.join(s['text'] for s in spans))
            result = {'id': clip['id'], 'status': 'completed', 'seconds': clip['seconds'],
                      'wall_seconds': time.perf_counter() - start, 'edits': edits(ref, hyp), 'reference_chars': len(ref),
                      'cer': edits(ref, hyp) / len(ref), 'spans': spans, 'turns': turns,
                      **(speaker_error(clip, turns) if config.speaker_diarization else {'speaker_evaluation': 'not run'})}
        except Exception as exc:  # noqa: BLE001 -- retain actual dependency/model failures
            result = {'id': clip['id'], 'status': 'failed', 'error': f'{type(exc).__name__}: {exc}',
                      'wall_seconds': time.perf_counter() - start}
        results.append(result)
        (destination / 'results.json').write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding='utf8')
        print(json.dumps({'folder': str(destination), **{k: v for k, v in result.items() if k not in {'spans', 'turns'}}}), flush=True)
        if result['status'] == 'failed':
            break
    manifest['status'] = 'completed' if len(results) == len(clips) and all(r['status'] == 'completed' for r in results) else 'failed'
    (destination / 'manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf8')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--limit', type=int, default=24)
    parser.add_argument('--backend', choices=['sensevoice', 'whisper'], default='sensevoice')
    main(parser.parse_args())
