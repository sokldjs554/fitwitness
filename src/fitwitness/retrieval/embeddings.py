"""Optional real pretrained encoders. Never substitute random/hash vectors."""

from io import BytesIO
from PIL import Image


class Encoders:
    def __init__(self, text_revision=None):
        from sentence_transformers import SentenceTransformer
        import open_clip

        self.text = SentenceTransformer(
            "intfloat/multilingual-e5-small", revision=text_revision, device="cpu"
        )
        self.image, _, self.preprocess = open_clip.create_model_and_transforms(
            "ViT-B-32", pretrained="laion2b_s34b_b79k", device="cpu"
        )
        self.image.eval()

    def encode_text(self, texts, query=False):
        prefix = "query: " if query else "passage: "
        return self.text.encode([prefix + t for t in texts], normalize_embeddings=True)

    def encode_image(self, images):
        import torch

        with torch.inference_mode():
            batch = torch.stack(
                [
                    self.preprocess(Image.open(BytesIO(im)).convert("RGB"))
                    for im in images
                ]
            )
            features = self.image.encode_image(batch)
            return (features / features.norm(dim=-1, keepdim=True)).cpu().numpy()
