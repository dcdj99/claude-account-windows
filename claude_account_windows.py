"""Windows Credential Manager, private atomic file updates, and Claude's directory locks."""
import contextlib
import ctypes as C
from ctypes import wintypes as W
import json
import os
from pathlib import Path
import threading
import time
import uuid
import zlib

adv = C.WinDLL("advapi32", use_last_error=True)
kernel = C.WinDLL("kernel32", use_last_error=True)


def bind(dll, func, result, *args):
    call = getattr(dll, func)
    call.restype, call.argtypes = result, list(args)
    return call


class Credential(C.Structure):
    _fields_ = [("Flags", W.DWORD), ("Type", W.DWORD), ("TargetName", W.LPWSTR),
                ("Comment", W.LPWSTR), ("LastWritten", W.FILETIME), ("BlobSize", W.DWORD),
                ("Blob", C.c_void_p), ("Persist", W.DWORD), ("AttributeCount", W.DWORD),
                ("Attributes", C.c_void_p), ("TargetAlias", W.LPWSTR), ("UserName", W.LPWSTR)]


PC = C.POINTER(Credential)
cred_read = bind(adv, "CredReadW", W.BOOL, W.LPCWSTR, W.DWORD, W.DWORD, C.POINTER(PC))
cred_write = bind(adv, "CredWriteW", W.BOOL, PC, W.DWORD)
cred_delete = bind(adv, "CredDeleteW", W.BOOL, W.LPCWSTR, W.DWORD, W.DWORD)
cred_enum = bind(adv, "CredEnumerateW", W.BOOL, W.LPCWSTR, W.DWORD, C.POINTER(W.DWORD), C.POINTER(C.POINTER(PC)))
cred_free = bind(adv, "CredFree", None, C.c_void_p)


class Vault:
    def __init__(self, prefix="ClaudeAccountCLI/v1/"):
        self.prefix = prefix

    def get(self, key):
        ptr = PC()
        if not cred_read(self.prefix + key, 1, 0, C.byref(ptr)):
            if C.get_last_error() == 1168:  # ERROR_NOT_FOUND
                return None
            raise C.WinError(C.get_last_error())
        try:
            return json.loads(zlib.decompress(C.string_at(ptr.contents.Blob, ptr.contents.BlobSize)))
        finally:
            cred_free(ptr)

    def put(self, key, value):
        blob = zlib.compress(json.dumps(value, separators=(",", ":")).encode("utf8"))
        if len(blob) > 2560:
            raise ValueError("The saved login exceeds Windows Credential Manager's entry limit.")
        buf = C.create_string_buffer(blob)
        entry = Credential(Type=1, TargetName=self.prefix + key, BlobSize=len(blob),
                           Blob=C.cast(buf, C.c_void_p), Persist=2, UserName=key)
        if not cred_write(C.byref(entry), 0):
            raise C.WinError(C.get_last_error())

    def delete(self, key):
        if not cred_delete(self.prefix + key, 1, 0) and C.get_last_error() != 1168:
            raise C.WinError(C.get_last_error())

    def names(self):
        start = self.prefix + "profile/"
        count, entries = W.DWORD(), C.POINTER(PC)()
        if not cred_enum(start + "*", 0, C.byref(count), C.byref(entries)):
            if C.get_last_error() == 1168:
                return []
            raise C.WinError(C.get_last_error())
        try:
            return sorted(entries[i].contents.TargetName[len(start):] for i in range(count.value))
        finally:
            cred_free(entries)


@contextlib.contextmanager
def operation_lock(folder):
    """Serialize this tool's mutations, including the whole browser flow. Windows releases on exit."""
    import msvcrt
    with (folder / ".claude-account.operation.lock").open("a+b") as f:
        f.seek(0)
        try:
            msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError as error:
            raise RuntimeError("Another account switch or sign-in is in progress.") from error
        try:
            yield
        finally:
            f.seek(0)
            msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)


@contextlib.contextmanager
def directory_lock(path):
    deadline = time.monotonic() + 8
    while True:
        try:
            path.mkdir()
            break
        except FileExistsError:
            if time.monotonic() >= deadline:
                raise RuntimeError("Claude is updating its login or settings. Retry in a moment.")
            time.sleep(.15)
    stop = threading.Event()
    def touch():
        while not stop.wait(2):
            os.utime(path)
    beat = threading.Thread(target=touch, daemon=True)
    beat.start()
    try:
        yield
    finally:
        stop.set()
        beat.join()
        path.rmdir()


