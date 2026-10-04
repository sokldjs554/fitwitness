"""Download only the hash-locked files needed by the research worker."""
from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
from pathlib import Path
import os
import urllib.request
from fitwitness.retrieval.embeddings import model_lock, verify_models, file_hash

root = Path(os.getenv('FITWITNESS_MODEL_CACHE', 'var/models'))
lock = model_lock()

def fetch(item):
    model, name, expected = item
    path = root / model['folder'] / name
    if path.is_file() and file_hash(path) == expected:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + '.part')
    url = f"https://huggingface.co/{model['model_id']}/resolve/{model['revision']}/{name}"
    urllib.request.urlretrieve(url, temporary)
    if file_hash(temporary) != expected:
        temporary.unlink()
        raise ValueError(f'download hash mismatch: {name}')
    temporary.replace(path)

if __name__ == '__main__':
    import argparse
    parser=argparse.ArgumentParser()
    parser.add_argument('--reranker',action='store_true')
    if parser.parse_args().reranker:
        from fitwitness.retrieval.reranking import reranker_lock
        lock.update(reranker_lock())
    with ThreadPoolExecutor(max_workers=3) as pool:
        list(pool.map(fetch, [(m,n,h) for m in lock.values() for n,h in m['files'].items()]))
    verify_models(root, lock)
    print('Pinned model files verified:', ', '.join(lock))
