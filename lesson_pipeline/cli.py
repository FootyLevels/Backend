"""Command line for the lesson pipeline. See PLAYBOOK.md for the full procedure.

  run        VIDEO --work DIR --host user@gpu [--port N] [--server-root DIR]
                                                video → 3D → analysis → evidence → draft
  analyze    --work DIR                         (re)run the analysis from downloaded motion
  evidence   --work DIR                         contact sheets + evidence.md
  draft      --work DIR [--title T]             lesson.draft.json from the measurements
  generate   [VIDEO] --work DIR [--id ID] [--mode baseline|grounded]
                                                video → lesson via Gemini API (grounded needs analysis/)
  check      --work DIR [--lesson F]          mechanical schema/timestamp validation
  build      --work DIR [--lesson F] [--web OUT] [--app OUT]
  integrate  --work DIR --frontend REPO         build the app bundle and copy it into the app
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
from pathlib import Path

from . import analysis, build, cuts, draft, env, evidence, generation, grounded, remote
from .authoring import load_lesson
from .prompting import VIDEO_MIMES, PromptError, video_metadata, video_path
from .validate import validate, validate_structure


def _work(args) -> Path:
    work = Path(args.work).expanduser().resolve()
    if not work.is_dir():
        sys.exit(f'No work folder at {work}. Start with `generate VIDEO --work DIR`.')
    return work


def _cuts(work: Path) -> list[float]:
    path = work / 'cuts.json'
    return json.loads(path.read_text()) if path.exists() else []


def _analyze(work: Path) -> None:
    rec = json.loads((work / 'reconstruction.json').read_text())
    parts = [(work / p['dir'], p['start']) for p in rec.get('parts', [])]
    summary = analysis.analyze(work / 'project', work / 'analysis', _cuts(work), parts)
    print(f"analysis: {summary['frames']} frames, {summary['valid']} with a body, ball in {summary['ballFrames']}, "
          f"{summary['touches']} touches, {summary['plants']} plants, {summary['motionMB']} MB of 3D")


def cmd_run(args) -> None:
    video = Path(args.video).expanduser().resolve()
    if not video.is_file():
        sys.exit(f'No video at {video}')
    work = Path(args.work).expanduser().resolve()
    work.mkdir(parents=True, exist_ok=True)
    shutil.copy(video, work / 'source.mp4')
    found = cuts.detect_cuts(work / 'source.mp4')
    (work / 'cuts.json').write_text(json.dumps(found))
    print(f'cuts: {found or "none"}')
    host = args.host or os.environ.get('LESSON_GPU_HOST')
    if not host:
        sys.exit('Give --host or set LESSON_GPU_HOST in ~/.config/footylevels/lesson.env')
    server = remote.Server(host, args.port, args.server_root, data_dir=args.server_data)
    rec = remote.reconstruct(work / 'source.mp4', work, server, found, reuse=args.reuse)
    print(f"3D: project {rec['projectId']} ({rec['status']}), {len(rec['parts'])} segment(s) re-run")
    _analyze(work)
    print(f"evidence: {evidence.build_evidence(work, work / 'upload.mp4', found)}")
    out = draft.build_draft(work, args.id or _slug(work.name), args.title)
    print(f'draft: {out}\nNext: generate --work {work}, then `check --strict`, `build`, `integrate`.')


def _slug(name: str) -> str:
    return re.sub(r'[^a-z0-9-]+', '-', name.lower()).strip('-')[:40] or 'lesson'


def _lesson(work: Path, args) -> Path:
    if getattr(args, 'lesson', None):
        path = Path(args.lesson).expanduser()
    else:
        path = work / 'lesson.json' if (work / 'lesson.json').exists() else work / 'lesson.py'
    if not path.is_file():
        sys.exit(f'No lesson at {path}. Run `generate --work {work}` first.')
    return path.resolve()


def cmd_check(args) -> int:
    work = _work(args)
    path = _lesson(work, args)
    if args.measured:
        analysis = json.loads((work / 'analysis' / 'analysis.json').read_text())
        lesson = load_lesson(path, analysis)
        errors, warnings = validate(lesson, analysis, strict=args.strict, hand_typed=path.suffix == '.json')
    else:
        if path.suffix != '.json':
            sys.exit('Use --measured for trusted legacy lesson scripts.')
        lesson = json.loads(path.read_text())
        errors, warnings = validate_structure(lesson, video_metadata(work)['duration'])
    for e in errors:
        print(f'ERROR   {e}')
    for w in warnings:
        print(f'warning {w}')
    print(f'{len(errors)} error(s), {len(warnings)} warning(s)')
    return 1 if errors else 0


def _import_video(video, work):
    source, work = Path(video).expanduser().resolve(), Path(work).expanduser().resolve()
    if not source.is_file() or source.suffix.lower() not in VIDEO_MIMES:
        raise PromptError('Supply a supported local video file.')
    work.mkdir(parents=True, exist_ok=True)
    try:
        existing = video_path(work)
    except PromptError:
        existing = None
    if existing:
        with source.open('rb') as first, existing.open('rb') as second:
            same = hashlib.file_digest(first, 'sha256').digest() == hashlib.file_digest(second, 'sha256').digest()
        if not same:
            raise PromptError('This work folder has a different video; use a new work folder.')
        return existing
    destination = work / ('source' + source.suffix.lower())
    shutil.copyfile(source, destination)
    return destination


def cmd_generate(args) -> None:
    if args.video:
        _import_video(args.video, args.work)
    work = _work(args)
    cache = Path(args.cache).expanduser() if args.cache else work / 'generation' / 'cache'
    if args.mode == 'grounded':
        path = grounded.author(work, args.id or _slug(work.name), model=args.model, max_attempts=args.attempts,
                               cache=cache)
        print(f'lesson: {path}\nNext: check --work {work} --measured --strict, then build --app.')
        return
    extra = {'cache': cache} if args.cache else {}
    path = generation.generate(work, args.id or _slug(work.name), model=args.model, max_attempts=args.attempts,
                               **extra)
    print(f'lesson: {path}\nNext: check --work {work}, then review the content. 3D packaging needs its separate assets.')


def main(argv=None) -> int:
    try:
        env.load()
    except env.EnvError as error:
        sys.exit(str(error))
    p = argparse.ArgumentParser(prog='lesson_pipeline', description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest='cmd', required=True)
    r = sub.add_parser('run', help='video → 3D → analysis → evidence → draft')
    r.add_argument('video')
    r.add_argument('--work', required=True, help='folder for this video’s files')
    r.add_argument('--host', help='GPU server as user@host or an SSH alias (key-based SSH); '
                                  'default: LESSON_GPU_HOST from the private env file')
    r.add_argument('--port', type=int, default=8012, help='backend port on the GPU server (loopback)')
    r.add_argument('--server-root', default='skill-lab',
                   help="the backend's folder on the GPU server, absolute or relative to your home (default: skill-lab)")
    r.add_argument('--server-data', help='remote SKILL_LAB_DATA path (default: <server-root>/data)')
    r.add_argument('--reuse', help='an existing backend project id to reuse instead of uploading')
    r.add_argument('--id', help='lesson id (slug); defaults to the work folder name')
    r.add_argument('--title', help='working title for the draft')
    for name in ('analyze', 'evidence', 'draft', 'generate', 'check', 'build', 'integrate'):
        s = sub.add_parser(name)
        s.add_argument('--work', required=True)
        if name == 'generate':
            s.add_argument('video', nargs='?', help='source video to copy without transcoding or audio removal')
            s.add_argument('--id', help='lesson id (slug); defaults to the work folder name')
            s.add_argument('--model', default=os.environ.get('GEMINI_MODEL') or generation.DEFAULT_MODEL)
            s.add_argument('--attempts', type=int, choices=range(1, 6), default=3,
                           help='maximum billable content/repair requests per step (default: 3)')
            s.add_argument('--mode', choices=('baseline', 'grounded'), default='baseline',
                           help='baseline: one call over the whole video; grounded: screen, look, teach, '
                                'verify and measured checks (needs analysis/)')
            s.add_argument('--cache', help='authoring cache folder (grounded default: WORK/generation/cache)')
        if name == 'draft':
            s.add_argument('--id')
            s.add_argument('--title')
        if name in ('check', 'build', 'integrate'):
            s.add_argument('--lesson', help='lesson file (default: WORK/lesson.json)')
        if name == 'check':
            s.add_argument('--measured', action='store_true', help='check legacy lessons using analysis/measurement placeholders')
            s.add_argument('--strict', action='store_true', help='apply legacy measured-lesson completion rules with --measured')
        if name == 'build':
            s.add_argument('--web', help='folder to write the web lesson into')
            s.add_argument('--app', help='folder to write the app bundle into')
        if name == 'integrate':
            s.add_argument('--frontend', required=True, help='path to the Frontend repository')
    args = p.parse_args(argv)

    if args.cmd == 'run':
        cmd_run(args)
    elif args.cmd == 'analyze':
        _analyze(_work(args))
    elif args.cmd == 'evidence':
        work = _work(args)
        print(evidence.build_evidence(work, work / 'upload.mp4', _cuts(work)))
    elif args.cmd == 'draft':
        work = _work(args)
        print(draft.build_draft(work, args.id or _slug(work.name), args.title))
    elif args.cmd == 'generate':
        try:
            cmd_generate(args)
        except (generation.GenerationError, OSError, ValueError, generation.PromptError) as e:
            sys.exit(f'generate: {e}')
    elif args.cmd == 'check':
        return cmd_check(args)
    elif args.cmd == 'build':
        work = _work(args)
        lesson = _lesson(work, args)
        if not args.web and not args.app:
            sys.exit('Give --web and/or --app')
        if args.web:
            print(f'web: {build.build_web(work, lesson, Path(args.web).expanduser())}')
        if args.app:
            print(f'app: {build.build_app(work, lesson, Path(args.app).expanduser())}')
    elif args.cmd == 'integrate':
        work = _work(args)
        bundle = build.build_app(work, _lesson(work, args), work / 'build' / 'app')
        print(f'integrated: {build.integrate(bundle, Path(args.frontend).expanduser().resolve())}')
    return 0
