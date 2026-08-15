"""Download the official AliMeeting Eval archive and a pinned local ASR model.

All payloads stay under ignored data/benchmarks. Supports interrupted downloads.
"""
import hashlib
import json
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1] / "data" / "benchmarks"
ARCHIVE = "https://speech-lab-share-data.oss-cn-shanghai.aliyuncs.com/AliMeeting/openlr/Eval_Ali.tar.gz"
MODEL = "Systran/faster-whisper-small"


def download(url, target):
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.is_file():
        return
    partial = target.with_name(target.name + ".part")
    for attempt in range(4):
        start = partial.stat().st_size if partial.exists() else 0
        try:
            with httpx.Client(follow_redirects=True, timeout=60) as client:
                headers = {"Range": f"bytes={start}-"} if start else {}
                with client.stream("GET", url, headers=headers) as response:
                    response.raise_for_status()
                    if start and response.status_code == 206:
                        if not response.headers.get("content-range", "").startswith(f"bytes {start}-"):
                            raise ValueError("Server returned a mismatching byte range")
                    else:
                        start = 0
                    total = start + int(response.headers.get("content-length", "0"))
                    written, logged = start, start
                    with partial.open("ab" if start else "wb") as stream:
                        for chunk in response.iter_bytes(1024 * 1024):
                            stream.write(chunk)
                            written += len(chunk)
                            if written - logged >= 64 * 1024 * 1024:
                                print(f"{target.name}: {written:,}/{total:,} bytes", flush=True)
                                logged = written
                    if total and written != total:
                        raise ValueError("Incomplete download")
            partial.replace(target)
            print(f"Downloaded {target.name}: {written:,} bytes", flush=True)
            return
        except (httpx.HTTPError, ValueError) as exc:
            print(f"Retry {target.name}: {type(exc).__name__}", flush=True)
            if attempt == 3:
                raise
            time.sleep(2)


def checksum(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(4 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def main():
    ROOT.mkdir(parents=True, exist_ok=True)
    with httpx.Client(follow_redirects=True, timeout=60) as client:
        metadata = client.get(f"https://huggingface.co/api/models/{MODEL}")
        metadata.raise_for_status()
    revision = metadata.json()["sha"]
    jobs = [(ARCHIVE, ROOT / "alimeeting" / "Eval_Ali.tar.gz")]
    for name in ["config.json", "model.bin", "tokenizer.json", "vocabulary.txt", "README.md"]:
        jobs.append((f"https://huggingface.co/{MODEL}/resolve/{revision}/{name}",
                     ROOT / "models" / "faster-whisper-small" / name))
    with ThreadPoolExecutor(max_workers=3) as executor:
        list(executor.map(lambda job: download(*job), jobs))
    manifest = {
        "dataset": "AliMeeting official Eval", "source": "https://www.openslr.org/119/",
        "license": "CC BY-SA 4.0", "model": MODEL, "model_revision": revision,
        "files": [{"path": str(path.relative_to(ROOT)), "source": url,
                   "bytes": path.stat().st_size, "sha256": checksum(path)} for url, path in jobs],
    }
    (ROOT / "downloads.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    (ROOT / "README.txt").write_text(
        "AliMeeting Eval: https://www.openslr.org/119/\n"
        "License: Creative Commons Attribution-ShareAlike 4.0\n"
        "https://creativecommons.org/licenses/by-sa/4.0/\n"
        "Cite Yu et al., M2MeT, ICASSP 2022.\n"
        "Public research meetings, NOT customer sales calls. Do not invent sales identities.\n"
        "Audio, annotations, model weights and generated transcripts are local-only.\n",
        encoding="utf-8")
    print("Download manifest and checksums saved.", flush=True)


if __name__ == "__main__":
    main()
