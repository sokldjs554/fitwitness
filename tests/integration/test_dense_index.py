from uuid import uuid4
from hashlib import sha256
import numpy as np
import pytest
from fitwitness.contracts import TenantScope, DrawingRevision, SearchRequest
from fitwitness.retrieval.indexing import index_revision
from fitwitness.retrieval.pipeline import search
from test_storage import repo

class TinyEncoder:
    # Test boundary only; all reported evaluation uses pretrained models.
    fingerprint='integration-test-only'
    def encode_text(self, texts, query=False): return np.array([[1.,0.,0.] for _ in texts])
    def encode_image(self, images): return np.array([[0.,1.,0.] for _ in images])

def setup(repo):
    s=TenantScope(tenant_id=str(uuid4()),user_id='test')
    r=DrawingRevision(tenant_id=s.tenant_id,document_id=str(uuid4()),drawing_number='DENSE-1',family_id='f',revision_label='A',source_hash=sha256(b'pdf').hexdigest())
    repo.add_revision(s,r,b'pdf');repo.put_asset(s,r.id,'png',b'png')
    return s,r

def test_atomic_index_is_scoped_and_facts_invalidate_it(repo):
    a,ra=setup(repo);b,rb=setup(repo);e=TinyEncoder()
    index_revision(a,ra.id,repo,e);index_revision(b,rb.id,repo,e)
    snap=repo.snapshot(a)
    found=search(a,SearchRequest(text='shape'),snap,repo,e,{'semantic'})
    assert [c.revision_id for c in found]==[ra.id]
    assert repo.vector_metadata(a,[rb.id])=={}
    assert set(repo.vector_metadata(a,[ra.id])[ra.id])=={'text','image'}
    repo.save_facts(a,ra.id,[])
    assert repo.vector_metadata(a,[ra.id])=={}
    with pytest.raises(ValueError,match='snapshot'):search(a,SearchRequest(text='shape'),snap,repo,e,{'semantic'})
    with pytest.raises(ValueError,match='index'):search(a,SearchRequest(text='shape'),repo.snapshot(a),repo,e,{'semantic'})

def test_index_model_mismatch_and_source_race_rejected(repo):
    s,r=setup(repo);e=TinyEncoder();meta=index_revision(s,r.id,repo,e)
    e.fingerprint='other-model'
    with pytest.raises(ValueError,match='index'):search(s,SearchRequest(text='shape'),repo.snapshot(s),repo,e,{'semantic'})
    before=repo.snapshot(s).id
    with pytest.raises(ValueError,match='source changed'):
        repo.save_index(s,r.id,{'text':[1.,0.],'image':[0.,1.]},{**meta,'text_hash':'wrong'})
    assert repo.snapshot(s).id==before


def test_dense_graph_uses_indexed_scores(repo):
    from fitwitness.runtime.jobs import Jobs
    from fitwitness.contracts import RunRequest
    from fitwitness.agents.graph import execute_run
    s,r=setup(repo);e=TinyEncoder();index_revision(s,r.id,repo,e)
    jobs=Jobs(repo)
    run=jobs.enqueue(s,RunRequest(search=SearchRequest(text='shape'),provider='rules',model_id='rules'),str(uuid4()))
    execute_run(repo,s,run.id,encoders=e)
    result=jobs.get(s,run.id)
    assert result.state=='completed'
    retrieved=next(x for x in jobs.events(s,run.id) if x['kind']=='retrieved')
    assert retrieved['payload']['candidates'][0]['scores']['semantic']==pytest.approx(1.)


def test_vision_tool_preserves_source_scope_and_uncertainty(repo):
    from io import BytesIO
    from PIL import Image
    from fitwitness.agents.tools import EvidenceTools,ToolRequest
    from fitwitness.agents.budget import BudgetTracker
    from fitwitness.contracts import Budget
    from fitwitness.ingest.vision import ImageReading
    a,ra=setup(repo);b,rb=setup(repo)
    buf=BytesIO();Image.new('RGB',(100,100),'white').save(buf,format='PNG')
    repo.put_asset(a,ra.id,'png',buf.getvalue())
    class Reader:
        emit=lambda *args:None
        def read_image(self,image,prompt):
            return ImageReading(annotations=[dict(field='width',value='40',unit='mm',visible_text='40',bbox=(0,0,1,1))]),{'role':'vision'}
    t=EvidenceTools(a,repo.snapshot(a),repo,BudgetTracker(Budget()),vision=Reader())
    op=ToolRequest(name='read_image_region',arguments={'revision_id':ra.id,'bbox':[0,0,1,1],'fields':['width']})
    facts=t.execute(op)
    assert facts[0]['certainty']=='uncertain' and facts[0]['source']['source_hash']==sha256(buf.getvalue()).hexdigest()
    with pytest.raises(ValueError,match='snapshot'):
        t.execute(op.model_copy(update={'arguments':{**op.arguments,'revision_id':rb.id}}))
