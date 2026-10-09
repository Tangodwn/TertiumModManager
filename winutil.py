from __future__ import annotations

import base64
import ctypes
import os
import sys
from ctypes import wintypes
from pathlib import Path

from core import ModManagerError


class DATA_BLOB(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte))]


def _blob_from_bytes(data: bytes) -> tuple[DATA_BLOB, ctypes.Array]:
    buf = ctypes.create_string_buffer(data)
    blob = DATA_BLOB(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_byte)))
    return blob, buf


def _bytes_from_blob(blob: DATA_BLOB) -> bytes:
    return ctypes.string_at(blob.pbData, blob.cbData)


def protect_secret(secret: str) -> str:
    if os.name != "nt":
        return "plain:" + base64.b64encode(secret.encode("utf-8")).decode("ascii")
    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    in_blob, _keep = _blob_from_bytes(secret.encode("utf-8"))
    out_blob = DATA_BLOB()
    if not crypt32.CryptProtectData(ctypes.byref(in_blob), None, None, None, None, 0, ctypes.byref(out_blob)):
        raise ctypes.WinError()
    try:
        data = _bytes_from_blob(out_blob)
        return "dpapi:" + base64.b64encode(data).decode("ascii")
    finally:
        kernel32.LocalFree(out_blob.pbData)


def unprotect_secret(value: str) -> str:
    if not value:
        return ""
    if value.startswith("plain:"):
        return base64.b64decode(value[6:]).decode("utf-8")
    if not value.startswith("dpapi:"):
        return ""
    if os.name != "nt":
        return ""
    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    raw = base64.b64decode(value[6:])
    in_blob, _keep = _blob_from_bytes(raw)
    out_blob = DATA_BLOB()
    if not crypt32.CryptUnprotectData(ctypes.byref(in_blob), None, None, None, None, 0, ctypes.byref(out_blob)):
        raise ctypes.WinError()
    try:
        return _bytes_from_blob(out_blob).decode("utf-8")
    finally:
        kernel32.LocalFree(out_blob.pbData)


def executable_command() -> str:
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}" "%1"'
    pythonw = Path(sys.executable)
    if pythonw.name.lower() == "python.exe":
        candidate = pythonw.with_name("pythonw.exe")
        if candidate.exists():
            pythonw = candidate
    script = Path(sys.argv[0]).resolve()
    return f'"{pythonw}" "{script}" "%1"'


def register_nxm_protocol() -> None:
    if os.name != "nt":
        raise ModManagerError("nxm:// protocol registration is only supported on Windows.")
    import winreg

    command = executable_command()
    base = r"Software\Classes\nxm"
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, base) as key:
        winreg.SetValueEx(key, "", 0, winreg.REG_SZ, "URL:Nexus Mods Protocol")
        winreg.SetValueEx(key, "URL Protocol", 0, winreg.REG_SZ, "")
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, base + r"\DefaultIcon") as key:
        winreg.SetValueEx(key, "", 0, winreg.REG_SZ, sys.executable)
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, base + r"\shell\open\command") as key:
        winreg.SetValueEx(key, "", 0, winreg.REG_SZ, command)


def nxm_protocol_command() -> str | None:
    if os.name != "nt":
        return None
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Classes\nxm\shell\open\command") as key:
            return winreg.QueryValueEx(key, "")[0]
    except OSError:
        return None
