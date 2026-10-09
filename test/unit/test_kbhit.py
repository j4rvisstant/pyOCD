# pyOCD debugger
# Copyright (c) 2026 j4rvisstant
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import types
from typing import Iterable, List, Optional, Tuple, Union

import pytest

import pyocd.utility.kbhit as kbhit_module
from pyocd.utility.kbhit import KBHit

# A console key event. A str is a key with a character value (one UTF-16 code unit). A
# (prefix, scan_code) tuple is an extended key as the Windows console runtime delivers it.
KeyEvent = Union[str, Tuple[str, str]]

ESC = '\x1b'
NUL = '\x00'
A_UMLAUT = '\xe4'  # 'ä' (U+00E4): byte 0x84 under cp437
A_GRAVE = '\xe0'  # 'à' (U+00E0): the same code unit as the wide extended-key prefix; byte 0x85 under cp437

ARROW_UP = ('\xe0', 'H')
ARROW_DOWN = ('\xe0', 'P')
ARROW_LEFT = ('\xe0', 'K')
ARROW_RIGHT = ('\xe0', 'M')
F1 = ('\x00', ';')

# (id, event) rows for the extended keys of the behaviour table; the scan code must never surface.
EXTENDED_KEY_TABLE = [
    ("home", ('\xe0', 'G')),
    ("end", ('\xe0', 'O')),
    ("insert", ('\xe0', 'R')),
    ("delete", ('\xe0', 'S')),
    ("page_up", ('\xe0', 'I')),
    ("page_down", ('\xe0', 'Q')),
    ("f11", ('\xe0', '\x85')),
    ("ctrl_left", ('\xe0', 's')),
    ("f1", ('\x00', ';')),
    ("f2", ('\x00', '<')),
    ("f3", ('\x00', '=')),
    ("f4", ('\x00', '>')),
    ("alt_a", ('\x00', '\x1e')),
    ("keypad_8", ('\x00', 'H')),
    ("ctrl_2", ('\x00', '\x03')),
]


class FakeMsvcrt:
    """Stand-in for the msvcrt module, modelled on the UCRT console input path.

    Events are consumed in order. A str event is a key with a character value (one
    UTF-16 code unit). A (prefix, scan_code) tuple is an extended key: getwch() returns
    the prefix and parks the scan code in the one-slot push-back buffer, like
    _getwch_nolock() does with wchbuf. ungetwch() raises OSError while that slot is
    occupied, like _ungetwch_nolock(). kbhit() looks at the event queue only; the
    push-back slot is invisible to it, like the real _kbhit(). Reads that would block
    on a real console raise RuntimeError instead.
    """

    def __init__(self, events: Iterable[KeyEvent], max_polls: int = 10000) -> None:
        self._events: List[KeyEvent] = list(events)
        self._wchbuf: Optional[str] = None
        self._bytes_pending: List[bytes] = []  # narrow push-back used by getch()
        self._polls = 0
        self._max_polls = max_polls
        self.ungetwch_calls = 0

    def kbhit(self) -> bool:
        self._polls += 1
        if self._polls > self._max_polls:
            raise RuntimeError('key queue polled too often; loop did not terminate')
        return bool(self._events)

    def getwch(self) -> str:
        if self._wchbuf is not None:
            c, self._wchbuf = self._wchbuf, None
            return c
        if not self._events:
            raise RuntimeError('getwch() would block: no key event pending')
        event = self._events.pop(0)
        if isinstance(event, tuple):
            prefix, scan_code = event
            self._wchbuf = scan_code
            return prefix
        return event

    def ungetwch(self, c: str) -> None:
        self.ungetwch_calls += 1
        if len(c) != 1:
            raise TypeError('ungetwch() argument must be a single character')
        if self._wchbuf is not None:
            raise OSError(0, 'Error')  # the real call fails while the slot is occupied
        self._wchbuf = c

    def getch(self) -> bytes:
        # Narrow API, reached only by the base commit and by getarrow(): bytes in a
        # single-byte console code page (cp437), scan code after the prefix.
        if self._bytes_pending:
            return self._bytes_pending.pop(0)
        if not self._events:
            raise RuntimeError('getch() would block: no key event pending')
        event = self._events.pop(0)
        if isinstance(event, tuple):
            prefix, scan_code = event
            self._bytes_pending.append(scan_code.encode('latin-1'))
            return prefix.encode('latin-1')
        return event.encode('cp437')

    @property
    def remaining(self) -> int:
        return len(self._events)

    @property
    def pushback_empty(self) -> bool:
        return self._wchbuf is None and not self._bytes_pending


def use_fake_windows_console(monkeypatch: pytest.MonkeyPatch, events: Iterable[KeyEvent]) -> FakeMsvcrt:
    """Make the Windows branch of pyocd.utility.kbhit run against a FakeMsvcrt on any OS.

    Only module globals of pyocd.utility.kbhit are patched; the real os.name is untouched.
    """
    fake = FakeMsvcrt(events)
    monkeypatch.setattr(kbhit_module, 'os', types.SimpleNamespace(name='nt'))
    monkeypatch.setattr(kbhit_module, 'msvcrt', fake, raising=False)
    return fake


