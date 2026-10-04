"""Index source PDFs only. Does not open the gold directory."""
import os,json
from pathlib import Path
from fitwitness.contracts import TenantScope,DrawingRevision
from fitwitness.storage.repository import Repository
from fitwitness.ingest.pdf import extract_pdf


def seed(repo,scope,root=Path('var/corpus'),include_updates=False):
    manifest=json.loads((root/'manifest.json').read_text())
    for entry in manifest['document_entries']:
        if entry['is_revision_update'] and not include_updates:continue
        data=(root/entry['pdf']).read_bytes()
        fields={k:entry[k] for k in ['id','document_id','drawing_number','family_id','revision_label','supersedes','kind','title','source_hash']}
        rev=DrawingRevision(tenant_id=scope.tenant_id,**fields)
        existing=repo.get_revision(scope,rev.id)
        if existing:continue
        repo.add_revision(scope,rev,data);repo.save_facts(scope,rev.id,extract_pdf(data,rev))
        for k in ['png','step','mesh']:repo.put_asset(scope,rev.id,k,(root/entry[k]).read_bytes())
    return len(repo.list_revisions(scope))

if __name__=='__main__':
    repo=Repository(os.environ['FITWITNESS_DATABASE_URL']);repo.migrate()
    scope=TenantScope(tenant_id='demo-template',user_id='system',role='admin')
    print('indexed source documents:',seed(repo,scope))
