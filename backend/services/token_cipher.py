import ctypes
from ctypes import wintypes
import json
import os


class TokenStorageError(Exception):
    pass


class DataBlob(ctypes.Structure):
    _fields_ = [('size', wintypes.DWORD), ('data', ctypes.POINTER(ctypes.c_ubyte))]


class WindowsTokenCipher:
    def _crypt(self, user_id, payload, decrypt=False):
        if os.name != 'nt':
            raise TokenStorageError('Encrypted Google token storage requires this Windows user profile.')
        if type(user_id) is not int or user_id <= 0:
            raise TokenStorageError('Invalid token owner.')
        source_buffer = (ctypes.c_ubyte * len(payload)).from_buffer_copy(payload)
        entropy = f'AI Study Agent Google OAuth v1:{user_id}'.encode()
        entropy_buffer = (ctypes.c_ubyte * len(entropy)).from_buffer_copy(entropy)
        source = DataBlob(len(payload), ctypes.cast(source_buffer, ctypes.POINTER(ctypes.c_ubyte)))
        extra = DataBlob(len(entropy), ctypes.cast(entropy_buffer, ctypes.POINTER(ctypes.c_ubyte)))
        output = DataBlob()
        crypt = ctypes.WinDLL('crypt32', use_last_error=True)
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.LocalFree.argtypes = [wintypes.HLOCAL]
        kernel.LocalFree.restype = wintypes.HLOCAL
        operation = crypt.CryptUnprotectData if decrypt else crypt.CryptProtectData
        operation.argtypes = [ctypes.POINTER(DataBlob), ctypes.c_void_p, ctypes.POINTER(DataBlob),
                             ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(DataBlob)]
        operation.restype = wintypes.BOOL
        if not operation(ctypes.byref(source), None, ctypes.byref(extra), None, None, 1, ctypes.byref(output)):
            raise TokenStorageError('Google credentials could not be protected or unlocked.')
        try:
            return ctypes.string_at(output.data, output.size)
        finally:
            kernel.LocalFree(ctypes.cast(output.data, wintypes.HLOCAL))

    def encrypt(self, user_id, value):
        return self._crypt(user_id, json.dumps(value).encode())

    def decrypt(self, user_id, value):
        try:
            return json.loads(self._crypt(user_id, bytes(value), decrypt=True))
        except (ValueError, TypeError):
            raise TokenStorageError('Stored Google credentials could not be unlocked.') from None
