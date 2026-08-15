"""Download tokenizer data from a frozen official Qwen revision; never load model code."""
import argparse
import hashlib
import json
from pathlib import Path

from prepare_audio_benchmark import download

MODELS = {'qwen3-14b': 'Qwen/Qwen3-14B', 'qwen35-4b': 'Qwen/Qwen3.5-4B', 'qwen35-9b': 'Qwen/Qwen3.5-9B'}
REVISIONS = {'qwen3-14b': '40c069824f4251a91eefaf281ebe4c544efd3e18',
             'qwen35-4b': '851bf6e806efd8d0a36b00ddf55e13ccb7b8cd0a',
             'qwen35-9b': 'c202236235762e1c871ad0ccb60c8ee5ba337b9a'}
SHA256 = {'qwen3-14b': 'aeb13307a71acd8fe81861d94ad54ab689df773318809eed3cbe794b4492dae4',
          'qwen35-4b': '5f9e4d4901a92b997e463c1f46055088b6cca5ca61a6522d1b9f64c4bb81cb42',
          'qwen35-9b': '5f9e4d4901a92b997e463c1f46055088b6cca5ca61a6522d1b9f64c4bb81cb42'}
ROOT = Path(__file__).resolve().parents[1] / 'data/models/tokenizers'


def main(name):
    repository = MODELS[name]
    revision = REVISIONS[name]
    folder = ROOT / name / revision
    url = f'https://huggingface.co/{repository}/resolve/{revision}/tokenizer.json'
    path = folder / 'tokenizer.json'
    if not path.is_file():
        download(url, path)
    from tokenizers import Tokenizer
    tokenizer = Tokenizer.from_file(str(path))
    with path.open('rb') as stream:
        sha = hashlib.file_digest(stream, 'sha256').hexdigest()
    if sha != SHA256[name]:
        raise ValueError('Tokenizer data differs from the pinned SHA256; receipt not written')
    receipt = {'repository': repository, 'revision': revision, 'source': url, 'sha256': sha,
               'vocabulary_size': tokenizer.get_vocab_size(), 'path': str(path.resolve())}
    (folder / 'source.json').write_text(json.dumps(receipt, indent=2), encoding='utf8')
    print(json.dumps(receipt), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('model', choices=list(MODELS))
    main(parser.parse_args().model)
