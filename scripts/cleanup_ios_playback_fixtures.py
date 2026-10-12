"""Exact Development-only cleanup after the opt-in FP-40 iOS proof finishes.

The evidence carries IDs and phases, never credentials. Mac ADC verifies/removes
only its exact temporary Firebase account. DGX cleanup waits for all writers to
quiesce, validates the single UUID-titled project, and subtracts exact quota bytes.
No runtime deployment, GPU restart, or unrelated account/object deletion.
"""
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import uuid

REMOTE = r'''
import json, os, re, shutil, socket, stat, sys, time
from pathlib import Path
from google.cloud import firestore
from google.cloud.firestore_v1.base_query import FieldFilter
from infra.dgx.cloud_config import load
from footylevels.assets import SupabaseAssets
from footylevels.media_jobs import FirestoreMediaJobs
from backend.model_bridge import send, receive
from verify_media_worker_dgx import cleanup
body=json.load(sys.stdin)
config=load('dev')
assert config['FIREBASE_PROJECT_ID']=='footylevels-dev-20261009'
uid=body['users'][0]; pids=body['projects']; base=Path.home()/'.local/share/footylevels'
db=firestore.Client.from_service_account_json(os.environ['GOOGLE_APPLICATION_CREDENTIALS'],project=config['FIREBASE_PROJECT_ID'])
repo=FirestoreMediaJobs(db); store=SupabaseAssets(config['SUPABASE_URL'],config['SUPABASE_SECRET_KEY'])
owned=lambda collection:list(db.collection(collection).where(filter=FieldFilter('ownerId','==',uid)).stream())
jobs=owned('jobs'); jids={s.id for s in jobs}
assert set(body['jobs']) <= jids
for snapshot in jobs:
    assert snapshot.to_dict()['projectId'] in pids
    if snapshot.to_dict()['status'] in {'queued','running','retrying'}:repo.cancel_job(uid,snapshot.id)
def health():
    with socket.socket(socket.AF_UNIX) as stream:
        stream.settimeout(10);stream.connect(str(base/'model-bridge/inference.sock'))
        send(stream,{'op':'health'});return receive(stream)
def quiet():
    return health()['activeRequests']==0 and all(not any(p.is_dir() for p in (base/name/'dev').iterdir())
        for name in ['media-worker','reconstruction-worker'])
deadline=time.monotonic()+250
while not quiet() and time.monotonic()<deadline:time.sleep(.5)
assert quiet(),'Writers remain; retain evidence and retry later'
reservations=owned('assetReservations')
result_count=sum('/reconstructions/' in s.to_dict()['asset']['objectKey'] for s in reservations)
if pids:
    removed=cleanup(db,repo,store,uid,pids[0],ios_playback_run_id=body['runID'])
else:
    assert not any(owned(c) for c in ['projects','uploads','jobs','reconstructions','assetReservations','comparisons'])
    db.collection('users').document(uid).delete()
    removed={'sourceObjects':0,'preparedObjects':0,'absenceVerified':True}
cache_count=0
for name in ['cache','motion-cache']:
    root=base/'model-bridge'/name
    if not root.exists():continue
    assert not root.is_symlink() and stat.S_IMODE(root.stat().st_mode)&0o077==0
    for marker in root.glob('*/request.json'):
        folder=marker.parent
        assert not folder.is_symlink() and re.fullmatch(r'[a-f0-9]{64}',folder.name)
        if json.loads(marker.read_text()).get('jobId') in jids:
            shutil.rmtree(folder);cache_count+=1
    assert not any(json.loads(p.read_text()).get('jobId') in jids for p in root.glob('*/request.json'))
assert quiet()
removed['resultObjects']=result_count
removed['preparedObjects']-=result_count
removed['privateModelCacheEntries']=cache_count
removed['globalReservedBytes']=(db.collection('storageUsage').document('global').get().to_dict() or {}).get('bytes',0)
print(json.dumps({'ticket':'FP-40','environment':'dev','accountsAbsentVerified':True,'fixtureCleanup':removed,'writersQuiescent':True}))
db.close()
'''


def validated_evidence(evidence):
    run = str(uuid.UUID(evidence['runID']))
    if (run != evidence['runID'] or evidence.get('ticket') != 'FP-40' or evidence.get('environment') != 'dev'
            or len(evidence['users']) != 1 or len(evidence['projects']) > 1
            or any(not isinstance(i, str) or not re.fullmatch(r'[a-zA-Z0-9_-]{1,128}', i)
                   for i in [*evidence['users'], *evidence['projects'], *evidence['jobs']])
            or not evidence['phases'][-1].startswith(('passed-account-deleted-', 'failed-awaiting-'))):
        raise ValueError('Expected a finished Development FP-40 invocation')
    return {key: evidence[key] for key in ['runID', 'users', 'projects', 'jobs']}


def main():
    from firebase_admin import auth, credentials, delete_app, initialize_app
    import google.auth
    body = validated_evidence(json.loads(Path(sys.argv[1]).read_text()))
    project = 'footylevels-dev-20261009'
    saved = os.environ.pop('GOOGLE_APPLICATION_CREDENTIALS', None)
    try:
        adc, _ = google.auth.default(scopes=['https://www.googleapis.com/auth/cloud-platform'], quota_project_id=project)
    finally:
        if saved: os.environ['GOOGLE_APPLICATION_CREDENTIALS'] = saved
    class AdminADC(credentials.Base):
        def get_credential(self): return adc
    admin = initialize_app(AdminADC(), {'projectId': project}, name='fp40-cleanup-' + uuid.uuid4().hex)
    ssh = ['/usr/bin/ssh', '-o', 'BatchMode=yes', 'bajajkun@142.55.34.202']
    folder = '~/.local/share/footylevels/cloud-api/fp40-proof'
    try:
        uid = body['users'][0]
        try:
            user = auth.get_user(uid, app=admin)
            if user.email != 'fp40-ios-' + body['runID'] + '@example.invalid':
                raise ValueError('Auth fixture does not match invocation')
            auth.delete_user(uid, app=admin)
        except auth.UserNotFoundError: pass
        try: auth.get_user(uid, app=admin)
        except auth.UserNotFoundError: pass
        else: raise RuntimeError('Fixture Auth account remains')
        # Copy trusted local cleanup code, never an SDK error or caller script.
        guard = Path(__file__).with_name('verify_media_worker_dgx.py').read_bytes()
        for name, content in [('verify_media_worker_dgx.py', guard), ('cleanup.py', REMOTE.encode())]:
            result = subprocess.run([*ssh, 'umask 077; mkdir -p '+folder+'; cat > '+folder+'/'+name],
                input=content, capture_output=True, timeout=30)
            if result.returncode: raise RuntimeError('Trusted helper transfer failed')
        command = ('cd ~/.local/share/footylevels/cloud-api/current && PYTHONPATH=src:.:runtime/skill_lab bash '
            '~/.local/share/footylevels/fp50-dns/with_network_dns.sh '
            '~/.local/share/footylevels/cloud-api/venv/bin/python '+folder+'/cleanup.py')
        result = subprocess.run([*ssh, command], input=json.dumps(body).encode(), capture_output=True, timeout=360)
        if result.returncode: raise RuntimeError('Exact cleanup failed; retain evidence IDs')
        print(json.dumps(json.loads(result.stdout)), flush=True)
    finally: delete_app(admin)


if __name__ == '__main__':
    try: main()
    except Exception as error:
        print('FP-40 cleanup failed (' + type(error).__name__ + '); no credential diagnostics displayed', file=sys.stderr)
        sys.exit(1)
