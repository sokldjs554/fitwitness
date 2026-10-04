from hashlib import sha256
from uuid import uuid4
import numpy as np
import pytest
from fitwitness.contracts import DrawingRevision, TenantScope, SearchRequest, SearchSnapshot
from fitwitness.retrieval.pipeline import search

class Repo:
    def __init__(self):
        self.rev = DrawingRevision(tenant_id='t',document_id=str(uuid4()),drawing_number='D-1',family_id='f',revision_label='A',source_hash=sha256(b'pdf').hexdigest(),title='plate')
        self.saved = []
    def get_revision(self,scope,rid): return self.rev if rid==self.rev.id else None
    def load_facts(self,*args): return []
    def load_facts_many(self,scope,ids): return {rid: [] for rid in ids}
    def asset(self,*args): return b'image'
    def list_revisions(self,*args): return [self.rev]
    def save_index(self,*args): self.saved.append(args)
    def vector_metadata(self,*args): return {}
    def vector_search(self,*args): return []
    def snapshot(self,*args): return SearchSnapshot(id='now',index_hash='now',revision_ids=[self.rev.id])

class Encoder:
    fingerprint='pinned-test-encoder'
    def encode_text(self,texts,query=False): return np.array([[1.,0.] for _ in texts])
    def encode_image(self,images): return np.array([[0.,1.] for _ in images])

scope=TenantScope(tenant_id='t',user_id='u')

def test_index_uses_source_and_publishes_both_channels_atomically():
    from fitwitness.retrieval.indexing import index_revision
    repo=Repo()
    index_revision(scope,repo.rev.id,repo,Encoder())
    assert len(repo.saved)==1
    _,rid,vectors,metadata=repo.saved[0]
    assert rid==repo.rev.id and set(vectors)=={'text','image'}
    assert metadata['encoder_fingerprint']=='pinned-test-encoder'
    assert metadata['source_hash']==repo.rev.source_hash
    assert metadata['image_hash']==sha256(b'image').hexdigest()


def test_failed_image_encoding_publishes_no_partial_text_index():
    from fitwitness.retrieval.indexing import index_revision
    class Broken(Encoder):
        def encode_image(self,images):raise RuntimeError('encoder unavailable')
    repo=Repo()
    with pytest.raises(RuntimeError):index_revision(scope,repo.rev.id,repo,Broken())
    assert repo.saved==[]


def test_dense_search_rejects_unindexed_corpus():
    repo=Repo()
    with pytest.raises(ValueError,match='index'):
        search(scope,SearchRequest(text='plate'),repo.snapshot(scope),repo,Encoder(),{'semantic'})


def test_search_rejects_old_snapshot_before_reading_vectors():
    repo=Repo()
    old=SearchSnapshot(id='old',index_hash='old',revision_ids=[repo.rev.id])
    with pytest.raises(ValueError,match='snapshot'):
        search(scope,SearchRequest(text='plate'),old,repo,Encoder(),{'semantic'})


def test_empty_channels_return_nothing():
    repo=Repo()
    assert search(scope,SearchRequest(text='plate'),repo.snapshot(scope),repo,channels=set())==[]


def test_encoder_config_is_opt_in_and_rejects_typos(monkeypatch):
    from fitwitness.retrieval.embeddings import configured_encoders
    monkeypatch.delenv('FITWITNESS_ENCODERS',raising=False)
    assert configured_encoders() is None
    monkeypatch.setenv('FITWITNESS_ENCODERS','pretraned')
    with pytest.raises(ValueError): configured_encoders()


def test_model_lock_requires_pinned_hashes():
    from fitwitness.retrieval.embeddings import model_lock
    lock=model_lock()
    assert len(lock['text']['revision'])==40 and len(lock['image']['revision'])==40
    for model in lock.values():
        assert all(len(x)==64 for x in model['files'].values())
        assert any(x.endswith('.safetensors') for x in model['files'])