class TestKBHitWindows:

    def test_e0_prefixed_key_then_normal_key(self, monkeypatch):
        fake = use_fake_windows_console(monkeypatch, [ARROW_UP, 'a'])
        kb = KBHit()
        got = [kb.getch(), kb.getch()]
        assert got == ['', 'a']
        remaining = fake.remaining
        assert remaining == 0
        pushback_empty = fake.pushback_empty
        assert pushback_empty

    def test_00_prefixed_key_then_normal_key(self, monkeypatch):
        fake = use_fake_windows_console(monkeypatch, [F1, 'b'])
        kb = KBHit()
        c = kb.getch()
        assert not c  # an extended key has no character value
        assert c == ''
        assert kb.getch() == 'b'  # the scan code was consumed, the next key is intact
        remaining = fake.remaining
        assert remaining == 0
        pushback_empty = fake.pushback_empty
        assert pushback_empty

    def test_interleaved_extended_and_ordinary_keys(self, monkeypatch):
        fake = use_fake_windows_console(monkeypatch, [ARROW_LEFT, 'x', ARROW_RIGHT, 'y', ESC])
        kb = KBHit()
        got = [kb.getch() for _ in range(5)]
        assert got == ['', 'x', '', 'y', ESC]
        remaining = fake.remaining
        assert remaining == 0
        pushback_empty = fake.pushback_empty
        assert pushback_empty

    @pytest.mark.parametrize("key", ['a', 'Z', '1', ' ', '\r', '\n', ESC],
                             ids=["a", "Z", "1", "space", "cr", "lf", "escape"])
    def test_ordinary_key_returned_unchanged(self, monkeypatch, key):
        fake = use_fake_windows_console(monkeypatch, [key])
        kb = KBHit()
        assert kb.getch() == key
        remaining = fake.remaining
        assert remaining == 0

    def test_non_ascii_character_returned_unchanged(self, monkeypatch):
        fake = use_fake_windows_console(monkeypatch, [A_UMLAUT])
        kb = KBHit()
        assert kb.getch() == A_UMLAUT
        remaining = fake.remaining
        assert remaining == 0

    def test_a_grave_alone_is_a_character(self, monkeypatch):
        # Nothing follows the key. A second console read would block on a real console and
        # raises RuntimeError in the fake; the push-back probe must not read the console.
        fake = use_fake_windows_console(monkeypatch, [A_GRAVE])
        kb = KBHit()
        assert kb.getch() == A_GRAVE
        remaining = fake.remaining
        assert remaining == 0
        pushback_empty = fake.pushback_empty
        assert pushback_empty
        calls = fake.ungetwch_calls
        assert calls == 1

    def test_a_grave_followed_by_normal_key(self, monkeypatch):
        # Both keys are already queued, as with fast typing or a paste.
        fake = use_fake_windows_console(monkeypatch, [A_GRAVE, 'x'])
        kb = KBHit()
        got = [kb.getch(), kb.getch()]
        assert got == [A_GRAVE, 'x']
        remaining = fake.remaining
        assert remaining == 0
        pushback_empty = fake.pushback_empty
        assert pushback_empty

    def test_a_grave_interleaved_with_extended_keys(self, monkeypatch):
        fake = use_fake_windows_console(monkeypatch, [A_GRAVE, ARROW_UP, A_GRAVE, ESC])
        kb = KBHit()
        got = [kb.getch() for _ in range(4)]
        assert got == [A_GRAVE, '', A_GRAVE, ESC]
        remaining = fake.remaining
        assert remaining == 0
        pushback_empty = fake.pushback_empty
        assert pushback_empty

    def test_extended_key_consumes_exactly_the_scan_code(self, monkeypatch):
        fake = use_fake_windows_console(monkeypatch, [ARROW_UP])
        kb = KBHit()
        assert kb.getch() == ''
        pushback_empty = fake.pushback_empty
        assert pushback_empty
        remaining = fake.remaining
        assert remaining == 0
        calls = fake.ungetwch_calls
        assert calls == 1

    def test_ordinary_characters_are_not_probed(self, monkeypatch):
        fake = use_fake_windows_console(monkeypatch, ['a', A_UMLAUT])
        kb = KBHit()
        assert kb.getch() == 'a'
        assert kb.getch() == A_UMLAUT
        calls = fake.ungetwch_calls
        assert calls == 0

    def test_bare_nul_is_reported_as_ignored(self, monkeypatch):
        # U+0000 with an empty push-back slot is not produced by any key; the contract still
        # promises that getch() never returns NUL.
        fake = use_fake_windows_console(monkeypatch, [NUL])
        kb = KBHit()
        c = kb.getch()
        assert not c
        assert c == ''
        remaining = fake.remaining
        assert remaining == 0
        pushback_empty = fake.pushback_empty
        assert pushback_empty

    def test_getarrow_unchanged(self, monkeypatch):
        fake = use_fake_windows_console(monkeypatch, [ARROW_UP, ARROW_RIGHT, ARROW_DOWN, ARROW_LEFT])
        kb = KBHit()
        assert [kb.getarrow() for _ in range(4)] == [0, 1, 2, 3]
        remaining = fake.remaining
        assert remaining == 0
        pushback_empty = fake.pushback_empty
        assert pushback_empty

    @pytest.mark.parametrize("key", [pytest.param(event, id=name) for name, event in EXTENDED_KEY_TABLE])
    def test_scan_code_never_surfaces(self, monkeypatch, key):
        fake = use_fake_windows_console(monkeypatch, [key])
        kb = KBHit()
        c = kb.getch()
        assert not c
        assert c == ''
        remaining = fake.remaining
        assert remaining == 0
        pushback_empty = fake.pushback_empty
        assert pushback_empty
