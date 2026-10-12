import importlib.util
from pathlib import Path
import uuid

import pytest

spec = importlib.util.spec_from_file_location('probe_cleanup', Path(__file__).parents[1]/'scripts/verify_media_worker_dgx.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_real_ios_fixture_guard_accepts_only_exact_development_marker_and_owner():
    run = str(uuid.uuid4())
    project = {'ownerId': 'firebaseQaUser', 'title': 'FP40 iOS '+run, 'source': 'gallery'}
    module.validate_ios_cleanup_scope('footylevels-dev-20261009', 'firebaseQaUser', 'project', run, [('project', project)])
    cases = [
        ('footylevels-demo-20261009', 'firebaseQaUser', 'project', run, [('project', project)]),
        ('footylevels-dev-20261009', 'someoneElse', 'project', run, [('project', project)]),
        ('footylevels-dev-20261009', '../user', 'project', run, [('project', project)]),
        ('footylevels-dev-20261009', 'firebaseQaUser', 'other', run, [('project', project)]),
        ('footylevels-dev-20261009', 'firebaseQaUser', 'project', 'not-a-uuid', [('project', project)]),
        ('footylevels-dev-20261009', 'firebaseQaUser', 'project', run, []),
        ('footylevels-dev-20261009', 'firebaseQaUser', 'project', run, [('project', project), ('other', project)]),
        ('footylevels-dev-20261009', 'firebaseQaUser', 'project', run, [('project', {**project, 'title': 'Real practice'})]),
        ('footylevels-dev-20261009', 'firebaseQaUser', 'project', run, [('project', {**project, 'source': 'capture'})]),
    ]
    for case in cases:
        with pytest.raises(ValueError):
            module.validate_ios_cleanup_scope(*case)
