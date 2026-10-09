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

import argparse
from typing import Iterable, List, Optional

import pytest

from pyocd.subcommands.rtt_cmd import RTTSubcommand
from pyocd.utility.kbhit import KBHit

from .test_kbhit import A_GRAVE, A_UMLAUT, ARROW_UP, ESC, F1, use_fake_windows_console


class FakeUpChannel:
    """Up channel (target to host) that never has data; see pyocd.debug.rtt.RTTUpChannel.read()."""

    def read(self) -> bytes:
        return b''


class RecordingDownChannel:
    """Down channel (host to target) that records every byte; see RTTDownChannel.write()."""

    def __init__(self) -> None:
        self.written = bytearray()

    def write(self, data: bytes, blocking: bool = False) -> int:
        self.written += data
        return len(data)


class StallingDownChannel(RecordingDownChannel):
    """Down channel that accepts nothing on its first write, like a target with a full buffer.

    RTTDownChannel.write() returns the number of bytes accepted, which may be fewer than
    offered; viewer_loop() keeps the remainder in its buffer and retries it on the next pass.
    """

    def __init__(self) -> None:
        super().__init__()
        self.calls = 0

    def write(self, data: bytes, blocking: bool = False) -> int:
        self.calls += 1
        if self.calls == 1:
            return 0  # nothing accepted; the caller must retry these bytes
        return super().write(data, blocking)


class FakeKeyboard:
    """Duck-typed KBHit: getch() yields the given strings; '' models an ignored key."""

    def __init__(self, chars: Iterable[str], max_polls: int = 10000) -> None:
        self._chars: List[str] = list(chars)
        self._polls = 0
        self._max_polls = max_polls

    def kbhit(self) -> bool:
        self._polls += 1
        if self._polls > self._max_polls:
            raise RuntimeError('keyboard polled too often; loop did not terminate')
        return bool(self._chars)

    def getch(self) -> str:
        if not self._chars:
            raise RuntimeError('getch() called with no pending key')
        return self._chars.pop(0)


def run_viewer(monkeypatch: pytest.MonkeyPatch, kb: object, down_chan: Optional[RecordingDownChannel]) -> None:
    """Run RTTSubcommand.viewer_loop() to completion with the poll delay removed."""
    monkeypatch.setattr('pyocd.subcommands.rtt_cmd.sleep', lambda _: None)
    RTTSubcommand(argparse.Namespace()).viewer_loop(FakeUpChannel(), down_chan, kb)


class TestViewerLoop:

    def test_extended_key_then_ordinary_keys_end_to_end(self, monkeypatch, capsys):
        fake = use_fake_windows_console(monkeypatch, [ARROW_UP, 'a', 'b', '\r', ESC])
        down = RecordingDownChannel()
        run_viewer(monkeypatch, KBHit(), down)
        written = bytes(down.written)
        assert b'\x00' not in written
        assert b'\xe0' not in written
        assert b'H' not in written
        assert written == b'ab\r'
        assert capsys.readouterr().out == 'ab'
        remaining = fake.remaining
        assert remaining == 0

    def test_a_grave_end_to_end(self, monkeypatch, capsys):
        fake = use_fake_windows_console(monkeypatch, [A_GRAVE, ARROW_UP, 'a', ESC])
        down = RecordingDownChannel()
        run_viewer(monkeypatch, KBHit(), down)
        assert bytes(down.written) == b'\xc3\xa0a'
        assert capsys.readouterr().out == A_GRAVE + 'a'
        remaining = fake.remaining
        assert remaining == 0

    def test_00_prefixed_key_sends_nothing(self, monkeypatch, capsys):
        fake = use_fake_windows_console(monkeypatch, [F1, 'a', ESC])
        down = RecordingDownChannel()
        run_viewer(monkeypatch, KBHit(), down)
        written = bytes(down.written)
        assert b'\x00' not in written
        assert b';' not in written
        assert written == b'a'
        assert capsys.readouterr().out == 'a'
        remaining = fake.remaining
        assert remaining == 0

    def test_ignored_key_is_neither_echoed_nor_sent(self, monkeypatch, capsys):
        down = RecordingDownChannel()
        run_viewer(monkeypatch, FakeKeyboard(['', 'a', ESC]), down)
        assert bytes(down.written) == b'a'
        assert capsys.readouterr().out == 'a'

    def test_escape_ends_loop_without_output(self, monkeypatch, capsys):
        down = RecordingDownChannel()
        run_viewer(monkeypatch, FakeKeyboard([ESC]), down)
        assert bytes(down.written) == b''
        assert capsys.readouterr().out == ''

    def test_printable_text_and_newline_are_echoed_and_sent(self, monkeypatch, capsys):
        down = RecordingDownChannel()
        run_viewer(monkeypatch, FakeKeyboard(['h', 'i', '\n', ESC]), down)
        assert bytes(down.written) == b'hi\n'
        assert capsys.readouterr().out == 'hi\n'

    def test_non_ascii_character_reaches_target_as_utf8(self, monkeypatch, capsys):
        fake = use_fake_windows_console(monkeypatch, [A_UMLAUT, ESC])
        down = RecordingDownChannel()
        run_viewer(monkeypatch, KBHit(), down)
        assert bytes(down.written) == b'\xc3\xa4'
        assert capsys.readouterr().out == A_UMLAUT
        remaining = fake.remaining
        assert remaining == 0

    def test_extended_key_and_a_grave_without_down_channel(self, monkeypatch, capsys):
        fake = use_fake_windows_console(monkeypatch, [ARROW_UP, A_GRAVE, 'a', ESC])
        run_viewer(monkeypatch, KBHit(), None)
        assert capsys.readouterr().out == A_GRAVE + 'a'
        remaining = fake.remaining
        assert remaining == 0

    def test_pending_bytes_are_retried_after_an_ignored_key(self, monkeypatch, capsys):
        # The target accepts nothing on the first write, so 'a' stays in the send buffer.
        # Handling the ignored key must not skip the retry of those pending bytes: Escape
        # ends the loop on the next pass and anything still buffered would be lost.
        down = StallingDownChannel()
        run_viewer(monkeypatch, FakeKeyboard(['a', '', ESC]), down)
        written = bytes(down.written)
        assert written == b'a'
        assert capsys.readouterr().out == 'a'
        calls = down.calls
        assert calls == 2
