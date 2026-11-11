"""Readings from one video's analysis, and the ``{{...}}`` placeholders lessons use to quote them.

Lesson text never types a measured number. It writes a placeholder such as ``{{drop 1.0 1.6}}``
and the build replaces it with the value computed from ``analysis.json``. Feet are named by job:
``ball`` is the foot that works the ball in this skill, ``stand`` the other one.

Placeholders (times in seconds; ``role`` is ``ball`` or ``stand``; ``[role]`` is optional):

  drop A B                 most the body centre drops below standing height, cm
  turn A B                 how far the hips turn from A to B, degrees
  touches A B [role]       number of ball contacts
  touchesText A B [role]   the same as words: "1 touch", "3 touches", "no touches"
  firstTouch A B [role]    time of the first contact, s      lastTouch A B [role]
  plantStart role T        when the foot planted at T came down, s
  plantEnd role T          when it lifted, s                 plantLength role T   seconds planted
  gap role A B             closest the foot comes to the ball's surface, cm (at least 1)
  travel A B               how far the body centre moves across the pitch, m
  ballSpeed A B            fastest the tracked ball moves, m/s
  weightPct role A B       average share of the weight toward that foot, %

In a numeric field (a phase ``key``, a challenge ``target``) a lone placeholder such as
``"{{firstTouch 1.6 2.2 ball}}"`` becomes a number.
"""
from __future__ import annotations

import re

import numpy as np

PLACEHOLDER = re.compile(r'\{\{\s*([A-Za-z]+)((?:\s+[^\s}]+)*)\s*\}\}')


class PlaceholderError(ValueError):
    pass


