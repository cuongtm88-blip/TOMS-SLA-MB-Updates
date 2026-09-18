"""Store the diagnostics token in the operating system credential vault."""
from __future__ import annotations

import ctypes
import subprocess
import sys
from ctypes import wintypes


def _windows_read(service, account):
    class CREDENTIAL(ctypes.Structure):
        _fields_ = [
            ("Flags", wintypes.DWORD),
            ("Type", wintypes.DWORD),
            ("TargetName", wintypes.LPWSTR),
            ("Comment", wintypes.LPWSTR),
            ("LastWritten", wintypes.FILETIME),
            ("CredentialBlobSize", wintypes.DWORD),
            ("CredentialBlob", ctypes.POINTER(ctypes.c_ubyte)),
            ("Persist", wintypes.DWORD),
            ("AttributeCount", wintypes.DWORD),
            ("Attributes", ctypes.c_void_p),
            ("TargetAlias", wintypes.LPWSTR),
            ("UserName", wintypes.LPWSTR),
        ]

    pointer = ctypes.POINTER(CREDENTIAL)()
    advapi32 = ctypes.WinDLL("Advapi32.dll")
    advapi32.CredReadW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.POINTER(ctypes.POINTER(CREDENTIAL)),
    ]
    advapi32.CredReadW.restype = wintypes.BOOL
    advapi32.CredFree.argtypes = [ctypes.c_void_p]
    advapi32.CredFree.restype = None
    if not advapi32.CredReadW(service, 1, 0, ctypes.byref(pointer)):
        return ""
    try:
        credential = pointer.contents
        blob = ctypes.string_at(
            credential.CredentialBlob, credential.CredentialBlobSize
        )
        return blob.decode("utf-16-le")
    finally:
        advapi32.CredFree(pointer)


def _windows_write(service, account, secret):
    class CREDENTIAL(ctypes.Structure):
        _fields_ = [
            ("Flags", wintypes.DWORD),
            ("Type", wintypes.DWORD),
            ("TargetName", wintypes.LPWSTR),
            ("Comment", wintypes.LPWSTR),
            ("LastWritten", wintypes.FILETIME),
            ("CredentialBlobSize", wintypes.DWORD),
            ("CredentialBlob", ctypes.POINTER(ctypes.c_ubyte)),
            ("Persist", wintypes.DWORD),
            ("AttributeCount", wintypes.DWORD),
            ("Attributes", ctypes.c_void_p),
            ("TargetAlias", wintypes.LPWSTR),
            ("UserName", wintypes.LPWSTR),
        ]

    encoded = secret.encode("utf-16-le")
    blob = (ctypes.c_ubyte * len(encoded)).from_buffer_copy(encoded)
    credential = CREDENTIAL()
    credential.Type = 1
    credential.TargetName = service
    credential.CredentialBlobSize = len(encoded)
    credential.CredentialBlob = ctypes.cast(blob, ctypes.POINTER(ctypes.c_ubyte))
    credential.Persist = 2
    credential.UserName = account
    advapi32 = ctypes.WinDLL("Advapi32.dll")
    advapi32.CredWriteW.argtypes = [
        ctypes.POINTER(CREDENTIAL),
        wintypes.DWORD,
    ]
    advapi32.CredWriteW.restype = wintypes.BOOL
    if not advapi32.CredWriteW(ctypes.byref(credential), 0):
        raise ctypes.WinError()


def load_secret(service, account):
    if sys.platform == "win32":
        return _windows_read(service, account)
    if sys.platform == "darwin":
        result = subprocess.run(
            ["/usr/bin/security", "find-generic-password", "-s", service, "-a", account, "-w"],
            capture_output=True,
            text=True,
            check=False,
        )
        return result.stdout.strip() if result.returncode == 0 else ""
    return ""


def save_secret(service, account, secret):
    if sys.platform == "win32":
        _windows_write(service, account, secret)
        return
    if sys.platform == "darwin":
        subprocess.run(
            [
                "/usr/bin/security",
                "add-generic-password",
                "-U",
                "-s",
                service,
                "-a",
                account,
                "-w",
                secret,
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        return
    raise OSError("Hệ điều hành này chưa hỗ trợ kho lưu thông tin xác thực")
