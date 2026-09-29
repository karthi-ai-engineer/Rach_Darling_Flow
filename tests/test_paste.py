import ctypes
import struct

from sst.paste import INPUT


def test_input_struct_has_the_size_sendinput_expects():
    # SendInput silently does nothing when cbSize is wrong: 40 bytes on 64-bit Windows, 28 on 32-bit.
    assert ctypes.sizeof(INPUT) == (40 if struct.calcsize("P") == 8 else 28)
