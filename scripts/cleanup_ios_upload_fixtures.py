"""Remove only quiescent FP-16 opt-in simulator fixtures, never user media.

Run after the SDK test process has finished. Input is its credential-free evidence
JSON. Accounts must already be deleted, or still match the invocation's random
fixture email. Mac ADC is used only for exact temporary Auth account cleanup.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import uuid

from firebase_admin import auth, credentials, delete_app, initialize_app
import google.auth

REMOTE = r'''
import json, sys
from google.cloud import firestore
from google.cloud.firestore_v1.base_query import FieldFilter
from infra.dgx.cloud_config import load
from footylevels.assets import SupabaseAssets
from footylevels.records import AssetRef, utc_now
from footylevels.uploads import FirestoreUploads
body=json.load(sys.stdin)
c=load('dev')
db=firestore.Client.from_service_account_json(c['GOOGLE_APPLICATION_CREDENTIALS'],project=c['FIREBASE_PROJECT_ID'])
repo=FirestoreUploads(db)
store=SupabaseAssets(c['SUPABASE_URL'],c['SUPABASE_SECRET_KEY'])
removed={'projects':0,'uploads':0,'assets':0,'profiles':0}
for uid in body['users']:
    projects=list(db.collection('projects').where(filter=FieldFilter('ownerId','==',uid)).stream())
    pids=set()
    for snapshot in projects:
        raw=snapshot.to_dict()
        assert raw['title'].startswith('FP16 ') and body['runID'] in raw['title']
        assert raw['ownerId']==uid
        pids.add(snapshot.id)
    # This probe never starts a model/job. Stop if its scope has changed.
    for collection in ['jobs','reconstructions','assetReservations','comparisons']:
        assert not list(db.collection(collection).where(filter=FieldFilter('ownerId','==',uid)).stream())
    uploads=list(db.collection('uploads').where(filter=FieldFilter('ownerId','==',uid)).stream())
    for snapshot in uploads:
        raw=snapshot.to_dict()
        assert raw['projectId'] in pids and raw['ownerId']==uid
        asset=AssetRef.model_validate(raw['asset'])
        assert asset.object_key==f"users/{uid}/projects/{raw['projectId']}/source/{snapshot.id}.mp4"
        store.delete_exact(asset);store.verify_absent(asset);removed['assets']+=1
        ref=snapshot.reference
        @firestore.transactional
        def release(transaction):
            current=ref.get(transaction=transaction)
            if not current.exists:return
            item=current.to_dict()
            assert item['ownerId']==uid and item['projectId'] in pids and item['asset']==raw['asset']
            ledgers=repo._ledgers(uid)
            counts=[(ledger.get(transaction=transaction).to_dict() or {}).get('bytes',0) for ledger in ledgers]
            assert all(count>=item['reservedBytes'] for count in counts)
            for ledger,count in zip(ledgers,counts):
                transaction.set(ledger,{'bytes':count-item['reservedBytes'],'updatedAt':utc_now()})
            transaction.delete(ref)
        repo._run(release);removed['uploads']+=1
    for snapshot in projects:
        snapshot.reference.delete();removed['projects']+=1
    db.collection('users').document(uid).delete();removed['profiles']+=1
    ledger=repo._ledgers(uid)[1]
    assert (ledger.get().to_dict() or {}).get('bytes',0)==0
    ledger.delete()
    for collection in ['uploads','projects']:
        assert not list(db.collection(collection).where(filter=FieldFilter('ownerId','==',uid)).stream())
    assert not db.collection('users').document(uid).get().exists
removed['globalReservedBytes']=(db.collection('storageUsage').document('global').get().to_dict() or {}).get('bytes',0)
db.close()
print(json.dumps({'environment':'dev','fixtureCleanup':removed}))
'''


def main():
    evidence = json.loads(Path(sys.argv[1]).read_text())
    run_id = str(uuid.UUID(evidence['runID']))
    owners = evidence['users']
    if evidence['runID'] != run_id or not 1 <= len(owners) <= 4 or any(not isinstance(uid, str) or '/' in uid for uid in owners):
        raise ValueError('Expected one opt-in simulator invocation')
    if not evidence['phases'][-1].startswith(('passed-accounts-deleted-', 'failed-needs-')):
        raise ValueError('Test must have finished before fixture cleanup')
    project = 'footylevels-dev-20261009'
    saved = os.environ.pop('GOOGLE_APPLICATION_CREDENTIALS', None)
    try:
        adc, _ = google.auth.default(scopes=['https://www.googleapis.com/auth/cloud-platform'], quota_project_id=project)
    finally:
        if saved:
            os.environ['GOOGLE_APPLICATION_CREDENTIALS'] = saved
    class AdminADC(credentials.Base):
        def get_credential(self): return adc
    admin = initialize_app(AdminADC(), {'projectId': project}, name='fp16-fixture-cleanup-'+uuid.uuid4().hex)
    try:
        for uid in owners:
            try:
                user = auth.get_user(uid, app=admin)
                if not user.email or not user.email.startswith('fp16-ios-'+run_id+'-') or not user.email.endswith('@example.invalid'):
                    raise ValueError('Fixture Auth account does not match run')
                auth.delete_user(uid, app=admin)
            except auth.UserNotFoundError:
                pass
        for uid in owners:
            try: auth.get_user(uid, app=admin)
            except auth.UserNotFoundError: continue
            raise RuntimeError('Fixture account still present')
        # Run source through a separate script file; evidence travels over stdin.
        remote_file = '~/.local/share/footylevels/cloud-api/cleanup-fp16-fixtures.py'
        upload = subprocess.run(['/usr/bin/ssh','-o','BatchMode=yes','bajajkun@142.55.34.202',
            'umask 077; cat > '+remote_file], input=REMOTE.encode(), capture_output=True, timeout=30)
        if upload.returncode: raise RuntimeError('Cleanup helper transfer failed')
        command = ('cd ~/.local/share/footylevels/cloud-api/current && PYTHONPATH=src:. bash '
            '~/.local/share/footylevels/fp50-dns/with_network_dns.sh '
            '~/.local/share/footylevels/cloud-api/venv/bin/python '+remote_file)
        result = subprocess.run(['/usr/bin/ssh','-o','BatchMode=yes','bajajkun@142.55.34.202',command],
            input=json.dumps({'runID':run_id,'users':owners}).encode(), capture_output=True, timeout=180)
        if result.returncode: raise RuntimeError('Exact fixture cleanup failed; retain evidence IDs')
        report = json.loads(result.stdout)
        print(json.dumps(report), flush=True)
        print('Temporary Firebase accounts and exact fixture records/assets removed; absence verified.')
    finally:
        delete_app(admin)


if __name__ == '__main__':
    try: main()
    except Exception as error:
        print('iOS fixture cleanup failed ('+type(error).__name__+'); no credential diagnostics displayed', file=sys.stderr)
        sys.exit(1)
