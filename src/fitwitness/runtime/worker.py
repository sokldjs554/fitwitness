"""Run one leased job in a separately terminable process."""
import argparse,os
from fitwitness.contracts import TenantScope
from fitwitness.storage.repository import Repository
from fitwitness.agents.graph import execute_run

def main():
    p=argparse.ArgumentParser();p.add_argument('--tenant',required=True);p.add_argument('--run',required=True);p.add_argument('--fault',action='store_true');args=p.parse_args()
    r=Repository(os.environ['FITWITNESS_DATABASE_URL'])
    execute_run(r,TenantScope(tenant_id=args.tenant,user_id='worker',role='operator'),args.run,fault_after_retrieval=args.fault)
if __name__=='__main__':main()
