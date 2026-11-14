"""One-time import of the owner's trusted legacy cache into safe native storage.

PYTHONPATH=core/src:server/src python scripts/migrate_results.py
Never use this importer with uploaded or third-party pickle files.
"""
import hashlib
from skyshift_server.local_results import jobs
from skyshift_server.jobs import root,save

if __name__=='__main__':
    for job in jobs():
        key=hashlib.sha256(('legacy:'+job['_source']).encode()).hexdigest()[:24]
        if not (root()/key/'job.json').exists():save(key,job)
        print(job['_source'],'->',key,flush=True)
