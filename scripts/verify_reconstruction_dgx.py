#!/usr/bin/env python3
"""Opt-in Development GPU/cloud probe, using only an existing permitted clip.

Reclaims an abandoned claim through the running dispatcher, verifies private
publication and cached replay, then removes this exact random fixture's assets.
This is a server-path check, not authenticated iOS end-to-end verification.
"""
import argparse
from datetime import timedelta
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT/'src'), str(ROOT/'infra/dgx'), str(ROOT/'runtime/skill_lab'), str(ROOT/'scripts')]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--fixture', type=Path, required=True)
    args = parser.parse_args()
    if os.environ.get('FOOTYLEVELS_LIVE_RECONSTRUCTION_TEST') != '1':
        raise SystemExit('Set FOOTYLEVELS_LIVE_RECONSTRUCTION_TEST=1 to opt in')
    from cloud_config import load
    load('dev')
    assert os.environ['FIREBASE_PROJECT_ID'] == 'footylevels-dev-20261009'
    from google.cloud import firestore
    from backend.model_bridge import receive, send
    from footylevels.assets import SupabaseAssets
    from footylevels.media_jobs import FirestoreMediaJobs
    from footylevels.reconstruction_jobs import FirestoreReconstructionJobs
    from footylevels.records import KeyframeInput, ReconstructionRecord, utc_now
    from footylevels.model_requests import BODY_PROFILE, body_input_hash
    from footylevels.repositories import RecordConflict
    from verify_media_worker_dgx import cleanup
    ffmpeg = Path.home()/'skill-lab/.media/bin/ffmpeg'
    bridge_root = Path.home()/'.local/share/footylevels/model-bridge'
    adapter_command=[sys.executable,str(ROOT/'infra/dgx/body_keyframes_adapter.py'),
        '--socket',str(bridge_root/'inference.sock'),'--ffmpeg',str(ffmpeg)]
    def health():
        with socket.socket(socket.AF_UNIX) as stream:
            stream.settimeout(10)
            stream.connect(str(bridge_root/'inference.sock'))
            send(stream, {'op':'health'})
            return receive(stream)
    before = health()
    assert before['status'] == 'ok' and before['activeRequests'] == 0
    cache_before = set(p.name for p in (bridge_root/'cache').iterdir())
    client = firestore.Client.from_service_account_json(os.environ['GOOGLE_APPLICATION_CREDENTIALS'],
        project=os.environ['FIREBASE_PROJECT_ID'])
    media_jobs, jobs = FirestoreMediaJobs(client), FirestoreReconstructionJobs(client)
    storage = SupabaseAssets(os.environ['SUPABASE_URL'], os.environ['SUPABASE_SECRET_KEY'])
    uid, project = 'fp53-model-'+uuid.uuid4().hex, None
    probe_job_ids = set()
    recovery = Path.home()/'.local/share/footylevels/cloud-api/fp53-last-fixture.json'
    with recovery.open('x') as saved:
        os.chmod(recovery, 0o600)
        saved.write(json.dumps({'ownerId':uid}))
    evidence = {'ticket':'FP-53','environment':'dev','gpuUsed':True,'fullIosEndToEnd':False}
    try:
        with tempfile.TemporaryDirectory(prefix='fp53-verification-') as folder:
            directory = Path(folder)
            source = directory/'source.mp4'
            subprocess.run([str(ffmpeg), '-hide_banner','-loglevel','error','-nostdin','-i',str(args.fixture),
                '-t','2','-an','-c:v','libx264','-threads','2','-crf','25',str(source)],
                check=True, timeout=30, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            jobs.bootstrap_user(uid,'Temporary reconstruction verification')
            project = jobs.create_project(uid,'FP53 isolated GPU fixture','gallery')
            recovery.write_text(json.dumps({'ownerId':uid,'projectId':project.id}))
            upload = media_jobs.begin(uid,project.id,1,source.stat().st_size,
                hashlib.sha256(source.read_bytes()).hexdigest(),'video/mp4',uuid.uuid4().hex)
            storage.put_file(upload.asset,source)
            media_jobs.commit_upload(uid,upload.id)
            deadline=time.monotonic()+240
            while jobs.get_project(uid,project.id).state=='preparing' and time.monotonic()<deadline:
                time.sleep(2)
            ready=jobs.get_project(uid,project.id)
            assert ready.state=='ready' and ready.prepared_media
            frames=[KeyframeInput(frame_index=i,bbox=(.055,.21,.64,.79)) for i in [0,15]]
            digest=body_input_hash(ready.prepared_media.video,frames)
            job=jobs.enqueue_reconstruction(uid,project.id,ready.input_revision,'reconstructKeyframes',digest,
                BODY_PROFILE,keyframes=frames)
            probe_job_ids.add(job.id)
            abandoned=jobs.claim(job.id,'fp53-abandoned-probe')
            assert abandoned and abandoned.attempt_count==1
            client.collection('jobs').document(job.id).update({'leaseExpiresAt':utc_now()-timedelta(seconds=1)})
            deadline=time.monotonic()+300
            persisted=jobs.get_job(uid,job.id)
            while persisted.status in {'queued','running','retrying'} and time.monotonic()<deadline:
                time.sleep(2)
                persisted=jobs.get_job(uid,job.id)
            assert persisted.status=='succeeded', persisted.status
            assert persisted.attempt_count==2
            result=jobs.get_reconstruction(uid,job.id)
            assert result.reconstructed_frame_count==2 and result.missing_frame_count==0
            assert result.source_asset==ready.prepared_media.video
            assert jobs.get_project(uid,project.id).video==upload.asset
            assert jobs.get_project(uid,project.id).latest_reconstruction_id==job.id
            for artifact in result.artifacts:
                storage.verify(artifact)
                destination=directory/artifact.object_key.rsplit('/',1)[-1]
                storage.download_file(artifact,destination)
                if artifact.content_type=='model/gltf-binary':
                    # Independently decode geometry in the existing GPU env.
                    program='import sys,trimesh,numpy as np; m=trimesh.load(sys.argv[1],force="mesh",process=False); assert len(m.vertices)==18439 and len(m.faces)==36874 and np.isfinite(m.vertices).all()'
                    subprocess.run([str(Path.home()/'skill-lab/.venv-gpu/bin/python'),'-c',program,str(destination)],
                        check=True,timeout=30,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            with_reconstruction=health()
            assert with_reconstruction['inferenceCalls']-before['inferenceCalls']==2
            # Replay exact same frame/attempt in a scrubbed actual CPU child.
            replay=directory/'replay'
            replay.mkdir(mode=0o700)
            (replay/'artifacts').mkdir()
            storage.download_file(result.source_asset,replay/'input.mp4')
            (replay/'job.json').write_text(persisted.model_dump_json(by_alias=True,
                exclude={'lease_token','worker_id','lease_expires_at'}))
            subprocess.run([*adapter_command,str(replay)],
                env={'PATH':os.defpath,'HOME':str(replay),'TMPDIR':str(replay)},check=True,timeout=60,
                stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            repeated=ReconstructionRecord.model_validate_json((replay/'result.json').read_text())
            assert repeated.artifacts==result.artifacts and health()['inferenceCalls']==with_reconstruction['inferenceCalls']
            assert jobs.enqueue_reconstruction(uid,project.id,ready.input_revision,'reconstructKeyframes',digest,
                BODY_PROFILE,keyframes=frames).id==job.id
            assert jobs.claim(job.id,'duplicate-after-completion') is None
            try: jobs.checkpoint(abandoned)
            except RecordConflict: pass
            else: raise AssertionError('Old attempt retained publication rights')
            assert jobs.publish_reconstruction(result,'lost-ack-replay')==result
            evidence.update({'passed':True,'runningDispatcherVerified':True,'abandonedClaimRecovered':True,
                'attemptCount':2,'generatedBodies':2,'outputChecksumsVerified':len(result.artifacts),
                'decodedVerticesPerBody':18439,'decodedFacesPerBody':36874,'cachedReplayAddedInference':False,
                'oldLeaseRejected':True,'duplicateClaimsIgnored':True,'completionReplayVerified':True,
                'originalSourcePreserved':True,'engine':result.engine.model_dump(mode='json',by_alias=True),
                'topologyVersion':result.contract.topology_version})
            # Cancel a real in-flight model request. The serial GPU may finish
            # into its private cache, but its invalidated lease cannot publish.
            from concurrent.futures import ThreadPoolExecutor
            from footylevels.reconstruction_worker import run_job
            cancel_frames=[KeyframeInput(frame_index=15,bbox=(.055,.21,.64,.78))]
            cancel_job=jobs.enqueue_reconstruction(uid,project.id,ready.input_revision,'reconstructKeyframes',
                body_input_hash(ready.prepared_media.video,cancel_frames),BODY_PROFILE,keyframes=cancel_frames)
            probe_job_ids.add(cancel_job.id)
            cancel_claim=jobs.claim(cancel_job.id,'fp53-cancellation-probe')
            assert cancel_claim
            calls_before_cancel=health()['inferenceCalls']
            staging=directory/'cancel-staging'
            staging.mkdir()
            with ThreadPoolExecutor(max_workers=1) as executor:
                attempt=executor.submit(run_job,jobs,storage,cancel_claim,
                    adapter_command,staging,
                    heartbeat_seconds=.1,command_timeout=30)
                deadline=time.monotonic()+15
                observed=False
                while not attempt.done() and time.monotonic()<deadline:
                    if health()['inferenceCalls']>calls_before_cancel:
                        observed=True
                        break
                    time.sleep(.02)
                assert observed,'Real model inference was not observed before cancellation'
                assert jobs.cancel_job(uid,cancel_job.id).status=='cancelled'
                assert attempt.result(timeout=40)=='superseded'
            deadline=time.monotonic()+190
            while health()['activeRequests'] and time.monotonic()<deadline:
                time.sleep(.1)
            assert health()['activeRequests']==0
            assert not client.collection('reconstructions').document(cancel_job.id).get().exists
            assert jobs.get_project(uid,project.id).active_job_id is None
            assert not list(staging.iterdir())
            evidence.update({'realInFlightCancellationVerified':True,'cancelledResultNotPublished':True,
                'cancelledAdapterReapedAndStagingRemoved':True,'modelQuiescentAfterCancellation':True})
    finally:
        if project:
            evidence['fixtureCleanup']=cleanup(client,media_jobs,storage,uid,project.id)
        else:
            client.collection('users').document(uid).delete()
        # Only cache entries created by this probe, after its requests quiesce.
        import shutil
        if health()['activeRequests']:
            raise ValueError('Model still active; retain private recovery evidence')
        for path in (bridge_root/'cache').iterdir():
            if path.name not in cache_before and path.is_dir() and not path.is_symlink():
                # New non-probe requests could arrive during this run. Restrict
                # removal further using request identity stored by the bridge.
                identity=path/'request.json'
                if identity.exists() and json.loads(identity.read_text()).get('jobId') in probe_job_ids:
                    shutil.rmtree(path)
        recovery.unlink()
        client.close()
    print(json.dumps(evidence))


if __name__=='__main__':
    main()
