"""Source-only, atomic per-revision dense indexing. Gold is never an input."""
from hashlib import sha256
import json


def document_text(revision, facts):
    return " ".join([revision.title, revision.drawing_number, revision.kind or ""] +
                    [f"{f.field} {f.value} {f.unit or ''}" for f in sorted(facts, key=lambda f: (f.field, str(f.value), f.unit or ""))])


def source_metadata(revision, text, image, fingerprint):
    return dict(encoder_fingerprint=fingerprint, source_hash=revision.source_hash,
                text_hash=sha256(text.encode()).hexdigest(), image_hash=sha256(image).hexdigest())


def index_revision(scope, revision_id, repo, encoders):
    revision = repo.get_revision(scope, revision_id)
    if revision is None:
        raise ValueError("revision not found")
    text = document_text(revision, repo.load_facts(scope, revision_id))
    image = repo.asset(scope, revision_id, "png")
    if not image:
        raise ValueError("index requires a source preview")
    metadata = source_metadata(revision, text, image, encoders.fingerprint)
    vectors = {"text": encoders.encode_text([text])[0].tolist(),
               "image": encoders.encode_image([image])[0].tolist()}
    # No write happens until every encoder succeeded. Repository checks source
    # consistency again under the tenant lock, before atomically replacing both.
    repo.save_index(scope, revision_id, vectors, metadata)
    return metadata


def require_index(scope, snapshot, repo, encoders):
    metadata = repo.vector_metadata(scope, snapshot.revision_ids)
    for rid in snapshot.revision_ids:
        records = metadata.get(rid, {})
        if set(records) != {"text", "image"} or any(
            m.get("encoder_fingerprint") != encoders.fingerprint for m in records.values()
        ):
            raise ValueError("dense index missing or encoder mismatch; rebuild index")


def main():
    import argparse, os
    from fitwitness.contracts import TenantScope
    from fitwitness.storage.repository import Repository
    from fitwitness.retrieval.embeddings import Encoders
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--tenant', required=True)
    args = p.parse_args()
    scope = TenantScope(tenant_id=args.tenant, user_id='indexer', role='operator')
    repo = Repository(os.environ['FITWITNESS_DATABASE_URL'])
    encoders = Encoders()
    for rid in repo.snapshot(scope).revision_ids:
        index_revision(scope, rid, repo, encoders)
    print(json.dumps(dict(indexed=len(repo.snapshot(scope).revision_ids),
                          encoder_fingerprint=encoders.fingerprint)))


if __name__ == '__main__':
    main()
