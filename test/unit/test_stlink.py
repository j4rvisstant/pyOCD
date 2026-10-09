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

import struct
from unittest import mock

import pytest

from pyocd.core import exceptions
from pyocd.probe.stlink.constants import Status
from pyocd.probe.stlink.stlink import STLink


def status_response(status: int) -> bytes:
    """Two-byte status word as returned by most ST-Link commands."""
    return struct.pack('<H', status)


def rw_status_response(status: int, fault_addr: int = 0) -> bytes:
    """Twelve-byte JTAG_GETLASTRWSTATUS2 response: status, reserved, fault address, padding."""
    return struct.pack('<HHI', status, 0, fault_addr) + bytes(4)


# Vendor-specific statuses that are not in STLink._ERROR_CLASSES and therefore raise a bare ProbeError.
VENDOR_STATUS_CASES = [
    pytest.param(Status.JTAG_UNKNOWN_JTAG_CHAIN, 'STLink error (4): Unknown JTAG chain', id='0x04'),
    pytest.param(Status.JTAG_NO_DEVICE_CONNECTED, 'STLink error (5): No device connected', id='0x05'),
    pytest.param(Status.JTAG_GET_IDCODE_ERROR, 'STLink error (9): Get IDCODE error', id='0x09'),
    pytest.param(Status.JTAG_DBG_POWER_ERROR, 'STLink error (11): Debug power error', id='0x0b'),
    pytest.param(Status.JTAG_ALREADY_OPENED_IN_OTHER_MODE, 'STLink error (14): Already opened in another mode',
                 id='0x0e'),
    pytest.param(Status.JTAG_FREQ_NOT_SUPPORTED, 'STLink error (65): Frequency not supported', id='0x41'),
]

# Every status mapped to an exception class by STLink._ERROR_CLASSES, with the expected class, the
# expected exception args and the expected str() spelled out literally. This table is the oracle: it is
# deliberately not derived from the production mapping or from Status.get_error_message(), so that a
# change to either of those fails here instead of redefining what these cases expect.
MAPPED_STATUS_CASES = (
    (0x10, exceptions.TransferTimeoutError, 'STLink error (16): AP wait',
     'STLink error (16): AP wait'),
    (0x11, exceptions.TransferFaultError, 'STLink error (17): AP fault',
     'Memory transfer fault (STLink error (17): AP fault)'),
    (0x12, exceptions.TransferError, 'STLink error (18): AP error',
     'STLink error (18): AP error'),
    (0x13, exceptions.TransferError, 'STLink error (19): AP parity error',
     'STLink error (19): AP parity error'),
    (0x14, exceptions.TransferTimeoutError, 'STLink error (20): DP wait',
     'STLink error (20): DP wait'),
    (0x15, exceptions.TransferFaultError, 'STLink error (21): DP fault',
     'Memory transfer fault (STLink error (21): DP fault)'),
    (0x16, exceptions.TransferError, 'STLink error (22): DP error',
     'STLink error (22): DP error'),
    (0x17, exceptions.TransferError, 'STLink error (23): DP parity error',
     'STLink error (23): DP parity error'),
    (0x18, exceptions.TransferFaultError, 'STLink error (24): AP WDATA error',
     'Memory transfer fault (STLink error (24): AP WDATA error)'),
    (0x19, exceptions.TransferError, 'STLink error (25): AP sticky error',
     'STLink error (25): AP sticky error'),
    (0x1a, exceptions.TransferError, 'STLink error (26): AP sticky overrun error',
     'STLink error (26): AP sticky overrun error'),
)

MAPPED_STATUS_PARAMS = [
    pytest.param(status, cls, message, text, id=f'0x{status:02x}')
    for status, cls, message, text in MAPPED_STATUS_CASES
]

# The complete status-to-exception mapping the production code must keep, taken from the table above.
EXPECTED_ERROR_CLASSES = {status: cls for status, cls, _message, _text in MAPPED_STATUS_CASES}

ADDR = 0x20000000
DATA = [1, 2, 3, 4]


@pytest.fixture(scope='function')
def link():
    link = STLink(mock.Mock())
    # _get_csw_bytes() indexes MIN_JTAG_VERSION_MEM_CSW[hw_version]; the default 0 raises KeyError.
    link._hw_version = 3
    return link


class TestCheckStatus:
    def test_ok(self, link):
        assert link._check_status(status_response(Status.JTAG_OK)) is None

    @pytest.mark.parametrize('status, message', VENDOR_STATUS_CASES)
    def test_vendor_status(self, link, status, message):
        with pytest.raises(exceptions.ProbeError) as excinfo:
            link._check_status(status_response(status))
        exc = excinfo.value
        assert type(exc) is exceptions.ProbeError
        assert str(exc) == message
        assert str(exc) == Status.get_error_message(status)
        assert exc.status == status

    def test_error_classes_mapping_unchanged(self):
        assert STLink._ERROR_CLASSES == EXPECTED_ERROR_CLASSES

    @pytest.mark.parametrize('status, cls, message, text', MAPPED_STATUS_PARAMS)
    def test_mapped_status(self, link, status, cls, message, text):
        with pytest.raises(cls) as excinfo:
            link._check_status(status_response(status))
        exc = excinfo.value
        assert type(exc) is cls
        assert exc.args == (message,)
        assert str(exc) == text
        assert exc.status == status

    def test_unknown_status(self, link):
        with pytest.raises(exceptions.ProbeError) as excinfo:
            link._check_status(status_response(0x7f))
        exc = excinfo.value
        assert type(exc) is exceptions.ProbeError
        assert str(exc) == 'STLink error (127): Unknown error'
        assert exc.status == 0x7f


