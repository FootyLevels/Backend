"""Turns a checked lesson into the two deliverables, and drops the app one into the iOS project.

Web package (a folder you can serve or publish):
  index.html, data/{lesson.json, analysis.json, mesh.json, motion.json, motion-NNN.gz.txt, video.mp4}
  Motion chunks ship as base64 text because some static hosts refuse binary files.

App bundle ``Lesson-<id>.bundle`` (copied whole into the app by Xcode's synchronized folders):
  lesson.json, analysis.json, mesh.json, video.mp4,
  motion/{sequence.json, faces.bin, body-NNNNNN.bin.gz}   (the backend's MotionSequence format)
"""
from __future__ import annotations

import base64
import html
import json
import shutil
from pathlib import Path

from . import app_contract
from .authoring import load_lesson
from .measures import PLACEHOLDER, resolve_lesson
from .validate import SLUG, validate, validate_structure

WEB_TEMPLATE = Path(__file__).resolve().parent / 'web' / 'index.html'


class BuildError(RuntimeError):
    pass


def _resolved(work: Path, lesson_path: Path, strict: bool) -> tuple[dict, dict]:
    analysis = json.loads((work / 'analysis' / 'analysis.json').read_text())
    lesson = load_lesson(lesson_path, analysis)
    lesson.setdefault('folder', lesson.get('id'))
    if any(not isinstance(lesson.get(k), str) or not SLUG.fullmatch(lesson[k]) for k in ('id', 'folder')):
        raise BuildError('Lesson id and folder must be lowercase slugs, not file paths.')
    measured = bool(PLACEHOLDER.search(json.dumps(lesson))) or Path(lesson_path).suffix == '.py'
    if measured:
        errors, _ = validate(lesson, analysis, strict=strict, hand_typed=Path(lesson_path).suffix == '.json')
    else:
        errors, _ = validate_structure(lesson, analysis['duration'])
    if errors:
        raise BuildError('the lesson has errors; run `check` first:\n  ' + '\n  '.join(errors))
    out = resolve_lesson(lesson, analysis) if measured else lesson.copy()
    for k in ('draft', 'reviewNotes'):
        out.pop(k, None)
    return out, analysis


def _video(work: Path) -> Path:
    for name in ('upload.mp4', 'source.mp4'):
        if (work / name).exists():
            return work / name
    raise BuildError(f'3D bundle export needs matching MP4 playback as upload.mp4 or source.mp4 in {work}.')


def build_web(work: Path, lesson_path: Path, out_root: Path, strict=False) -> Path:
    lesson, _ = _resolved(work, lesson_path, strict)
    folder = out_root / lesson['folder']
    data = folder / 'data'
    if data.exists():
        shutil.rmtree(data)
    data.mkdir(parents=True)
    page = WEB_TEMPLATE.read_text().replace('<title>Skill Lesson</title>', f"<title>{html.escape(lesson['title'])}</title>", 1)
    (folder / 'index.html').write_text(page)
    (data / 'lesson.json').write_text(json.dumps(lesson, ensure_ascii=False, indent=1))
    analysis_dir = work / 'analysis'
    shutil.copy(analysis_dir / 'analysis.json', data / 'analysis.json')
    shutil.copy(analysis_dir / 'mesh.json', data / 'mesh.json')
    seq = json.loads((analysis_dir / 'motion' / 'sequence.json').read_text())
    chunks = []
    for c in seq['chunks']:
        name = f"motion-{c['start']:03d}.gz.txt"
        (data / name).write_text(base64.b64encode((analysis_dir / 'motion' / c['file']).read_bytes()).decode())
        chunks.append({'start': c['start'], 'count': c['count'], 'file': name, 'bytes': c['bytes']})
    (data / 'motion.json').write_text(json.dumps({'vertexCount': seq['vertexCount'], 'frameCount': seq['frameCount'],
                                                  'fps': seq['fps'], 'min': seq['min'], 'span': seq['span'], 'chunks': chunks}))
    shutil.copy(_video(work), data / 'video.mp4')
    return folder


def build_app(work: Path, lesson_path: Path, out_root: Path, strict=True) -> Path:
    lesson, _ = _resolved(work, lesson_path, strict)
    bundle = out_root / f"Lesson-{lesson['id']}.bundle"
    if bundle.exists():
        shutil.rmtree(bundle)
    (bundle / 'motion').mkdir(parents=True)
    (bundle / 'lesson.json').write_text(json.dumps(lesson, ensure_ascii=False, separators=(',', ':')))
    analysis_dir = work / 'analysis'
    shutil.copy(analysis_dir / 'analysis.json', bundle / 'analysis.json')
    mesh = json.loads((analysis_dir / 'mesh.json').read_text())
    (bundle / 'mesh.json').write_text(json.dumps({'vertexCount': mesh['vertexCount'], 'footSets': mesh['footSets']},
                                                 separators=(',', ':')))
    for f in (analysis_dir / 'motion').iterdir():
        shutil.copy(f, bundle / 'motion' / f.name)
    shutil.copy(_video(work), bundle / 'video.mp4')
    try:
        app_contract.check_bundle(bundle)
    except app_contract.ContractError as error:
        shutil.rmtree(bundle)
        raise BuildError(f'the app could not open this bundle: {error}') from None
    return bundle


def integrate(bundle: Path, frontend: Path) -> Path:
    """Copies an app bundle into the iOS project, replacing an older copy of the same lesson."""
    app_folder = frontend / 'FootyLevels-Frontend'
    if not app_folder.is_dir():
        raise BuildError(f'{frontend} is not the Frontend repository (no FootyLevels-Frontend folder)')
    if not bundle.name.startswith('Lesson-') or bundle.suffix != '.bundle':
        raise BuildError(f'{bundle} is not a lesson bundle')
    dest = app_folder / 'SampleData' / 'Lessons' / bundle.name
    if dest.exists():
        shutil.rmtree(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(bundle, dest)
    return dest
