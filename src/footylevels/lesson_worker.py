"""Serial, fenced execution of one ``buildLesson`` attempt.

Stages: downloadingInput → screeningVideo → generatingBodies → writingLesson → checkingLesson →
publishingAssets. The Gemini key and all cloud rights stay in this parent process; the GPU step
runs as a credential-free child (``infra/dgx/lesson_adapter.py``) with an empty environment.
Accepted model responses are cached per job outside the attempt folder, so a retried attempt does
not pay again for steps that already passed.
"""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import time

from .assets import StorageUnavailable
from .model_requests import lesson_input_hash
from .records import AssetRef, Engine, LessonAuthoring, LessonRecord, LessonSkill
from .reconstruction_worker import stop_child
from .repositories import RecordConflict, RecordNotFound
from .uploads import StorageQuotaExceeded


class AdapterFailed(Exception):
    pass


class LessonFailed(Exception):
    pass


CONTENT_TYPES = {'.json': 'application/json', '.mp4': 'video/mp4', '.gz': 'application/gzip',
                 '.bin': 'application/octet-stream'}


def lesson_slug(job_id: str) -> str:
    """The bundle id inside lesson.json: short, stable, not guessable from the project."""
    return 'v' + job_id[:24]


def bundle_assets(claim, bundle: Path) -> list[tuple[AssetRef, Path]]:
    prefix = f"users/{claim.owner_id}/projects/{claim.project_id}/lesson/{claim.id}/{claim.attempt_count}/"
    files = sorted(p for p in bundle.rglob('*') if p.is_file())
    if not 4 <= len(files) <= 64:
        raise LessonFailed()
    out = []
    for path in files:
        relative = path.relative_to(bundle)
        if (path.is_symlink() or len(relative.parts) > 2 or path.suffix not in CONTENT_TYPES
                or (len(relative.parts) == 2 and relative.parts[0] != 'motion')):
            raise LessonFailed()
        size = path.stat().st_size
        if not 0 < size <= 52428800:
            raise LessonFailed()
        with path.open('rb') as stream:
            digest = hashlib.file_digest(stream, 'sha256').hexdigest()
        out.append((AssetRef(bucket='private-media', object_key=prefix + relative.as_posix(), sha256=digest,
                             byte_size=size, content_type=CONTENT_TYPES[path.suffix]), path))
    return out


def _child(adapter_command, directory, check, lost, stop, timeout, ffmpeg_dir):
    started = time.monotonic()
    path = os.pathsep.join(p for p in (ffmpeg_dir, os.defpath) if p)
    process = subprocess.Popen([sys.executable, str(Path(__file__).with_name('reconstruction_limits.py')),
                               *adapter_command, str(directory)], cwd=directory,
                               env={'PATH': path, 'LANG': 'C.UTF-8', 'HOME': str(directory), 'TMPDIR': str(directory)},
                               stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                               start_new_session=True)
    try:
        while process.poll() is None:
            if lost.is_set() or (stop and stop.is_set()):
                check()
            if time.monotonic() - started >= timeout:
                raise AdapterFailed()
            time.sleep(0.1)
    finally:
        stop_child(process)
    if process.returncode != 0:
        raise AdapterFailed()


