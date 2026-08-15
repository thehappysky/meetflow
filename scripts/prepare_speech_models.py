"""Download public official sherpa-onnx model assets; never execute archive code."""
import hashlib
import json
import tarfile
from pathlib import Path

from prepare_audio_benchmark import download

ROOT = Path(__file__).resolve().parents[1] / 'data/models/speech'
ASSETS = [
    ('sensevoice.tar.bz2', 'https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2024-07-17.tar.bz2'),
    ('segmentation.tar.bz2', 'https://github.com/k2-fsa/sherpa-onnx/releases/download/speaker-segmentation-models/sherpa-onnx-pyannote-segmentation-3-0.tar.bz2'),
    ('speaker.onnx', 'https://github.com/k2-fsa/sherpa-onnx/releases/download/speaker-recongition-models/3dspeaker_speech_eres2net_base_sv_zh-cn_3dspeaker_16k.onnx'),
    ('silero_vad.onnx', 'https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/silero_vad.onnx'),
]


def main():
    ROOT.mkdir(parents=True, exist_ok=True)
    ledger = []
    for name, url in ASSETS:
        target = ROOT / name
        download(url, target)
        with target.open('rb') as asset:
            digest = hashlib.file_digest(asset, 'sha256').hexdigest()
        ledger.append({'file': name, 'source': url, 'sha256': digest, 'bytes': target.stat().st_size})
        if name.endswith('tar.bz2'):
            with tarfile.open(target, 'r:bz2') as archive:
                for member in archive:
                    relative = Path(member.name)
                    destination = (ROOT / relative).resolve()
                    if relative.is_absolute() or not destination.is_relative_to(ROOT.resolve()):
                        raise ValueError('Archive path outside model directory')
                    if member.isfile() and (relative.name in {'model.int8.onnx', 'model.onnx', 'tokens.txt', 'LICENSE', 'README.md'}):
                        destination.parent.mkdir(parents=True, exist_ok=True)
                        with archive.extractfile(member) as source, destination.open('wb') as output:
                            while data := source.read(1024 * 1024):
                                output.write(data)
    (ROOT / 'assets.json').write_text(json.dumps(ledger, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'directory': str(ROOT), 'assets': len(ledger)}), flush=True)


if __name__ == '__main__':
    main()
