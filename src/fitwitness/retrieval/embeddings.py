"""Pinned, hash-verified pretrained encoders; optional for operator workers."""
from functools import lru_cache
from hashlib import sha256, file_digest
from io import BytesIO
from pathlib import Path
import json
import os
from PIL import Image


def model_lock():
    return json.loads(Path(__file__).with_name('models.json').read_text())


def file_hash(path):
    with path.open('rb') as stream:
        return file_digest(stream,'sha256').hexdigest()


def verify_models(root, lock):
    for model in lock.values():
        for name, expected in model['files'].items():
            p = root / model['folder'] / name
            if not p.is_file() or file_hash(p) != expected:
                raise ValueError(f'Pinned model missing or hash mismatch: {name}; run scripts/download_encoders.py')


@lru_cache(maxsize=1)
def pretrained_encoders():
    return Encoders()


def configured_encoders():
    mode = os.getenv('FITWITNESS_ENCODERS', 'off')
    if mode == 'off':
        return None
    if mode != 'pretrained':
        raise ValueError('FITWITNESS_ENCODERS must be off or pretrained')
    return pretrained_encoders()


class Encoders:
    def __init__(self):
        lock = model_lock()
        root = Path(os.getenv('FITWITNESS_MODEL_CACHE', 'var/models'))
        verify_models(root, lock)
        import torch
        from sentence_transformers import SentenceTransformer
        import open_clip
        torch.set_num_threads(int(os.getenv('FITWITNESS_TORCH_THREADS', '2')))
        self.fingerprint = sha256(json.dumps(lock, sort_keys=True).encode()).hexdigest()
        self.manifest = lock
        self.text = SentenceTransformer(str(root / lock['text']['folder']), device='cpu', local_files_only=True)
        self.image, _, self.preprocess = open_clip.create_model_and_transforms(
            'ViT-B-32', pretrained=str(root / lock['image']['folder'] / 'open_clip_model.safetensors'), device='cpu')
        self.image.eval()

    def encode_text(self, texts, query=False):
        prefix = 'query: ' if query else 'passage: '
        return self.text.encode([prefix + t for t in texts], normalize_embeddings=True, show_progress_bar=False)

    def encode_image(self, images):
        import torch
        with torch.inference_mode():
            batch = torch.stack([self.preprocess(Image.open(BytesIO(im)).convert('RGB')) for im in images])
            features = self.image.encode_image(batch)
            return (features / features.norm(dim=-1, keepdim=True)).cpu().numpy()
