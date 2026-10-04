"""Download only the hash-locked files needed by the research worker."""
from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
from pathlib import Path
import os
import urllib.request
from fitwitness.retrieval.embeddings import model_lock, verify_models

root = Path(os.getenv('FITWITNESS_MODEL_CACHE', 'var/models'))
lock = model_lock()

def fetch(item):
    model, name, expected = item
    path = root / model['folder'] / name
    if path.is_file() and sha256(path.read_bytes()).hexdigest() == expected:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + '.part')
    url = f"https://huggingface.co/{model['model_id']}/resolve/{model['revision']}/{name}"
    urllib.request.urlretrieve(url, temporary)
    if sha256(temporary.read_bytes()).hexdigest() != expected:
        temporary.unlink()
        raise ValueError(f'download hash mismatch: {name}')
    temporary.replace(path)

if __name__ == '__main__':
    with ThreadPoolExecutor(max_workers=3) as pool:
        list(pool.map(fetch, [(m,n,h) for m in lock.values() for n,h in m['files'].items()]))
    verify_models(root, lock)
    print('Pinned E5/OpenCLIP files verified.')
