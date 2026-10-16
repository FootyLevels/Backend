"""Create disposable real accounts locally, pass tokens to DGX only over SSH stdin."""
import json
import os
from pathlib import Path
import plistlib
import secrets
import subprocess
import sys
import uuid

from firebase_admin import auth, credentials, delete_app, initialize_app
import google.auth
import requests


def main():
    environment = sys.argv[1]
    if environment not in ('dev', 'demo'):
        raise ValueError('Select dev or demo')
    project = f'footylevels-{environment}-20261009'
    frontend = Path(__file__).resolve().parents[2] / 'Frontend/FootyLevels-Frontend'
    public = plistlib.loads((frontend / f'GoogleService-Info-{environment.title()}.plist').read_bytes())
    if public['PROJECT_ID'] != project:
        raise ValueError('Public Firebase config environment mismatch')
    saved = os.environ.pop('GOOGLE_APPLICATION_CREDENTIALS', None)
    try:
        adc, _ = google.auth.default(scopes=['https://www.googleapis.com/auth/cloud-platform'], quota_project_id=project)
    finally:
        if saved: os.environ['GOOGLE_APPLICATION_CREDENTIALS'] = saved
    class AdminADC(credentials.Base):
        def get_credential(self): return adc
    admin = initialize_app(AdminADC(), {'projectId': project}, name='fp17-admin-'+uuid.uuid4().hex)
    owners, tokens, emails = [], [], []
    try:
        try: auth.get_user('fp17-nonexistent-'+uuid.uuid4().hex, app=admin)
        except auth.UserNotFoundError: pass
        for _ in range(2):
            email = 'fp17-'+uuid.uuid4().hex+'@example.invalid'
            r = requests.post('https://identitytoolkit.googleapis.com/v1/accounts:signUp', params={'key': public['API_KEY']},
                json={'email': email, 'password': secrets.token_urlsafe(32), 'returnSecureToken': True}, timeout=30)
            if r.status_code != 200:
                raise RuntimeError(f'Temporary signup HTTP {r.status_code}')
            body = r.json(); owners.append(body['localId']); tokens.append(body['idToken']); emails.append(email)
        remote = "cd ~/.local/share/footylevels/cloud-api/current && PYTHONPATH=src:. bash ~/.local/share/footylevels/fp50-dns/with_network_dns.sh ~/.local/share/footylevels/cloud-api/venv/bin/python scripts/verify_private_assets_dgx.py ~/.local/share/footylevels/cloud-api/fixture.mp4"
        if len(sys.argv) == 3:
            if sys.argv[2] != '--http' or environment != 'dev':
                raise ValueError('HTTP probe is restricted to the private Development API')
            remote = remote.replace('PYTHONPATH=src:. bash', 'FOOTYLEVELS_ASSET_PROBE_HTTP=http://127.0.0.1:8011 PYTHONPATH=src:. bash')
        # Secrets never appear in process arguments, files or diagnostics.
        result = subprocess.run(['/usr/bin/ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=10', 'bajajkun@142.55.34.202', remote],
            input=json.dumps({'environment': environment, 'owners': owners, 'tokens': tokens, 'emails': emails}).encode(),
            capture_output=True, timeout=540)
        for line in result.stdout.decode().splitlines():
            try:
                parsed = json.loads(line)
                if parsed.get('environment') == environment and isinstance(parsed.get('tests'), list):
                    print(json.dumps(parsed), flush=True)
            except (ValueError, TypeError): pass
        if result.returncode:
            # Remote output is deliberately restricted to our safe diagnostic prefix.
            for line in result.stderr.decode().splitlines():
                if line.startswith('Private storage probe failed ('): print(line, file=sys.stderr)
            raise RuntimeError('Remote private storage verification failed')
    finally:
        failures = []
        for uid in owners:
            try:
                auth.delete_user(uid, app=admin)
                try: auth.get_user(uid, app=admin)
                except auth.UserNotFoundError: continue
                failures.append('account still present')
            except Exception: failures.append('account deletion')
        delete_app(admin)
        if failures: raise RuntimeError('Temporary Firebase account cleanup requires attention')
        print(environment+': temporary Firebase accounts deleted and absence verified', flush=True)


if __name__ == '__main__':
    try: main()
    except Exception as error:
        print('Private storage verification failed ('+type(error).__name__+'); no credential diagnostics displayed', file=sys.stderr)
        sys.exit(1)
