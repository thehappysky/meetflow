"""Download a pinned CTranslate2 ASR candidate without replacing current settings."""
import hashlib
import json
import os
from pathlib import Path

os.environ.setdefault('HF_HUB_DISABLE_XET','1')
from huggingface_hub import HfApi, hf_hub_download

ROOT=Path(__file__).resolve().parents[1]
REPO='dropbox-dash/faster-whisper-large-v3-turbo'
REVISION='0a363e9161cbc7ed1431c9597a8ceaf0c4f78fcf'
FILES=['config.json','preprocessor_config.json','tokenizer.json','vocabulary.json','README.md','model.bin']


def main():
    target=ROOT/'data/benchmarks/models/faster-whisper-large-v3-turbo'
    target.mkdir(parents=True,exist_ok=True)
    info=HfApi().model_info(REPO,revision=REVISION,files_metadata=True)
    expected={f.rfilename:f for f in info.siblings}
    receipt=[]
    for name in FILES:
        path=Path(hf_hub_download(REPO,name,revision=REVISION,local_dir=target))
        size=path.stat().st_size
        with path.open('rb') as stream:
            sha=hashlib.file_digest(stream,'sha256').hexdigest()
        reference=expected[name]
        if reference.lfs:
            assert sha==reference.lfs.sha256, 'LFS file content differs from pinned publisher hash'
        else:
            blob=hashlib.sha1(b'blob '+str(size).encode()+b'\0'+path.read_bytes()).hexdigest()
            assert blob==reference.blob_id, 'Git blob content differs from pinned publisher hash'
        assert size==reference.size
        receipt.append({'file':name,'bytes':size,'sha256':sha,
                        'source':f'https://huggingface.co/{REPO}/resolve/{REVISION}/{name}'})
        print(json.dumps({'file':name,'verified':True,'bytes':size}),flush=True)
    (target/'assets.json').write_text(json.dumps({'repo':REPO,'revision':REVISION,'files':receipt},indent=2),encoding='utf8')


if __name__=='__main__':
    main()
