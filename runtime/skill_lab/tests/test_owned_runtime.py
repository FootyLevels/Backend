import json

import pytest
import torch
from fastapi.testclient import TestClient

from backend import motion, store
from backend.providers.local import LocalProvider
from backend.app import app


def test_delete_only_idle_project_and_preserve_other_data(tmp_path, monkeypatch):
    monkeypatch.setattr(store, 'DATA', tmp_path)
    for identifier in ['delete-fixture', 'keep-fixture']:
        store.project_dir(identifier).mkdir()
        store.save({'id': identifier, 'createdAt': '2026', 'frames': [],
                    'job': None, 'motion': {'status': 'running'}})
    # Avoid the startup recovery pass: this exercises an actively running job.
    client = TestClient(app)
    assert client.delete('/api/projects/delete-fixture').status_code == 409
    project = store.read('delete-fixture')
    project['motion']['status'] = 'partial'
    store.save(project)
    assert client.delete('/api/projects/delete-fixture').status_code == 204
    assert client.get('/api/projects/delete-fixture').status_code == 404
    assert store.read('keep-fixture')['id'] == 'keep-fixture'


@pytest.mark.parametrize('free_gib,expected', [(20, 'partial'), (40, 'complete')])
def test_optional_object_capacity_preserves_body_and_rewarms(tmp_path, monkeypatch, free_gib, expected):
    monkeypatch.setattr(store, 'DATA', tmp_path)
    directory = store.project_dir('owned-runtime-test')
    directory.mkdir()
    store.save({'id': directory.name, 'motion': {'status': 'queued'}, 'frames': []})
    monkeypatch.setattr(torch.cuda, 'mem_get_info', lambda: (free_gib * 1024 ** 3, 80 * 1024 ** 3))
    monkeypatch.setenv('FOOTYLEVELS_OBJECTS_MIN_FREE_GIB', '32')
    calls = []
    monkeypatch.setattr(LocalProvider, 'release', lambda: calls.append('release'))
    monkeypatch.setattr(LocalProvider, 'load', lambda self: calls.append('warm'))

    def stage(command, **kwargs):
        script = command[2].split('/')[-1]
        calls.append(script)
        root = directory / 'motion'
        if script == 'reconstruct_motion.py':
            (root / 'sequence.json').write_text(json.dumps({'validBodyFrames': 3, 'validBallFrames': 3,
                'fps': 3, 'ball': {'file': None}, 'failures': []}))
        elif script == 'reconstruct_ball.py':
            p = root / 'sequence.json'
            data = json.loads(p.read_text())
            data['ball']['file'] = 'ball.glb'
            p.write_text(json.dumps(data))

    monkeypatch.setattr(motion.subprocess, 'run', stage)
    motion.run(directory.name)
    result = store.read(directory.name)['motion']
    assert calls[0] == 'release' and calls[-1] == 'warm'
    assert result['status'] == expected and result['bodyFrames'] == 3
    assert ('reconstruct_ball.py' in calls) == (free_gib >= 32)
    assert result['ballReady'] == (free_gib >= 32)
    if expected == 'partial':
        assert 'insufficient free GPU memory' in result['error']
