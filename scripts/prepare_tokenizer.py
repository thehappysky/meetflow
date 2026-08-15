"""Download tokenizer data from a frozen official Qwen revision; never load model code."""
import argparse
import hashlib
import json
from pathlib import Path

import httpx
from prepare_audio_benchmark import download

MODELS = {'qwen3-14b': 'Qwen/Qwen3-14B', 'qwen35-4b': 'Qwen/Qwen3.5-4B', 'qwen35-9b': 'Qwen/Qwen3.5-9B'}
ROOT = Path(__file__).resolve().parents[1] / 'data/models/tokenizers'


def main(name):
    repository = MODELS[name]
    metadata = httpx.get(f'https://huggingface.co/api/models/{repository}', timeout=30).raise_for_status().json()
    revision = metadata['sha']
    folder = ROOT / name / revision
    url = f'https://huggingface.co/{repository}/resolve/{revision}/tokenizer.json'
    path = folder / 'tokenizer.json'
    download(url, path)
    from tokenizers import Tokenizer
    tokenizer = Tokenizer.from_file(str(path))
    with path.open('rb') as stream:
        sha = hashlib.file_digest(stream, 'sha256').hexdigest()
    receipt = {'repository': repository, 'revision': revision, 'source': url, 'sha256': sha,
               'vocabulary_size': tokenizer.get_vocab_size(), 'path': str(path.resolve())}
    (folder / 'source.json').write_text(json.dumps(receipt, indent=2), encoding='utf8')
    print(json.dumps(receipt), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('model', choices=list(MODELS))
    main(parser.parse_args().model)
