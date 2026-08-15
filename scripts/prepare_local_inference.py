"""Prepare a pinned official portable Ollama, preserving the user's installed server."""
import hashlib
import json
import zipfile
from pathlib import Path

import httpx
from prepare_audio_benchmark import download

VERSION = 'v0.40.16'
EXPECTED = '50fcf5ad54277f2fa5d173ebfc93bcb017adbc0c9ef2314daabc96ca9e6aa64e'
ROOT = Path(__file__).resolve().parents[1] / 'data/tools'


def main():
    release = httpx.get(f'https://api.github.com/repos/ollama/ollama/releases/tags/{VERSION}',
                       follow_redirects=True, timeout=30).raise_for_status().json()
    asset = next(a for a in release['assets'] if a['name'] == 'ollama-windows-amd64.zip')
    if asset['digest'] != f'sha256:{EXPECTED}':
        raise ValueError('Official release digest differs from pinned value')
    ROOT.mkdir(parents=True, exist_ok=True)
    archive = ROOT / f'ollama-{VERSION}.zip'
    download(asset['browser_download_url'], archive)
    with archive.open('rb') as stream:
        actual = hashlib.file_digest(stream, 'sha256').hexdigest()
    if actual != EXPECTED:
        raise ValueError('Portable runtime checksum mismatch; not extracted or run')
    destination = ROOT / f'ollama-{VERSION}'
    destination.mkdir(exist_ok=True)
    with zipfile.ZipFile(archive) as package:
        for name in package.namelist():
            target = (destination / name).resolve()
            if not target.is_relative_to(destination.resolve()):
                raise ValueError('Archive path outside destination')
        package.extractall(destination)
    ledger = {'version': VERSION, 'asset': asset['name'], 'sha256': actual,
              'source': asset['browser_download_url'], 'installed_server_replaced': False}
    (destination / 'source.json').write_text(json.dumps(ledger, indent=2), encoding='utf8')
    print(json.dumps(ledger), flush=True)


if __name__ == '__main__':
    main()
