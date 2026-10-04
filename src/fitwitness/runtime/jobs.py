"""Durable job, lease and event storage with atomic snapshot fencing."""
from hashlib import sha256
import json
from uuid import uuid4
import psycopg
from psycopg.types.json import Jsonb
from fitwitness.contracts import RunRequest,RunView,Usage,Decision,uid,now

class Jobs:
    def __init__(self,repo):self.repo=repo
    def migrate(self):
        with psycopg.connect(self.repo.dsn,autocommit=True) as c:
            c.execute('''CREATE TABLE IF NOT EXISTS fw_runs (
                tenant_id text NOT NULL,id text NOT NULL,idempotency_key text NOT NULL,input_hash text NOT NULL,
                request jsonb NOT NULL,snapshot_id text NOT NULL,state text NOT NULL DEFAULT 'queued',
                result jsonb NOT NULL DEFAULT '[]',usage jsonb NOT NULL DEFAULT '{}',error text,
                lease_token text,lease_until timestamptz,fault_consumed bool NOT NULL DEFAULT false,
                created_at timestamptz NOT NULL DEFAULT now(),
                PRIMARY KEY(tenant_id,id),UNIQUE(tenant_id,idempotency_key));
                CREATE TABLE IF NOT EXISTS fw_events (
                tenant_id text NOT NULL,run_id text NOT NULL,seq integer NOT NULL,kind text NOT NULL,payload jsonb NOT NULL,
                timestamp timestamptz NOT NULL DEFAULT now(),PRIMARY KEY(tenant_id,run_id,seq),
                FOREIGN KEY(tenant_id,run_id) REFERENCES fw_runs(tenant_id,id));''')
            for name in ['fw_runs','fw_events']:
                c.execute(f'ALTER TABLE {name} ENABLE ROW LEVEL SECURITY');c.execute(f'ALTER TABLE {name} FORCE ROW LEVEL SECURITY')
                if not c.execute('SELECT 1 FROM pg_policies WHERE tablename=%s AND policyname=%s',(name,'tenant_scope')).fetchone():
                    c.execute(f"CREATE POLICY tenant_scope ON {name} USING (tenant_id=current_setting('app.tenant',true)) WITH CHECK (tenant_id=current_setting('app.tenant',true))")
                c.execute(f'GRANT SELECT,INSERT,UPDATE,DELETE ON {name} TO fitwitness_app')

    def enqueue(self,scope,request:RunRequest,idempotency_key:str):
        if scope.role=='viewer':raise PermissionError('read only')
        body=request.model_dump(mode='json');digest=sha256(json.dumps(body,sort_keys=True).encode()).hexdigest()
        with self.repo.connection(scope) as c:
            self.repo.lock(c,scope.tenant_id)
            row=c.execute('SELECT * FROM fw_runs WHERE idempotency_key=%s',(idempotency_key,)).fetchone()
            if row:
                if row['input_hash']!=digest:raise ValueError('idempotency key reused with a different request')
                run_id=row['id']
            else:
                run_id=uid();snapshot=self.repo.snapshot_in(c,scope)
                c.execute('INSERT INTO fw_runs(tenant_id,id,idempotency_key,input_hash,request,snapshot_id) VALUES(%s,%s,%s,%s,%s,%s)',(scope.tenant_id,run_id,idempotency_key,digest,Jsonb(body),snapshot.id))
                self._event(c,scope,run_id,'queued',{'provider':request.provider,'mode':request.mode})
        return self.get(scope,run_id)

    def raw(self,scope,run_id):
        with self.repo.connection(scope) as c:return c.execute('SELECT * FROM fw_runs WHERE id=%s',(run_id,)).fetchone()
    def get(self,scope,run_id):
        r=self.raw(scope,run_id)
        if not r:return None
        return RunView(id=r['id'],state=r['state'],decisions=[Decision.model_validate(d) for d in r['result']],usage=Usage.model_validate(r['usage']),error=r['error'],input_hash=r['input_hash'],snapshot_id=r['snapshot_id'],provider=r['request']['provider'],model_id=r['request']['model_id'])

    def claim(self,scope,run_id,lease_seconds=30):
        with self.repo.connection(scope) as c:
            r=c.execute('SELECT * FROM fw_runs WHERE id=%s FOR UPDATE',(run_id,)).fetchone()
            if not r or r['state'] not in ['queued','running','retry_wait']:return None
            if r['lease_token'] and c.execute('SELECT %s > now() AS active',(r['lease_until'],)).fetchone()['active']:return None
            token=uid();c.execute("UPDATE fw_runs SET state='running',lease_token=%s,lease_until=now()+(%s * interval '1 second') WHERE id=%s",(token,lease_seconds,run_id))
            self._event(c,scope,run_id,'started',{'recovered':r['state']=='running'});return token

    def heartbeat(self,scope,run_id,token):
        with self.repo.connection(scope) as c:
            return bool(c.execute("UPDATE fw_runs SET lease_until=now()+interval '30 seconds' WHERE id=%s AND lease_token=%s AND state='running' RETURNING id",(run_id,token)).fetchone())

    def _event(self,c,scope,run_id,kind,payload):
        c.execute('SELECT id FROM fw_runs WHERE id=%s FOR UPDATE',(run_id,))
        seq=c.execute('SELECT COALESCE(MAX(seq),0)+1 AS n FROM fw_events WHERE run_id=%s',(run_id,)).fetchone()['n']
        c.execute('INSERT INTO fw_events(tenant_id,run_id,seq,kind,payload) VALUES(%s,%s,%s,%s,%s)',(scope.tenant_id,run_id,seq,kind,Jsonb(payload)))
    def event(self,scope,run_id,kind,payload):
        with self.repo.connection(scope) as c:self._event(c,scope,run_id,kind,payload)
    def events(self,scope,run_id,after=0):
        with self.repo.connection(scope) as c:return c.execute('SELECT run_id,seq,kind,payload,timestamp FROM fw_events WHERE run_id=%s AND seq>%s ORDER BY seq LIMIT 500',(run_id,after)).fetchall()

    def finalize(self,scope,run_id,snapshot_id,decisions,lease_token,usage=None):
        with self.repo.connection(scope) as c:
            self.repo.lock(c,scope.tenant_id)
            r=c.execute('SELECT * FROM fw_runs WHERE id=%s FOR UPDATE',(run_id,)).fetchone()
            if not r or r['state']!='running' or r['lease_token']!=lease_token:return False
            if self.repo.snapshot_in(c,scope).id!=snapshot_id:
                c.execute("UPDATE fw_runs SET state='stale',lease_token=NULL WHERE id=%s",(run_id,));self._event(c,scope,run_id,'stale',{'reason':'도면 또는 추출 근거가 변경되어 재검증이 필요합니다'});return False
            c.execute("UPDATE fw_runs SET state='completed',result=%s,usage=%s,lease_token=NULL WHERE id=%s",(Jsonb([d.model_dump(mode='json') for d in decisions]),Jsonb((usage or Usage()).model_dump(mode='json')),run_id))
            self._event(c,scope,run_id,'completed',{'candidates':len(decisions)});return True

    def cancel(self,scope,run_id):
        if scope.role=='viewer':raise PermissionError('read only')
        with self.repo.connection(scope) as c:
            r=c.execute("UPDATE fw_runs SET state='cancelled',lease_token=NULL WHERE id=%s AND state IN ('queued','running','retry_wait','waiting_input') RETURNING id",(run_id,)).fetchone()
            if r:self._event(c,scope,run_id,'cancelled',{})
        return self.get(scope,run_id)
    def fail(self,scope,run_id,token,error):
        with self.repo.connection(scope) as c:
            r=c.execute("UPDATE fw_runs SET state='failed',error=%s,lease_token=NULL WHERE id=%s AND lease_token=%s AND state='running' RETURNING id",(error[:500],run_id,token)).fetchone()
            if r:self._event(c,scope,run_id,'failed',{'error':error[:500]})
    def release_crashed(self,scope,run_id):
        with self.repo.connection(scope) as c:c.execute("UPDATE fw_runs SET lease_until=now()-interval '1 second' WHERE id=%s AND state='running'",(run_id,))
    def mark_fault_consumed(self,scope,run_id):
        with self.repo.connection(scope) as c:
            return bool(c.execute('UPDATE fw_runs SET fault_consumed=true WHERE id=%s AND NOT fault_consumed RETURNING id',(run_id,)).fetchone())
    def invalidate(self,scope):
        with self.repo.connection(scope) as c:
            self.repo.lock(c,scope.tenant_id);snap=self.repo.snapshot_in(c,scope)
            rs=c.execute("UPDATE fw_runs SET state='stale' WHERE state='completed' AND snapshot_id<>%s RETURNING id",(snap.id,)).fetchall()
            for r in rs:self._event(c,scope,r['id'],'stale',{'reason':'도면 개정 후 결과를 다시 확인해야 합니다'})
        return [r['id'] for r in rs]
