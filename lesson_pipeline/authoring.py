"""Loads an authored lesson, in either of the two supported forms.

- ``lesson.json``: the format in SPEC.md, with ``{{...}}`` placeholders for measured numbers.
  This is what PLAYBOOK.md asks an author (person or model) to write.
- ``lesson.py``: a script defining ``build(analysis) -> dict`` that computes its numbers with
  ``Measures`` (and ``r`` for rounding), both provided. The first five hand-made lessons use it.
  Only run scripts you wrote or reviewed: they are ordinary Python.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import numpy as np

from .measures import Measures as _Measures
from .measures import PlaceholderError


class ScriptMeasures(_Measures):
    """The reading helpers lesson scripts call (seconds in, plain numbers out)."""

    def __init__(self, analysis, ball_foot='right'):
        super().__init__(analysis, ball_foot)
        self.fps = float(analysis['fps'])

    def planted(self, role, t):
        return bool(self.f[self.side(role) + 'Planted'][self.i(t)])

    def plant_run(self, role, t):
        try:
            return super().plant_run(role, t)
        except PlaceholderError:
            return None

    def touches(self, a, b, role=None):          # scripts take the list and use len()
        return self.touch_list(a, b, role)

    def gap(self, role, a, b):                    # metres, as the scripts expect
        return float(min(self.f['gapR' if self.side(role) == 'right' else 'gapL'][self._window(a, b)]))

    def weight_to_ball(self, a, b):
        w = np.array(self.f['weight'][self._window(a, b)])
        return float(np.mean(w if self.ball_foot == 'right' else 1 - w))

    def com_travel(self, a, b):
        return self.travel(a, b)

    def sole(self, role, t):
        return float(self.f[self.side(role) + 'Sole'][self.i(t)])

    def ball_speed(self, a, b):
        try:
            return super().ball_speed(a, b)
        except PlaceholderError:
            return None


def load_lesson(path: Path, analysis: dict) -> dict:
    path = Path(path)
    if path.suffix == '.json':
        return json.loads(path.read_text())
    if path.suffix == '.py':
        spec = importlib.util.spec_from_file_location(f'lesson_{path.stem}', path)
        module = importlib.util.module_from_spec(spec)
        module.Measures, module.r = ScriptMeasures, (lambda x, n=0: round(x) if n == 0 else round(x, n))
        spec.loader.exec_module(module)
        lesson = module.build(analysis)
        lesson.setdefault('schemaVersion', 1)
        return lesson
    raise ValueError(f'{path}: a lesson is a .json or a .py file')
