"""Real Firebase/Supabase probe on DGX. Tokens arrive only on private SSH stdin.

No credentials, capability URLs or transport exception bodies are printed. Uses
only temporary owners supplied by the Mac probe and removes every test asset.
"""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
import base64
import hashlib
import json
import os
from pathlib import Path
import sys
import time
from urllib.parse import urlsplit, urljoin

from fastapi.testclient import TestClient
from google.cloud import firestore
from google.cloud.firestore_v1.base_query import FieldFilter
import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'infra/dgx'))
from cloud_config import load
from api.cloud import app_factory
from footylevels.assets import SupabaseAssets, FILE_LIMIT
from footylevels.records import utc_now, AssetRef, Engine, PlaybackContract, ReconstructionRecord
from footylevels.uploads import FirestoreUploads, StorageQuotaExceeded
from footylevels.repositories import RecordConflict, RecordNotFound


def check(value, action):
    if not value:
        raise RuntimeError(action)


def tus_upload(grant, data, *, resume=True):
    headers = {**grant['headers'], 'Tus-Resumable': '1.0.0'}
    metadata = ','.join(k+' '+base64.b64encode(v.encode()).decode() for k,v in grant['metadata'].items())
    r = requests.post(grant['endpoint'], headers={**headers, 'Upload-Length': str(len(data)), 'Upload-Metadata': metadata},
                      timeout=30, allow_redirects=False)
    if r.status_code != 201:
        import re
        try:
            body = r.json()
            details = {k: v for k, v in body.items() if k in ('error', 'code', 'statusCode', 'message') and isinstance(v, str) and re.fullmatch(r'[A-Za-z0-9 _.,:()/-]{1,140}', v)}
        except ValueError:
            details = {}
        raise RuntimeError(f'TUS session creation HTTP {r.status_code}: {details}')
    location = urljoin(grant['endpoint'], r.headers['Location'])
    check(urlsplit(location).netloc == urlsplit(grant['endpoint']).netloc and urlsplit(location).scheme == 'https', 'TUS origin')
    first = min(len(data), grant['chunkSize'])
    r = requests.patch(location, data=data[:first], headers={**headers, 'Upload-Offset': '0',
                       'Content-Type': 'application/offset+octet-stream'}, timeout=60, allow_redirects=False)
    check(r.status_code == 204, f'TUS first chunk HTTP {r.status_code}')
    if first < len(data):
        # A fresh transport learns the offset rather than restarting the upload.
        r = requests.head(location, headers=headers, timeout=30, allow_redirects=False)
        check(r.status_code == 200 and int(r.headers['Upload-Offset']) == first, 'TUS resumed offset')
        retry = requests.patch(location, data=data[:first], headers={**headers, 'Upload-Offset': '0',
                                'Content-Type': 'application/offset+octet-stream'}, timeout=60, allow_redirects=False)
        check(retry.status_code == 409, 'TUS stale offset must conflict')
        r = requests.patch(location, data=data[first:], headers={**headers, 'Upload-Offset': str(first),
                           'Content-Type': 'application/offset+octet-stream'}, timeout=60, allow_redirects=False)
        check(r.status_code == 204, f'TUS final chunk HTTP {r.status_code}')


