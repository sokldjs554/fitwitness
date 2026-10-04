import importlib.util,os,hashlib
from uuid import uuid4
import pytest
from fitwitness.contracts import DrawingRevision,TenantScope

def api():
    assert importlib.util.find_spec('fitwitness.storage.repository'), 'PostgreSQL repository missing'
    from fitwitness.storage.repository import Repository
    return Repository

@pytest.fixture
def repo():
    R=api();r=R(os.getenv('FITWITNESS_DATABASE_URL','postgresql://fwadmin@/postgres?host=/tmp&port=55439'))
    r.migrate();return r

@pytest.fixture
def scopes():
    return [TenantScope(tenant_id=str(uuid4()),user_id=str(uuid4())) for _ in range(2)]

def doc(scope,**extra):
    return DrawingRevision(tenant_id=scope.tenant_id,document_id=str(uuid4()),drawing_number='BS-120',family_id='f1',revision_label='2',source_hash=hashlib.sha256(b'pdf').hexdigest(),**extra)

def test_foreign_tenant_cannot_read_revision_or_asset(repo,scopes):
    a,b=scopes;r=doc(a);repo.add_revision(a,r,b'pdf')
    assert repo.get_revision(b,r.id) is None
    assert repo.asset(b,r.id,'pdf') is None
    assert repo.snapshot(b).revision_ids==[]
    assert repo.asset(a,r.id,'pdf')==b'pdf'

def test_no_scope_leakage_across_transactions(repo,scopes):
    a,b=scopes;r=doc(a);repo.add_revision(a,r,b'pdf')
    for scope,want in [(a,1),(b,0),(a,1),(b,0)]: assert len(repo.list_revisions(scope))==want

def test_conflicting_approved_revisions_are_unresolved(repo,scopes):
    a=scopes[0];old=doc(a);repo.add_revision(a,old,b'pdf')
    for label in ['10','11']:
        v=old.model_copy(update={'id':str(uuid4()),'revision_label':label,'supersedes':old.id});repo.add_revision(a,v,b'pdf')
    assert len(repo.snapshot(a).revision_ids)==0
    assert repo.revision_conflicts(a)==[old.document_id]

def test_invalid_supersedes_rejected(repo,scopes):
    a=scopes[0]
    with pytest.raises(ValueError): repo.add_revision(a,doc(a,supersedes=str(uuid4())),b'pdf')

def test_content_hash_checked(repo,scopes):
    with pytest.raises(ValueError):repo.add_revision(scopes[0],doc(scopes[0]),b'wrong')

def test_immutable_revision(repo,scopes):
    a=scopes[0];r=doc(a);repo.add_revision(a,r,b'pdf');repo.add_revision(a,r,b'pdf')
    assert len(repo.list_revisions(a))==1
    with pytest.raises(ValueError):repo.add_revision(a,r.model_copy(update={'revision_label':'other'}),b'pdf')

def test_snapshot_changes_on_new_revision(repo,scopes):
    a=scopes[0];r=doc(a);before=repo.snapshot(a);repo.add_revision(a,r,b'pdf')
    assert before.id!=repo.snapshot(a).id

def test_actual_postgres_and_rls_role(repo,scopes):
    with repo.connection(scopes[0]) as c:
        row=c.execute('select current_user, rolsuper, rolbypassrls from pg_roles where rolname=current_user').fetchone()
        assert row['current_user']=='fitwitness_app'
        assert row['rolsuper'] is False and row['rolbypassrls'] is False

def test_vector_search_is_snapshot_and_tenant_scoped(repo,scopes):
    a,b=scopes;ra=doc(a);rb=doc(b);repo.add_revision(a,ra,b'pdf');repo.add_revision(b,rb,b'pdf')
    assert hasattr(repo,'save_vector'),'pgvector indexing missing'
    repo.save_vector(a,ra.id,'text',[1.,0.,0.]);repo.save_vector(b,rb.id,'text',[1.,0.,0.])
    results=repo.vector_search(a,'text',[1.,0.,0.],[ra.id,rb.id],5)
    assert [r['revision_id'] for r in results]==[ra.id]
    assert results[0]['distance']==pytest.approx(0.)
