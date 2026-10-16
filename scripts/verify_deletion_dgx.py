#!/usr/bin/env python3
"""Opt-in real private-storage cleanup, with a scoped synthetic owner/old deadlines.

No client capabilities, Firebase accounts or GPU jobs are created. The real
supervised worker must finish cleanup after an injected first storage failure.
"""
import argparse
from datetime import timedelta
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import uuid


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--backend-root',type=Path,default=Path(__file__).resolve().parents[1])
    args=parser.parse_args();root=args.backend_root.resolve()
    if os.environ.get('FOOTYLEVELS_LIVE_DELETION_TEST')!='1':raise SystemExit('Explicit opt-in required')
    sys.path[:0]=[str(root/'src'),str(root/'infra/dgx'),str(root/'scripts')]
    from cloud_config import load
    from google.cloud import firestore
    from footylevels.assets import SupabaseAssets,StorageUnavailable
    from footylevels.deletions import FirestoreDeletions
    from footylevels.records import (AssetRef,ComparisonRecord,Engine,JobRecord,PlaybackContract,ReconstructionRecord,utc_now)
    from footylevels.repositories import RecordNotFound
    from verify_media_worker_dgx import cleanup
    config=load('dev');assert config['FIREBASE_PROJECT_ID']=='footylevels-dev-20261009'
    db=firestore.Client.from_service_account_json(os.environ['GOOGLE_APPLICATION_CREDENTIALS'],project=config['FIREBASE_PROJECT_ID'])
    jobs=FirestoreDeletions(db);storage=SupabaseAssets(config['SUPABASE_URL'],config['SUPABASE_SECRET_KEY'])
    uid='fp54-media-'+uuid.uuid4().hex;project=None
    folder=Path.home()/'.local/share/footylevels/cloud-api/fp18-proof';folder.mkdir(exist_ok=True,mode=0o700)
    marker=folder/'recovery.json';marker.write_text(json.dumps({'ownerId':uid}));marker.chmod(0o600)
    evidence={'ticket':'FP-18','environment':'dev','cloudRelease':root.name,'gpuUsed':False,
        'signedCapabilitiesIssued':False,'eligibilityTimestampsSyntheticallyAged':True,'real72HourExpiryWaited':False,
        'fullIosEndToEnd':False,'identity':'Trusted synthetic fixture owner; no Firebase login account'}
    try:
        jobs.bootstrap_user(uid,'Temporary deletion verification')
        project=jobs.create_project(uid,'FP18 isolated deletion fixture','gallery')
        marker.write_text(json.dumps({'ownerId':uid,'projectId':project.id}))
        with tempfile.TemporaryDirectory(prefix='fp18-verification-') as tmp:
            directory=Path(tmp);source=directory/'source.mp4'
            subprocess.run([str(Path.home()/'skill-lab/.media/bin/ffmpeg'),'-hide_banner','-loglevel','error','-nostdin','-f','lavfi','-i','testsrc2=size=160x120:rate=30:duration=1','-an','-c:v','libx264','-threads','1',str(source)],check=True,capture_output=True,timeout=30)
            upload=jobs.begin(uid,project.id,1,source.stat().st_size,hashlib.sha256(source.read_bytes()).hexdigest(),'video/mp4',uuid.uuid4().hex)
            storage.put_file(upload.asset,source)
            project=jobs.commit_upload(uid,upload.id)
            identifier=hashlib.sha256((uid+'derived-fixture').encode()).hexdigest()
            assets=[]
            for name,data,kind in [('manifest.json',b'{"syntheticDeletionFixture":true}','application/json'),('body.glb',b'Synthetic deletion fixture; not a generated model','model/gltf-binary')]:
                path=directory/name;path.write_bytes(data)
                asset=AssetRef(bucket='private-media',object_key=f'users/{uid}/projects/{project.id}/reconstruction/{identifier}/1/{name}',sha256=hashlib.sha256(data).hexdigest(),byte_size=len(data),content_type=kind)
                jobs.reserve_artifact(uid,project.id,asset);storage.put_file(asset,path);assets.append(asset)
            job=JobRecord(id=identifier,owner_id=uid,project_id=project.id,kind='reconstructMotion',input_revision=project.input_revision,input_hash='e'*64,profile_version='deletion-fixture-v1',source_asset=upload.asset,status='succeeded',stage='succeeded',reconstruction_id=identifier,finished_at=utc_now())
            result=ReconstructionRecord(id=identifier,owner_id=uid,project_id=project.id,job_id=identifier,input_revision=project.input_revision,profile_version=job.profile_version,source_asset=upload.asset,mode='motion',outcome='ready',engine=Engine(model_name='Synthetic deletion fixture',upstream_commit='a'*40,checkpoint_hash='b'*64,config_hash='c'*64),contract=PlaybackContract(topology_version='fixture-v1',encoding='glb'),artifacts=tuple(assets),requested_frame_count=1,reconstructed_frame_count=1,missing_frame_count=0)
            db.collection('jobs').document(identifier).create(job.document());db.collection('reconstructions').document(identifier).create(result.document())
            comparison=ComparisonRecord(id=uuid.uuid4().hex,owner_id=uid,left_reconstruction_id=identifier,right_type='reconstruction',right_id=identifier,mode='overlay')
            jobs.create_comparison(comparison,uid)
            nested=db.collection('projects').document(project.id).collection('frames').document('selected');nested.set({'private':'fixture'})
            intent=jobs.begin_project(uid,project.id)
            try:jobs.get_project(uid,project.id)
            except RecordNotFound:pass
            else:raise AssertionError('Tombstone did not hide project')
            assert jobs.process(intent.id,storage).state=='pending'
            storage.verify(upload.asset)
            # No signatures were issued, and no GPU writer exists. Before aging
            # only this fixture's source/output deadlines, wait for owned writers.
            base=Path.home()/'.local/share/footylevels'
            deadline=time.monotonic()+180
            def quiet():return all(not any(p.is_dir() for p in (base/n/'dev').iterdir()) for n in ['media-worker','reconstruction-worker'])
            while not quiet() and time.monotonic()<deadline:time.sleep(1)
            assert quiet()
            old=utc_now()-timedelta(days=4)
            db.collection('uploads').document(upload.id).update({'cleanupAfter':old,'expiresAt':old-timedelta(days=2)})
            from google.cloud.firestore_v1.base_query import FieldFilter
            reservations=list(db.collection('assetReservations').where(filter=FieldFilter('ownerId','==',uid)).stream())
            for s in reservations:s.reference.update({'createdAt':old})
            db.collection('deletions').document(intent.id).update({'notBefore':old})
            class FailFirst:
                def delete_exact(self,asset):raise StorageUnavailable()
                def verify_absent(self,asset):raise AssertionError('Failed deletion must not release quota')
            failed=jobs.process(intent.id,FailFirst());assert failed.last_error=='cleanup_unavailable' and failed.state=='pending'
            used=sum(s.to_dict()['bytes'] for s in reservations)+upload.asset.byte_size
            assert jobs._ledgers(uid)[1].get().to_dict()['bytes']==used
            print(json.dumps({'phase':'quarantine-and-injected-failure-verified','registeredObjects':1+len(reservations)}),flush=True)
            # Normal deployed maintenance, not a one-shot bypass, must resume it.
            deadline=time.monotonic()+600
            while time.monotonic()<deadline:
                progress=jobs.progress(uid,intent.id)
                if progress.state=='completed':break
                time.sleep(3)
            assert progress.state=='completed' and progress.completed_at
            for asset in [upload.asset,*assets]:storage.verify_absent(asset)
            assert not nested.get().exists and not db.collection('comparisons').document(comparison.id).get().exists
            assert not db.collection('jobs').document(identifier).get().exists and not db.collection('reconstructions').document(identifier).get().exists
            assert jobs._ledgers(uid)[1].get().to_dict()['bytes']==0
            erased=db.collection('projects').document(project.id).get().to_dict()
            assert erased['state']=='deleted' and erased['video'] is None and erased['title']=='Deleted recording'
            assert jobs.process(intent.id,storage)==progress
            evidence.update(passed=True,automaticWorkerResumed=True,tombstoneDenial=True,pendingQuarantineHonest=True,injectedFailurePreservedQuota=True,registeredObjectsRemoved=1+len(reservations),sourceAndDerivedAbsent=True,comparisonAndNestedMetadataRemoved=True,ownerQuotaReleased=True,completedReplayIdempotent=True)
    finally:
        if project is not None:
            removed=cleanup(db,jobs,storage,uid,project.id);evidence['exactFixtureCleanup']=removed
        else:db.collection('users').document(uid).delete()
        marker.unlink(missing_ok=True);db.close()
    (folder/'evidence.json').write_text(json.dumps(evidence,indent=2)+'\n')
    print(json.dumps(evidence),flush=True)


if __name__=='__main__':
    try:main()
    except Exception as error:
        print(json.dumps({'passed':False,'errorType':type(error).__name__,'action':'Retain exact private recovery marker; no credential diagnostics'}),flush=True)
        raise SystemExit(1)
