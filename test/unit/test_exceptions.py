# pyOCD debugger
# Copyright (c) 2020 Arm Limited
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

import pytest

from pyocd.core.exceptions import *

# Tests for TransferFaultError.
class TestFaultError:
    def test_no_args(self):
        e = TransferFaultError()
        assert str(e) == 'Memory transfer fault'

    def test_no_args_set_addr(self):
        e = TransferFaultError()
        e.fault_address = 0x1000
        assert str(e) == 'Memory transfer fault @ 0x00001000'

    def test_no_args_set_addr_len(self):
        e = TransferFaultError()
        e.fault_address = 0x1000
        e.fault_length = 0x8
        assert str(e) == 'Memory transfer fault @ 0x00001000-0x00001007'

    def test_msg(self):
        e = TransferFaultError("temporal anomaly")
        assert str(e) == 'Memory transfer fault (temporal anomaly)'

    def test_arg_tuple(self):
        e = TransferFaultError(-1, 1234)
        assert str(e) == 'Memory transfer fault (-1, 1234)'

    def test_msg_ctor_addr(self):
        e = TransferFaultError("my bad", fault_address=0x20008400)
        assert e.fault_address == 0x20008400
        assert str(e) == 'Memory transfer fault (my bad) @ 0x20008400'

    def test_msg_ctor_addr_len(self):
        e = TransferFaultError("my bad", fault_address=0x20008400, length=32)
        assert e.fault_address == 0x20008400
        assert str(e) == 'Memory transfer fault (my bad) @ 0x20008400-0x2000841f'

# Tests for FlashFailure.
class TestFlashFailure:
    def test_no_args(self):
        e = FlashFailure()
        assert str(e) == ""
        assert e.address == None
        assert e.result_code == None

    def test_msg(self):
        e = FlashFailure("something exploded")
        assert str(e) == "something exploded"
        assert e.address == None
        assert e.result_code == None

    def test_addr(self):
        e = FlashFailure(address=0x4000)
        assert str(e) == "(address 0x00004000)"
        assert e.address == 0x4000
        assert e.result_code == None

    def test_code(self):
        e = FlashFailure(result_code=0x104)
        assert str(e) == "(result code 0x104)"
        assert e.address == None
        assert e.result_code == 0x104

    def test_addr_code(self):
        e = FlashFailure(address=0x4000, result_code=0x104)
        assert str(e) == "(address 0x00004000; result code 0x104)"
        assert e.address == 0x4000
        assert e.result_code == 0x104

    def test_msg_addr_code(self):
        e = FlashFailure("major error", address=0x4000, result_code=0x104)
        assert str(e) == "major error (address 0x00004000; result code 0x104)"
        assert e.address == 0x4000
        assert e.result_code == 0x104

# Classes that carry the probe status attribute.
STATUS_CLASSES = [
    pytest.param(ProbeError, id='ProbeError'),
    pytest.param(ProbeDisconnected, id='ProbeDisconnected'),
    pytest.param(TransferError, id='TransferError'),
    pytest.param(TransferTimeoutError, id='TransferTimeoutError'),
    pytest.param(TransferProtocolError, id='TransferProtocolError'),
    pytest.param(TransferFaultError, id='TransferFaultError'),
]

# Tests for the probe status attribute on the ProbeError and TransferError families.
class TestStatusAttribute:
    @pytest.mark.parametrize('cls', STATUS_CLASSES)
    def test_default_status_is_none(self, cls):
        e = cls()
        assert e.status is None

    @pytest.mark.parametrize('cls', STATUS_CLASSES)
    def test_explicit_status_preserved(self, cls):
        e = cls('x', status=0x0b)
        assert e.status == 0x0b
        assert e.args == ('x',)

    def test_probe_error_message_unchanged(self):
        e = ProbeError('x', status=0x0b)
        assert str(e) == 'x'
        assert repr(e) == "ProbeError('x')"
        assert e.status == 0x0b

    def test_transfer_error_message_unchanged(self):
        e = TransferError('x', status=0x12)
        assert str(e) == 'x'
        assert repr(e) == "TransferError('x')"
        assert e.status == 0x12

    def test_fault_error_status_with_address_and_length(self):
        e = TransferFaultError('my bad', fault_address=0x20008400, length=32, status=0x11)
        assert e.fault_address == 0x20008400
        assert e.fault_length == 32
        assert str(e) == 'Memory transfer fault (my bad) @ 0x20008400-0x2000841f'
        assert e.status == 0x11

    def test_positional_args_preserved(self):
        e = ProbeError('a', 2)
        assert e.args == ('a', 2)
        assert str(e) == "('a', 2)"
        assert e.status is None

    @pytest.mark.parametrize('cls', [
        pytest.param(ProbeError, id='ProbeError'),
        pytest.param(TransferError, id='TransferError'),
        pytest.param(TransferTimeoutError, id='TransferTimeoutError'),
    ])
    def test_unknown_keyword_rejected(self, cls):
        with pytest.raises(TypeError):
            cls('x', unknown=1)

    # The assignment below is rejected by the property itself, so it never invokes the getter and
    # therefore cannot mask a failure to report the status.
    @pytest.mark.parametrize('cls', STATUS_CLASSES)
    def test_status_is_read_only(self, cls):
        e = cls()
        with pytest.raises(AttributeError):
            e.status = 0x0b