class TestReadMem:
    def test_ok(self, link):
        link._device.transfer.side_effect = [bytearray(4), rw_status_response(Status.JTAG_OK)]
        assert link.read_mem32(ADDR, 4, 0, 0) == [0, 0, 0, 0]
        assert link._device.transfer.call_count == 2

    def test_fault(self, link):
        link._device.transfer.side_effect = [bytearray(4), rw_status_response(Status.SWD_AP_FAULT, ADDR)]
        with pytest.raises(exceptions.TransferFaultError) as excinfo:
            link.read_mem32(ADDR, 4, 0, 0)
        exc = excinfo.value
        assert type(exc) is exceptions.TransferFaultError
        assert str(exc) == 'Memory transfer fault (read) @ 0x20000000-0x20000003'
        assert exc.fault_address == 0x20000000
        assert exc.fault_length == 4
        assert exc.status == Status.SWD_AP_FAULT

    def test_fault_unknown_error_status(self, link):
        link._device.transfer.side_effect = [bytearray(4), rw_status_response(Status.JTAG_UNKNOWN_ERROR, ADDR)]
        with pytest.raises(exceptions.TransferFaultError) as excinfo:
            link.read_mem32(ADDR, 4, 0, 0)
        exc = excinfo.value
        assert str(exc) == 'Memory transfer fault (read) @ 0x20000000-0x20000003'
        assert exc.fault_address == 0x20000000
        assert exc.fault_length == 4
        assert exc.status == Status.JTAG_UNKNOWN_ERROR

    def test_mapped_non_fault(self, link):
        link._device.transfer.side_effect = [bytearray(4), rw_status_response(Status.SWD_AP_WAIT)]
        with pytest.raises(exceptions.TransferTimeoutError) as excinfo:
            link.read_mem32(ADDR, 4, 0, 0)
        exc = excinfo.value
        assert type(exc) is exceptions.TransferTimeoutError
        assert str(exc) == 'STLink error (16): AP wait'
        assert exc.status == Status.SWD_AP_WAIT

    def test_vendor(self, link):
        link._device.transfer.side_effect = [bytearray(4), rw_status_response(Status.JTAG_DBG_POWER_ERROR)]
        with pytest.raises(exceptions.ProbeError) as excinfo:
            link.read_mem32(ADDR, 4, 0, 0)
        exc = excinfo.value
        assert type(exc) is exceptions.ProbeError
        assert str(exc) == 'STLink error (11): Debug power error'
        assert exc.status == Status.JTAG_DBG_POWER_ERROR


class TestWriteMem:
    def test_ok(self, link):
        link._device.transfer.side_effect = [None, rw_status_response(Status.JTAG_OK)]
        assert link.write_mem32(ADDR, DATA, 0, 0) is None
        assert link._device.transfer.call_count == 2

    def test_fault(self, link):
        link._device.transfer.side_effect = [None, rw_status_response(Status.SWD_AP_FAULT, ADDR)]
        with pytest.raises(exceptions.TransferFaultError) as excinfo:
            link.write_mem32(ADDR, DATA, 0, 0)
        exc = excinfo.value
        assert type(exc) is exceptions.TransferFaultError
        assert str(exc) == 'Memory transfer fault (write) @ 0x20000000-0x20000003'
        assert exc.fault_address == 0x20000000
        assert exc.fault_length == 4
        assert exc.status == Status.SWD_AP_FAULT

    def test_mapped_non_fault(self, link):
        link._device.transfer.side_effect = [None, rw_status_response(Status.SWD_AP_WAIT)]
        with pytest.raises(exceptions.TransferTimeoutError) as excinfo:
            link.write_mem32(ADDR, DATA, 0, 0)
        exc = excinfo.value
        assert type(exc) is exceptions.TransferTimeoutError
        assert str(exc) == 'STLink error (16): AP wait'
        assert exc.status == Status.SWD_AP_WAIT

    def test_vendor(self, link):
        link._device.transfer.side_effect = [None, rw_status_response(Status.JTAG_DBG_POWER_ERROR)]
        with pytest.raises(exceptions.ProbeError) as excinfo:
            link.write_mem32(ADDR, DATA, 0, 0)
        exc = excinfo.value
        assert type(exc) is exceptions.ProbeError
        assert str(exc) == 'STLink error (11): Debug power error'
        assert exc.status == Status.JTAG_DBG_POWER_ERROR
