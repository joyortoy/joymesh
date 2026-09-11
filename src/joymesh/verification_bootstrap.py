"""Trusted isolated bootstrap: apply Seatbelt before executing any requested code.

Invoked with Python -I -S so repository modules and site hooks cannot run before
confinement. Direct exec avoids sandbox-exec's posix_spawn bootstrap exception.
"""
import ctypes
import json
import os
import sys


def main():
    request = json.loads(sys.stdin.buffer.read(65536))
    library = ctypes.CDLL('/usr/lib/libsandbox.dylib', use_errno=True)
    library.sandbox_init.argtypes = [ctypes.c_char_p, ctypes.c_uint64, ctypes.POINTER(ctypes.c_char_p)]
    library.sandbox_init.restype = ctypes.c_int
    error = ctypes.c_char_p()
    if library.sandbox_init(request['profile'].encode(), 0, ctypes.byref(error)) != 0:
        raise RuntimeError('verification sandbox initialization failed')
    # EOF input is maintained; the requested process never receives the profile.
    os.execv(request['argv'][0], request['argv'])


if __name__ == '__main__':
    main()
