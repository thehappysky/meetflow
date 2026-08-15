"""Offline model preparation from the official HTTPS registry with per-blob SHA checks.

Does not disable Ollama redirect/network protections or change system DNS/proxy.
"""
import argparse
import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx
from prepare_audio_benchmark import download

ROOT = Path(__file__).resolve().parents[1] / 'data/models/ollama-modern'
BASE = 'https://registry.ollama.ai/v2/library/qwen3.5'
MODELS = {'4b-q4_K_M': 'a9d0a3e1d8c91732a3812becd31f8d4a8ff57414903f226306069f0437e2ac50',
          '9b-q4_K_M': '02d45dc1cf451ba2475ac33b301c2dd8f985abe4c182ce04a1f2f5bf0260278d'}


def main(tag):
    response = httpx.get(f'{BASE}/manifests/{tag}', follow_redirects=True, timeout=30).raise_for_status()
    manifest = response.json()
    if not any(layer['digest'] == f'sha256:{MODELS[tag]}' and layer['mediaType'] == 'application/vnd.ollama.image.model'
               for layer in manifest['layers']):
        raise ValueError('Official model revision differs from pinned digest')

    def asset(item):
        algorithm, digest = item['digest'].split(':')
        if algorithm != 'sha256' or len(digest) != 64 or any(c not in '0123456789abcdef' for c in digest):
            raise ValueError('Invalid official model digest')
        target = ROOT / 'blobs' / f'sha256-{digest}'
        download(f'{BASE}/blobs/sha256:{digest}', target)
        with target.open('rb') as stream:
            actual = hashlib.file_digest(stream, 'sha256').hexdigest()
        if actual != digest or target.stat().st_size != item['size']:
            raise ValueError('Model blob checksum or size mismatch')
        return {'digest': item['digest'], 'bytes': item['size'], 'verified': True}

    with ThreadPoolExecutor(max_workers=2) as pool:
        ledger = list(pool.map(asset, [manifest['config'], *manifest['layers']]))
    target = ROOT / 'manifests/registry.ollama.ai/library/qwen3.5' / tag
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(response.content)
    (ROOT / f'source-{tag}.json').write_text(json.dumps({'source': f'{BASE}/manifests/{tag}',
        'manifest_sha256': hashlib.sha256(response.content).hexdigest(), 'blobs': ledger}, indent=2), encoding='utf8')
    print(json.dumps({'model': f'qwen3.5:{tag}', 'verified_blobs': len(ledger), 'installed_store_changed': False}), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--tag', choices=list(MODELS), default='4b-q4_K_M')
    main(parser.parse_args().tag)
