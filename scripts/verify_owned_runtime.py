"""Bounded real compatibility API smoke test; removes only its created project.

The fixture must already be permitted for internal testing. This does not assert
Firebase ownership, Supabase persistence or redistribution rights.
"""
import argparse
import json
from pathlib import Path
import time
from urllib.parse import urlsplit

import requests

from scripts.verify_dgx_artifacts import verify_glb, verify_motion


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', required=True)
    parser.add_argument('--fixture', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    base = args.url.rstrip('/')
    if urlsplit(base).scheme != 'https':
        raise ValueError('Use the approved HTTPS endpoint')
    args.output.mkdir(parents=True, exist_ok=True)
    session = requests.Session()
    session.headers['ngrok-skip-browser-warning'] = 'true'
    project_id = None
    evidence = {}

    def call(method, path, status=200, **kwargs):
        if not path.startswith('/') or path.startswith('//'):
            raise ValueError('Expected a relative application path')
        response = session.request(method, base + path, timeout=120, allow_redirects=False, **kwargs)
        if response.status_code != status:
            raise RuntimeError(f'{method} application request returned HTTP {response.status_code}; expected {status}')
        return response

    def wait(kind, deadline=300):
        until = time.monotonic() + deadline
        last = None
        while time.monotonic() < until:
            project = call('GET', f'/api/projects/{project_id}').json()
            state = project[kind]
            progress = (state['status'], state.get('stage'))
            if progress != last:
                print(f'{kind}: {progress}', flush=True)
                last = progress
            if state['status'] not in ('queued', 'running'):
                return project
            time.sleep(2)
        raise TimeoutError('Bounded reconstruction deadline exceeded; retain exact project ID for recovery')

    try:
        health = call('GET', '/api/health').json()
        assert health['runtime']['owner'] == 'FootyLevels' and health['runtime']['bodyModelLoaded']
        evidence['runtime'] = health['runtime']
        with args.fixture.open('rb') as source:
            project = call('POST', '/api/projects', 201,
                           files={'file': ('FP52-owned-smoke.mp4', source, 'video/mp4')}).json()
        project_id = project['id']
        evidence['projectId'] = project_id
        (args.output / 'evidence.json').write_text(json.dumps(evidence, indent=2))
        frame = project['frames'][0]
        call('PATCH', f'/api/projects/{project_id}/frames/{frame["id"]}',
             json={'bbox': [.055, .21, .64, .79]})
        timings = []
        for _ in range(2):
            start = time.monotonic()
            call('POST', f'/api/projects/{project_id}/generate', 202,
                 json={'provider': 'local', 'frameIds': [frame['id']]})
            project = wait('job')
            assert project['job']['status'] == 'complete'
            timings.append(round(time.monotonic() - start, 2))
            frame = next(f for f in project['frames'] if f['id'] == frame['id'])
            model, metadata = args.output / 'body.glb', args.output / 'body.json'
            model.write_bytes(call('GET', frame['meshUrl']).content)
            metadata.write_bytes(call('GET', frame['metadataUrl']).content)
            evidence['mesh'] = verify_glb(model, metadata)
        evidence['keyframeWallSeconds'] = timings
        print('Two fresh keyframe outputs independently decoded', flush=True)
        start = time.monotonic()
        call('POST', f'/api/projects/{project_id}/motion', 202)
        project = wait('motion')
        assert project['motion']['status'] in ('complete', 'partial')
        manifest = call('GET', project['motion']['sequenceUrl']).json()
        motion_dir = args.output / 'motion'
        motion_dir.mkdir(exist_ok=True)
        (motion_dir / 'sequence.json').write_text(json.dumps(manifest))
        names = [manifest['topologyFile']] + [c['file'] for c in manifest['chunks']]
        for name in names:
            assert Path(name).name == name
            (motion_dir / name).write_bytes(call('GET', f'/assets-data/{project_id}/motion/{name}').content)
        evidence['motion'] = verify_motion(motion_dir / 'sequence.json')
        evidence['motion'].update(status=project['motion']['status'], reason=project['motion']['error'],
            ballReady=project['motion']['ballReady'], groundAligned=project['motion']['groundAligned'],
            wallSeconds=round(time.monotonic() - start, 2))
        until = time.monotonic() + 60
        while time.monotonic() < until:
            health = call('GET', '/api/health').json()
            if health['runtime']['bodyModelLoaded']:
                break
            time.sleep(2)
        assert health['runtime']['bodyModelLoaded'], 'Body warmup did not recover after motion'
        evidence['bodyRewarmedAfterMotion'] = True
        print(json.dumps(evidence), flush=True)
    finally:
        evidence['cleanup'] = 'pending'
        (args.output / 'evidence.json').write_text(json.dumps(evidence, indent=2))
        try:
            if project_id:
                project = call('GET', f'/api/projects/{project_id}').json()
                if any((project.get(kind) or {}).get('status') in ('queued', 'running') for kind in ['job', 'motion']):
                    evidence['cleanup'] = 'pending-active-job'
                    print(f'Exact test project needs cleanup after its job finishes: {project_id}', flush=True)
                else:
                    call('DELETE', f'/api/projects/{project_id}', 204)
                    call('GET', f'/api/projects/{project_id}', 404)
                    evidence['cleanup'] = 'deleted-and-404-verified'
                    print('Exact test project/artifacts removed; absence verified', flush=True)
        finally:
            (args.output / 'evidence.json').write_text(json.dumps(evidence, indent=2))
            session.close()


if __name__ == '__main__':
    main()