def main():
    supplied = json.load(sys.stdin)
    environment = supplied['environment']
    config = load(environment)
    owners, tokens = supplied['owners'], supplied['tokens']
    check(len(owners) == len(tokens) == 2 and all(x.startswith('fp17-') for x in supplied['emails']), 'Temporary account boundary')
    db = firestore.Client.from_service_account_json(config['GOOGLE_APPLICATION_CREDENTIALS'], project=config['FIREBASE_PROJECT_ID'])
    repo = FirestoreUploads(db)
    store = SupabaseAssets(config['SUPABASE_URL'], config['SUPABASE_SECRET_KEY'])
    assets, project_ids, upload_ids, result_ids, artifact_reservations = [], [], [], [], []
    data = Path(sys.argv[1]).read_bytes() + b'\0' * (6*1024*1024 + 1234)
    digest = hashlib.sha256(data).hexdigest()
    result = {'environment': environment, 'tests': [], 'cleanup': False}
    try:
        http_url = os.environ.get('FOOTYLEVELS_ASSET_PROBE_HTTP')
        if http_url:
            check(environment == 'dev' and http_url == 'http://127.0.0.1:8011', 'Private Development HTTP boundary')
            import httpx
            transport = httpx.Client(base_url=http_url, timeout=90, follow_redirects=False)
            result['transport'] = 'dgx-loopback-http'
        else:
            transport = TestClient(app_factory())
            result['transport'] = 'real-cloud-api-test-client'
        with transport as client:
            headers = [{'Authorization': 'Bearer '+t} for t in tokens]
            result['stage'] = 'bootstrap'
            for uid, header in zip(owners, headers):
                response = client.post('/api/me/bootstrap', headers=header)
                check(response.status_code == 200 and response.json()['id'] == uid, 'Real Firebase identity')
            def create_project(title):
                response = client.post('/api/projects', headers=headers[0], json={'title': title, 'source': 'gallery'})
                check(response.status_code == 201, 'Project creation')
                project_ids.append(response.json()['id'])
                return response.json()
            result['stage'] = 'create-project'
            project = create_project('FP17 resumable test')
            pid = project['id']
            body = {'expectedRevision': 1, 'byteSize': len(data), 'sha256': digest, 'contentType': 'video/mp4'}
            hdr = {**headers[0], 'Idempotency-Key': 'fp17-real-upload-key-12345'}
            def begin(): return client.post(f'/api/projects/{pid}/upload-session', json=body, headers=hdr)
            result['stage'] = 'concurrent-begin'
            with ThreadPoolExecutor(max_workers=2) as pool:
                responses = list(pool.map(lambda _: begin(), range(2)))
            check(all(r.status_code == 200 for r in responses), 'Concurrent create')
            grant = responses[0].json()
            upload_id = grant['id']; upload_ids.append(upload_id)
            record = repo.get_upload(owners[0], upload_id); assets.append(record.asset)
            check(responses[1].json()['id'] == upload_id, 'Concurrent semantic deduplication')
            for method, route in [('get', f'/api/uploads/{upload_id}'), ('post', f'/api/uploads/{upload_id}/refresh'),
                                  ('post', f'/api/uploads/{upload_id}/finalize'), ('delete', f'/api/uploads/{upload_id}')]:
                check(getattr(client, method)(route, headers=headers[1]).status_code == 404, 'Foreign account denial')
                check(getattr(client, method)(route).status_code == 401, 'Anonymous denial')
            check(client.post(f'/api/projects/{pid}/upload-session', json={**body, 'sha256': 'a'*64}, headers=hdr).status_code == 409,
                  'Changed idempotency input denial')
            foreign_metadata = {**grant['metadata'], 'objectName': record.asset.object_key.replace('/source/', '/other/')}
            metadata = ','.join(k+' '+base64.b64encode(v.encode()).decode() for k,v in foreign_metadata.items())
            denied = requests.post(grant['endpoint'], headers={**grant['headers'], 'Tus-Resumable': '1.0.0',
                'Upload-Length': str(len(data)), 'Upload-Metadata': metadata}, timeout=30, allow_redirects=False)
            check(denied.status_code in (400, 401, 403, 404), f'Signed upload must be path-scoped HTTP {denied.status_code}')
            result['stage'] = 'tus-upload'
            tus_upload(grant, data)
            # The client cannot enable overwrite by changing its request headers.
            overwrite = requests.put(store.root+'/object/upload/sign/'+store._path(record.asset),
                params={'token': grant['headers']['x-signature']}, data=b'wrong',
                headers={'Content-Type': 'video/mp4', 'x-upsert': 'true'}, timeout=30, allow_redirects=False)
            check(overwrite.status_code in (400, 401, 403, 409), 'Immutable signed upload')
            with ThreadPoolExecutor(max_workers=2) as pool:
                final = list(pool.map(lambda _: client.post(f'/api/uploads/{upload_id}/finalize', headers=headers[0]), range(2)))
            check(all(r.status_code == 202 and r.json()['inputRevision'] == 2 and r.json()['state'] == 'preparing' for r in final),
                  'Concurrent finalization increments once: '+str([(r.status_code, r.json().get('inputRevision'), r.json().get('state')) for r in final]))
            check(client.post(f'/api/uploads/{upload_id}/finalize', headers=headers[0]).json() == final[0].json(), 'Finalization retry')
            check(client.post(f'/api/uploads/{upload_id}/refresh', headers=headers[0]).status_code == 409, 'Pinned input immutable')
            result['tests'].extend(['real-firebase-two-account-ownership', 'concurrent-idempotent-upload-and-finalization',
                                    'tus-interruption-resume-stale-offset', 'signed-upload-path-and-overwrite-denial'])
            signed = client.post(f'/api/projects/{pid}/assets', headers=headers[0])
            check(signed.status_code == 200 and signed.headers['Cache-Control'] == 'no-store', 'Private asset map')
            r = requests.get(signed.json()['assets']['source']['url'], timeout=60, allow_redirects=False)
            check(r.status_code == 200 and hashlib.sha256(r.content).hexdigest() == digest, 'Signed read bytes')
            public = requests.get(store.root+'/object/public/'+store._path(record.asset), timeout=30, allow_redirects=False)
            check(public.status_code in (400, 401, 403, 404), 'Public bucket denial')
            check(client.post(f'/api/projects/{pid}/assets', headers=headers[1]).status_code == 404, 'Foreign source read denial')
            short = store.signed_read(record.asset, seconds=1)
            time.sleep(3)
            expired = requests.get(short, timeout=30, allow_redirects=False)
            check(expired.status_code in (400, 401, 403, 404), 'Expired capability denial')
            check(client.post(f'/api/projects/{pid}/assets', headers=headers[0]).status_code == 200, 'Owner refresh')
            result['tests'].append('private-download-checksum-expiry-refresh')
            # Seed one previously verified real GLB to test private output delivery,
            # not a claim that authenticated model-job generation is integrated.
            model_path = Path(sys.argv[1]).with_name('body.glb')
            job_id = 'fp17-artifact-' + pid
            generated = AssetRef(bucket='private-media', object_key=f'users/{owners[0]}/projects/{pid}/reconstruction/{job_id}/1/body.glb',
                byte_size=model_path.stat().st_size, sha256=hashlib.sha256(model_path.read_bytes()).hexdigest(), content_type='model/gltf-binary')
            reservation = repo.reserve_artifact(owners[0], pid, generated)
            artifact_reservations.append(reservation); assets.append(generated)
            check(repo.reserve_artifact(owners[0], pid, generated) == reservation, 'Artifact quota reservation retry')
            store.put_file(generated, model_path)
            store.put_file(generated, model_path)
            reconstruction = ReconstructionRecord(id=job_id, owner_id=owners[0], project_id=pid, job_id=job_id,
                input_revision=2, profile_version='fp17-delivery-fixture', source_asset=record.asset, mode='keyframes', outcome='ready',
                engine=Engine(model_name='sam-3d-body-dinov3', upstream_commit='b5c765a0d89d789985e186d396315e7590887b94',
                    checkpoint_hash='b5a2f9d305dd02626b967aa2e86021fba07065df66ce7a7e00ffb9664f150abf', config_hash='a'*64),
                contract=PlaybackContract(topology_version='fp52-fixture', encoding='glb'), artifacts=(generated,),
                requested_frame_count=1, reconstructed_frame_count=1, missing_frame_count=0)
            db.collection('reconstructions').document(job_id).create(reconstruction.document()); result_ids.append(job_id)
            route = f'/api/reconstructions/{job_id}/assets'
            check(client.post(route).status_code == 401 and client.post(route, headers=headers[1]).status_code == 404, 'Private output owner denial')
            outputs = client.post(route, headers=headers[0])
            check(outputs.status_code == 200 and set(outputs.json()['assets']) == {'body.glb'}, 'Output allowlist')
            downloaded = requests.get(outputs.json()['assets']['body.glb']['url'], timeout=30, allow_redirects=False)
            check(downloaded.status_code == 200 and downloaded.content[:4] == b'glTF'
                  and hashlib.sha256(downloaded.content).hexdigest() == generated.sha256, 'Real GLB signed delivery')
            result['tests'].append('saved-real-glb-private-delivery-and-immutable-budget')
            # A wrong checksum never pins a source. Its reservation survives failure.
            bad_project = create_project('FP17 rejected source')
            bad = client.post(f"/api/projects/{bad_project['id']}/upload-session", headers={**headers[0], 'Idempotency-Key': 'fp17-invalid-upload-key'},
                json={**body, 'byteSize': 3, 'sha256': 'b'*64}).json()
            bad_record = repo.get_upload(owners[0], bad['id']); assets.append(bad_record.asset); upload_ids.append(bad['id'])
            tus_upload(bad, b'abc')
            check(client.post(f"/api/uploads/{bad['id']}/finalize", headers=headers[0]).status_code == 409, 'Wrong checksum denial')
            check(repo.get_project(owners[0], bad_project['id']).video is None, 'No source pinned on failure')
            check(client.delete(f"/api/uploads/{bad['id']}", headers=headers[0]).status_code == 202, 'Cancel upload')
            with_error = False
            try: repo.cleanup_expired(bad['id'], store)
            except RecordConflict: with_error = True
            check(with_error, 'Cleanup must wait for outstanding capabilities')
            db.collection('uploads').document(bad['id']).update({'expiresAt': utc_now()-timedelta(hours=50), 'cleanupAfter': utc_now()-timedelta(hours=1)})
            with ThreadPoolExecutor(max_workers=2) as pool:
                clean = list(pool.map(lambda _: repo.cleanup_expired(bad['id'], store), range(2)))
            check(all(r.reserved_bytes == 0 for r in clean), 'Cleanup/release idempotence')
            result['tests'].append('checksum-failure-cancel-quarantine-cleanup')
            # Bound global/user reservations under real concurrent transactions.
            reserved = []
            try:
                for index in range(3):
                    p = create_project('FP17 quota test')
                    r = repo.begin(owners[0], p['id'], 1, 12, 'a'*64, 'video/mp4', f'fp17-quota-reservation-{index}')
                    reserved.append(r); upload_ids.append(r.id); assets.append(r.asset)
                p = create_project('FP17 quota overflow')
                try:
                    repo.begin(owners[0], p['id'], 1, 12, 'a'*64, 'video/mp4', 'fp17-quota-overflow-key')
                    raise RuntimeError('Quota overflow accepted')
                except StorageQuotaExceeded: pass
            finally:
                pass
            result['tests'].append('free-storage-quota-reservations')
            client.delete(f'/api/projects/{pid}', headers=headers[0])
            check(client.post(f'/api/projects/{pid}/assets', headers=headers[0]).status_code == 404, 'Deleted source denial')
            result['tests'].append('deleted-project-asset-denial')
    except Exception as error:
        result['failure'] = str(error) if type(error) is RuntimeError else type(error).__name__
        raise
    finally:
        # Known test clients are finished; no request is in flight. Exact test IDs only.
        failures = []
        for asset in assets:
            try: store.delete_exact(asset); store.verify_absent(asset)
            except Exception: failures.append('asset')
        for snapshot in db.collection('uploads').where(filter=FieldFilter('ownerId', '==', owners[0])).stream():
            raw = snapshot.to_dict()
            if raw['projectId'] in project_ids and snapshot.id not in upload_ids:
                upload_ids.append(snapshot.id)
                assets.append(AssetRef.model_validate(raw['asset']))
        for upload_id in upload_ids:
            ref = db.collection('uploads').document(upload_id)
            @firestore.transactional
            def cleanup(transaction):
                snap = ref.get(transaction=transaction)
                if not snap.exists: return
                raw = snap.to_dict()
                check(raw['ownerId'] == owners[0] and raw['projectId'] in project_ids, 'Fixture cleanup ownership')
                ledgers = repo._ledgers(owners[0]); values = [(r.get(transaction=transaction).to_dict() or {}).get('bytes', 0) for r in ledgers]
                count = raw['reservedBytes']
                check(all(v >= count for v in values), 'Fixture ledger cleanup accounting')
                for ledger, value in zip(ledgers, values): transaction.set(ledger, {'bytes': value-count, 'updatedAt': utc_now()})
                transaction.delete(ref)
            try: cleanup(db.transaction())
            except Exception: failures.append('reservation')
        for identifier in artifact_reservations:
            ref = db.collection('assetReservations').document(identifier)
            @firestore.transactional
            def release_artifact(transaction):
                snap = ref.get(transaction=transaction)
                if not snap.exists: return
                raw = snap.to_dict()
                check(raw['ownerId'] == owners[0] and raw['projectId'] in project_ids, 'Fixture artifact ownership')
                ledgers = repo._ledgers(owners[0]); values = [(r.get(transaction=transaction).to_dict() or {}).get('bytes', 0) for r in ledgers]
                check(all(v >= raw['bytes'] for v in values), 'Artifact fixture accounting')
                for ledger, value in zip(ledgers, values): transaction.set(ledger, {'bytes': value-raw['bytes'], 'updatedAt': utc_now()})
                transaction.delete(ref)
            try: repo._run(release_artifact)
            except Exception: failures.append('artifact reservation')
        for identifier in result_ids:
            try: db.collection('reconstructions').document(identifier).delete()
            except Exception: failures.append('reconstruction')
        for pid in project_ids:
            try: db.collection('projects').document(pid).delete()
            except Exception: failures.append('project')
        for uid in owners:
            try:
                db.collection('users').document(uid).delete()
                ledger = repo._ledgers(uid)[1]
                value = (ledger.get().to_dict() or {}).get('bytes', 0)
                check(value == 0, 'Fixture user ledger must be empty')
                ledger.delete()
                check(not list(db.collection('uploads').where(filter=FieldFilter('ownerId', '==', uid)).stream()), 'No fixture uploads remain')
            except Exception: failures.append('user metadata')
        db.close()
        result['cleanup'] = not failures
        print(json.dumps(result), flush=True)
        if failures: raise RuntimeError('Fixture cleanup requires attention')


if __name__ == '__main__':
    try: main()
    except Exception as error:
        print('Private storage probe failed ('+type(error).__name__+'); '+(str(error) if type(error) is RuntimeError else 'no secret diagnostics displayed'), file=sys.stderr)
        sys.exit(1)
