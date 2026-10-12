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


def test_cleanup_entry_requires_finished_development_invocation_without_extra_owners():
    import copy
    spec = importlib.util.spec_from_file_location('ios_cleanup', Path(__file__).parents[1]/'scripts/cleanup_ios_playback_fixtures.py')
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    evidence = {'ticket': 'FP-40', 'environment': 'dev', 'runID': str(uuid.uuid4()),
                'users': ['fixtureUid'], 'projects': ['fixtureProject'], 'jobs': ['fixtureJob'],
                'phases': ['passed-account-deleted-awaiting-exact-cloud-cleanup']}
    assert helper.validated_evidence(evidence)['users'] == ['fixtureUid']
    for field, value in [('environment', 'demo'), ('ticket', 'FP-16'), ('users', ['a', 'b']),
                         ('projects', ['a', 'b']), ('jobs', ['../other']), ('phases', ['running'])]:
        changed = copy.deepcopy(evidence); changed[field] = value
        with pytest.raises(ValueError): helper.validated_evidence(changed)