class Measures:
    def __init__(self, analysis: dict, ball_foot: str = 'right'):
        if ball_foot not in ('left', 'right'):
            raise PlaceholderError(f'ballFoot must be "left" or "right", not {ball_foot!r}')
        self.a = analysis
        self.f = analysis['frames']
        self.fps = float(analysis['fps'])
        self.n = int(analysis['frameCount'])
        self.ball_foot = ball_foot

    # --- basics -------------------------------------------------------------------------------
    def i(self, t: float) -> int:
        return int(np.clip(round(float(t) * self.fps), 0, self.n - 1))

    def t(self, i: int) -> float:
        return i / self.fps

    def side(self, role: str) -> str:
        if role not in ('ball', 'stand'):
            raise PlaceholderError(f'foot role must be "ball" or "stand", not {role!r}')
        return self.ball_foot if role == 'ball' else ('left' if self.ball_foot == 'right' else 'right')

    def _window(self, a: float, b: float) -> slice:
        if b < a:
            raise PlaceholderError(f'time window {a}–{b} runs backwards')
        return slice(self.i(a), self.i(b) + 1)

    # --- readings -----------------------------------------------------------------------------
    def drop(self, a, b) -> float:
        return float(max(self.f['comDropCm'][self._window(a, b)]))

    def turn(self, a, b) -> float:
        y = self.f['yaw']
        return float(abs(y[self.i(b)] - y[self.i(a)]))

    def touch_list(self, a, b, role=None):
        side = self.side(role) if role else None
        return [e for e in self.a['events']['touches']
                if a <= self.t(e['frame']) <= b and (side is None or e['foot'] == side)]

    def touches(self, a, b, role=None) -> int:
        return len(self.touch_list(a, b, role))

    def first_touch(self, a, b, role=None) -> float:
        found = self.touch_list(a, b, role)
        if not found:
            raise PlaceholderError(f'no {role + " foot " if role else ""}touch between {a} and {b} s')
        return self.t(found[0]['frame'])

    def last_touch(self, a, b, role=None) -> float:
        found = self.touch_list(a, b, role)
        if not found:
            raise PlaceholderError(f'no {role + " foot " if role else ""}touch between {a} and {b} s')
        return self.t(found[-1]['frame'])

    def plant_run(self, role, t):
        series = self.f[self.side(role) + 'Planted']
        i = self.i(t)
        if not series[i]:
            raise PlaceholderError(f'the {role} foot is not planted at {t} s')
        a = b = i
        while a > 0 and series[a - 1]:
            a -= 1
        while b + 1 < len(series) and series[b + 1]:
            b += 1
        return self.t(a), self.t(b)

    def gap(self, role, a, b) -> float:
        g = self.f['gapR' if self.side(role) == 'right' else 'gapL'][self._window(a, b)]
        value = min(g)
        if value >= 9:
            raise PlaceholderError(f'the ball is not tracked between {a} and {b} s')
        return float(max(value, 0.0)) * 100

    def travel(self, a, b) -> float:
        c0, c1 = np.array(self.f['com'][self.i(a)]), np.array(self.f['com'][self.i(b)])
        return float(np.linalg.norm((c1 - c0)[[0, 2]]))

    def ball_speed(self, a, b) -> float:
        w = self._window(a, b)
        speeds = [v for v, p in zip(self.f['ballSpeed'][w], self.f['ball'][w]) if p]
        if not speeds:
            raise PlaceholderError(f'the ball is not tracked between {a} and {b} s')
        return float(max(speeds))

    def weight_pct(self, role, a, b) -> float:
        w = np.array(self.f['weight'][self._window(a, b)])
        toward_right = float(np.mean(w)) * 100
        return toward_right if self.side(role) == 'right' else 100 - toward_right

    # --- placeholders -------------------------------------------------------------------------
    def evaluate(self, name: str, args: list[str]):
        """Returns (number, text) for one placeholder."""
        def num(k):
            try:
                return float(args[k])
            except (IndexError, ValueError):
                raise PlaceholderError(f'{{{{{name} {" ".join(args)}}}}}: argument {k + 1} should be a number of seconds')

        def word(k, optional=False):
            if k >= len(args):
                if optional:
                    return None
                raise PlaceholderError(f'{{{{{name} {" ".join(args)}}}}}: missing foot role (ball or stand)')
            return args[k]

        s2 = lambda v: (v, f'{v:.2f}')
        table = {
            'drop': lambda: (lambda v: (v, f'{round(v)}'))(self.drop(num(0), num(1))),
            'turn': lambda: (lambda v: (v, f'{round(v)}'))(self.turn(num(0), num(1))),
            'touches': lambda: (lambda v: (v, f'{v}'))(self.touches(num(0), num(1), word(2, True))),
            'touchesText': lambda: (lambda v: (v, 'no touches' if v == 0 else f'{v} touch' + ('' if v == 1 else 'es')))(
                self.touches(num(0), num(1), word(2, True))),
            'firstTouch': lambda: s2(self.first_touch(num(0), num(1), word(2, True))),
            'lastTouch': lambda: s2(self.last_touch(num(0), num(1), word(2, True))),
            'plantStart': lambda: s2(self.plant_run(word(0), num(1))[0]),
            'plantEnd': lambda: s2(self.plant_run(word(0), num(1))[1]),
            'plantLength': lambda: (lambda r: (r[1] - r[0], f'{r[1] - r[0]:.1f}'))(self.plant_run(word(0), num(1))),
            # The ball's position is estimated, so contact is never claimed more finely than 1 cm.
            'gap': lambda: (lambda v: (v, f'{max(1, round(v))}'))(self.gap(word(0), num(1), num(2))),
            'travel': lambda: (lambda v: (v, f'{v:.1f}'))(self.travel(num(0), num(1))),
            'ballSpeed': lambda: (lambda v: (v, f'{v:.1f}'))(self.ball_speed(num(0), num(1))),
            'weightPct': lambda: (lambda v: (v, f'{round(v)}'))(self.weight_pct(word(0), num(1), num(2))),
        }
        if name not in table:
            raise PlaceholderError(f'unknown placeholder {{{{{name}}}}}; see measures.py for the list')
        return table[name]()

    def fill_text(self, text: str) -> str:
        return PLACEHOLDER.sub(lambda m: self.evaluate(m.group(1), m.group(2).split())[1], text)

    def fill_number(self, value):
        """A number stays a number; a lone placeholder string becomes one."""
        if isinstance(value, (int, float)):
            return value
        m = PLACEHOLDER.fullmatch(str(value).strip())
        if not m:
            raise PlaceholderError(f'expected a number or a single placeholder, got {value!r}')
        return round(self.evaluate(m.group(1), m.group(2).split())[0], 2)


NUMBER_FIELDS = ('key', 'target', 'from', 't', 'start', 'end')
NUMBER_LISTS = ('model', 'play', 'range', 'watch')


def resolve_lesson(lesson: dict, analysis: dict) -> dict:
    """A copy of the lesson with every placeholder replaced by its measured value."""
    import copy
    out = copy.deepcopy(lesson)
    for skill in out['skills']:
        m = Measures(analysis, skill.get('ballFoot', 'right'))

        def walk(node, key=None):
            if isinstance(node, dict):
                for k, v in list(node.items()):
                    if k in NUMBER_FIELDS and isinstance(v, str):
                        node[k] = m.fill_number(v)
                    elif isinstance(v, str):
                        node[k] = m.fill_text(v)
                    else:
                        walk(v, k)
            elif isinstance(node, list):
                for idx, v in enumerate(node):
                    if isinstance(v, str):
                        node[idx] = m.fill_number(v) if key in NUMBER_LISTS else m.fill_text(v)
                    else:
                        walk(v, key)
        walk(skill)
    return out