@contextlib.contextmanager
def locked(config_dir, settings):
    # Match Claude's refresh lock ordering. Never remove another owner's lock.
    with directory_lock(config_dir / ".oauth_refresh.lock"), directory_lock(Path(str(config_dir) + ".lock")), directory_lock(Path(str(settings) + ".lock")):
        yield


class SecurityAttributes(C.Structure):
    _fields_ = [("length", W.DWORD), ("descriptor", C.c_void_p), ("inherit", W.BOOL)]


make_sd = bind(adv, "ConvertStringSecurityDescriptorToSecurityDescriptorW", W.BOOL,
               W.LPCWSTR, W.DWORD, C.POINTER(C.c_void_p), C.c_void_p)
create_file = bind(kernel, "CreateFileW", W.HANDLE, W.LPCWSTR, W.DWORD, W.DWORD, C.POINTER(SecurityAttributes), W.DWORD, W.DWORD, W.HANDLE)
replace_file = bind(kernel, "ReplaceFileW", W.BOOL, W.LPCWSTR, W.LPCWSTR, W.LPCWSTR, W.DWORD, C.c_void_p, C.c_void_p)
local_free = bind(kernel, "LocalFree", C.c_void_p, C.c_void_p)
close_handle = bind(kernel, "CloseHandle", W.BOOL, W.HANDLE)
create_directory = bind(kernel, "CreateDirectoryW", W.BOOL, W.LPCWSTR, C.POINTER(SecurityAttributes))


def reject_links(path):
    """Never place managed credentials through a junction or symbolic link."""
    path = Path(os.path.abspath(path))
    for part in (path, *path.parents):
        if part.exists() and part.lstat().st_file_attributes & 0x400:
            raise RuntimeError("Linked or redirected managed paths are not supported.")


def private_directory(path):
    path = Path(path)
    reject_links(path)
    if path.exists():
        if not path.is_dir():
            raise RuntimeError("Expected a managed directory.")
        return
    if not path.parent.exists():
        private_directory(path.parent)
    sd = C.c_void_p()
    if not make_sd("D:P(A;OICI;FA;;;OW)(A;OICI;FA;;;SY)", 1, C.byref(sd), None):
        raise C.WinError(C.get_last_error())
    try:
        sa = SecurityAttributes(C.sizeof(SecurityAttributes), sd, False)
        if not create_directory(str(path), C.byref(sa)) and C.get_last_error() != 183:
            raise C.WinError(C.get_last_error())
    finally:
        local_free(sd)
    reject_links(path)


class SessionLease:
    """Exclusive Windows file handle inherited by Claude: survives wrapper death."""
    def __init__(self, path):
        self.path, self.handle = Path(path), None

    def __enter__(self):
        reject_links(self.path)
        handle = create_file(str(self.path), 0xC0000000, 0, None, 4, 0x80, None)
        if handle == C.c_void_p(-1).value:
            error = C.get_last_error()
            if error in (32, 33):
                raise RuntimeError("This chat is running or busy. Exit Claude in that chat first.")
            raise C.WinError(error)
        self.handle = handle
        return self

    def __exit__(self, *unused):
        close_handle(self.handle)
        self.handle = None


def private_json(path, value):
    """A private same-directory staging file; ReplaceFileW retains the original file ACL."""
    import msvcrt
    tmp = path.with_name(".claude-account-" + uuid.uuid4().hex + ".tmp")
    raw = json.dumps(value, ensure_ascii=False, indent=2).encode("utf8") + b"\n"
    sd = C.c_void_p()
    if not make_sd("D:P(A;;FA;;;OW)(A;;FA;;;SY)", 1, C.byref(sd), None):
        raise C.WinError(C.get_last_error())
    try:
        sa = SecurityAttributes(C.sizeof(SecurityAttributes), sd, False)
        handle = create_file(str(tmp), 0x40000000, 0, C.byref(sa), 1, 0x80, None)
        if handle == C.c_void_p(-1).value:
            raise C.WinError(C.get_last_error())
    finally:
        local_free(sd)
    try:
        try:
            fd = msvcrt.open_osfhandle(handle, os.O_WRONLY | os.O_BINARY)
        except OSError:
            close_handle(handle)
            raise
        with os.fdopen(fd, "wb") as f:
            f.write(raw)
            f.flush()
            os.fsync(f.fileno())
        if path.exists():
            if not replace_file(str(path), str(tmp), None, 0, None, None):
                raise C.WinError(C.get_last_error())
        else:
            tmp.rename(path)
    finally:
        tmp.unlink(missing_ok=True)