def run_job(jobs, storage, claim, adapter_command, staging_root, *, model, cache_root, client=None,
            heartbeat_seconds=30, command_timeout=3500, stop=None, log=lambda *_: None):
    """One attempt. Returns an outcome word for the runner's log; never raises job errors."""
    from lesson_pipeline import build, grounded, passes
    from lesson_pipeline.generation import GenerationError, Session, guarded
    from lesson_pipeline.prompting import PromptError, video_metadata
    if not adapter_command or heartbeat_seconds <= 0 or not 0 < command_timeout <= 3500:
        raise ValueError('Expected a bounded adapter command')
    finished, lost, failure = threading.Event(), threading.Event(), []

    def heartbeat():
        while not finished.wait(heartbeat_seconds):
            try:
                jobs.checkpoint(claim)
            except Exception as error:
                failure.append(error)
                lost.set()
                return

    def check(stage=None):
        if stop and stop.is_set():
            raise RecordConflict('Worker is stopping')
        if lost.is_set():
            raise failure[0]
        return jobs.checkpoint(claim, stage)

    def fail(code, retry):
        try:
            jobs.fail(claim, code, retry=retry)
        except (RecordConflict, RecordNotFound, StorageUnavailable):
            pass  # A lost connection leaves the durable lease for restart.

    monitor = threading.Thread(target=heartbeat, daemon=True)
    cache = Path(cache_root) / claim.id
    try:
        if claim.kind != 'buildLesson' or lesson_input_hash(claim.source_asset, model) != claim.input_hash:
            raise RecordConflict('Lesson job was created for a different authoring model')
        check('downloadingInput')
        monitor.start()
        with tempfile.TemporaryDirectory(prefix='lesson-', dir=staging_root) as temporary:
            directory = Path(temporary)
            storage.download_file(claim.source_asset, directory / 'input.mp4')
            payload = claim.model_dump(mode='json', by_alias=True, exclude={'lease_token', 'worker_id', 'lease_expires_at'})
            for name, body in (('job.json', payload), ('lesson.json', {'model': model})):
                (directory / name).write_text(json.dumps(body))
                (directory / name).chmod(0o600)

            check('screeningVideo')
            screen_folder = directory / 'screen'
            screen_folder.mkdir(mode=0o700)
            shutil.copyfile(directory / 'input.mp4', screen_folder / 'upload.mp4')
            duration = video_metadata(screen_folder)['duration']
            with Session(screen_folder, model=model, client=client, cache=cache, context='grounded') as session:
                screened = guarded(lambda: passes.screen(session, screen_folder / 'upload.mp4', duration))
            log(f"screen: {len(screened['moves'])} move(s)")

            check('generatingBodies')
            ffmpeg = shutil.which('ffmpeg')
            _child(adapter_command, directory, check, lost, stop, command_timeout,
                   str(Path(ffmpeg).parent) if ffmpeg else '')
            result_path = directory / 'result.json'
            work = directory / 'work'
            if (result_path.is_symlink() or not result_path.is_file() or result_path.stat().st_size > 65536
                    or work.is_symlink() or not (work / 'analysis' / 'analysis.json').is_file()):
                raise AdapterFailed()
            try:
                engine = Engine.model_validate(json.loads(result_path.read_text())['engine'])
            except (ValueError, KeyError, TypeError):
                raise AdapterFailed() from None

            check('writingLesson')
            slug = lesson_slug(claim.id)
            grounded.author(work, slug, model=model, client=client, cache=cache, screened=screened, log=log)
            report = json.loads((work / 'authoring.json').read_text())

            check('checkingLesson')
            try:
                bundle = build.build_app(work, work / 'lesson.json', directory / 'bundle', strict=True)
            except build.BuildError:
                raise LessonFailed() from None
            document = json.loads((bundle / 'lesson.json').read_text())
            assets = bundle_assets(claim, bundle)
            lesson = LessonRecord(id=claim.id, owner_id=claim.owner_id, project_id=claim.project_id, job_id=claim.id,
                                  input_revision=claim.input_revision, source_asset=claim.source_asset,
                                  title=document['title'][:120],
                                  skills=[LessonSkill(id=s['id'], name=s['name'][:120]) for s in document['skills'][:8]],
                                  authoring=LessonAuthoring(model=model, mode='grounded', prompt_sha256=report['promptSha256'],
                                                            claims_removed=report['claimsRemoved'], calls=report['calls']),
                                  engine=engine, artifacts=[a for a, _ in assets])
            jobs.reserve_outputs(claim, lesson.artifacts)
            check('publishingAssets')
            for asset, path in assets:
                check()
                storage.put_file(asset, path)
            check()
            jobs.publish_lesson(claim, lesson)
            shutil.rmtree(cache, ignore_errors=True)
            return 'succeeded'
    except passes.Rejected:
        fail('lesson_rejected', False)
        return 'rejected'
    except (RecordConflict, RecordNotFound):
        return 'superseded'
    except GenerationError:
        fail('lesson_unavailable', True)
        return 'retrying'
    except PromptError:
        # The video and its measurements disagree; another attempt would see the same files.
        fail('lesson_unavailable', False)
        return 'lesson-failed'
    except (StorageUnavailable, StorageQuotaExceeded, OSError):
        fail('model_unavailable', True)
        return 'retrying'
    except AdapterFailed:
        fail('reconstruction_failed', True)
        return 'adapter-failed'
    except LessonFailed:
        fail('lesson_unavailable', True)
        return 'lesson-failed'
    finally:
        finished.set()
        if monitor.ident is not None:
            monitor.join(timeout=2)
