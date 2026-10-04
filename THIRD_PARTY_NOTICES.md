
## Pinned retrieval research models

FitWitness source remains Apache-2.0. The upstream model licenses are separate:

- intfloat/multilingual-e5-small, revision `614241f622f53c4eeff9890bdc4f31cfecc418b3`, upstream MIT. Model card: https://huggingface.co/intfloat/multilingual-e5-small . Query/passages use the documented `query: ` / `passage: ` prefixes and normalized embeddings.
- laion/CLIP-ViT-B-32-laion2B-s34B-b79K, revision `1a25a446712ba5ee05982a381eed697ef9b435cf`, upstream MIT. Model card: https://huggingface.co/laion/CLIP-ViT-B-32-laion2B-s34B-b79K . This is a research model; the pilot tests constrained synthetic drawings and does not establish deployment/general-domain suitability. Only its image encoder is used; Korean text uses multilingual E5.

Weights are not redistributed in this repository or included in the free public web image. `src/fitwitness/retrieval/models.json` pins all required file SHA-256 values; the safetensors and LFS files were matched against Hugging Face's revision-specific LFS hashes before use.

## BGE cross-encoder (optional research runtime)

BAAI/bge-reranker-v2-m3, revision `953dc6f6f85a1b2dbfca4c34a2796e7dde08d41e`, Apache-2.0. Official model card: https://huggingface.co/BAAI/bge-reranker-v2-m3 . Revision and SHA-256 locks are in `src/fitwitness/retrieval/reranker.json`. Weights are downloaded separately and are not included in the repository or free web deployment.
