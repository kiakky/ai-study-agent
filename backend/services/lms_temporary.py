"""Per-read private scratch space, owned by the authorized retrieval adapter.

The authorized retrieval adapter, never model arguments, would own this object.
It accepts no returned path: the upstream bridge must use its fixed filename.
"""
from contextlib import contextmanager
import csv
import os
from pathlib import Path
import shutil
import stat
import subprocess
import tempfile

from services.document_extraction import MAX_FILE_BYTES, extract_document

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCRATCH_ROOT = PROJECT_ROOT / '.private-lms' / 'temporary-downloads'
FILENAME = 'material'
SUPPORTED_TYPES = frozenset({'application/pdf',
    'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    'application/vnd.openxmlformats-officedocument.presentationml.presentation'})


def plain_directory(path):
    info = path.lstat()
    if (not stat.S_ISDIR(info.st_mode) or path.is_symlink()
            or getattr(info, 'st_file_attributes', 0) & 0x400):
        raise ValueError('Scratch directory is not a plain directory.')


def protect(path):
    if os.name == 'nt':
        result = subprocess.run(['whoami.exe', '/user', '/fo', 'csv', '/nh'],
                                capture_output=True, text=True, check=True)
        sid = next(csv.reader([result.stdout.strip()]))[1]
        if not sid.startswith('S-1-5-') or not all(c in 'S-0123456789' for c in sid):
            raise ValueError('Current Windows identity is unavailable.')
        # mkdtemp on Windows can add explicit Administrators/OWNER RIGHTS ACEs.
        # Removing inheritance alone does not remove those explicit grants.
        # Replace this scratch directory's DACL, never system/account settings.
        import ctypes
        from ctypes import wintypes
        api=ctypes.WinDLL('advapi32',use_last_error=True)
        kernel=ctypes.WinDLL('kernel32',use_last_error=True)
        pointer=ctypes.c_void_p
        api.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes=[
            wintypes.LPCWSTR,wintypes.DWORD,ctypes.POINTER(pointer),ctypes.POINTER(wintypes.ULONG)]
        api.ConvertStringSecurityDescriptorToSecurityDescriptorW.restype=wintypes.BOOL
        api.GetSecurityDescriptorDacl.argtypes=[pointer,ctypes.POINTER(wintypes.BOOL),
                                               ctypes.POINTER(pointer),ctypes.POINTER(wintypes.BOOL)]
        api.GetSecurityDescriptorDacl.restype=wintypes.BOOL
        api.SetNamedSecurityInfoW.argtypes=[wintypes.LPWSTR,wintypes.DWORD,wintypes.DWORD,
                                           pointer,pointer,pointer,pointer]
        api.SetNamedSecurityInfoW.restype=wintypes.DWORD
        kernel.LocalFree.argtypes=[pointer];kernel.LocalFree.restype=pointer
        descriptor=pointer()
        if not api.ConvertStringSecurityDescriptorToSecurityDescriptorW(
                f'D:P(A;OICI;FA;;;SY)(A;OICI;FA;;;{sid})',1,ctypes.byref(descriptor),None):
            raise ValueError('Private scratch permissions could not be established.')
        try:
            acl=pointer();present=wintypes.BOOL();defaulted=wintypes.BOOL()
            if not api.GetSecurityDescriptorDacl(descriptor,ctypes.byref(present),ctypes.byref(acl),ctypes.byref(defaulted)) or not present:
                raise ValueError('Private scratch permissions could not be established.')
            if api.SetNamedSecurityInfoW(str(path),1,0x80000004,None,None,acl,None):
                raise ValueError('Private scratch permissions could not be established.')
        finally:
            kernel.LocalFree(descriptor)
    else:
        path.chmod(0o700)


class ScratchRead:
    def __init__(self, directory):
        self.directory = directory
        self.target = directory / FILENAME

    def read_bytes(self, media_type):
        if media_type not in SUPPORTED_TYPES:
            raise ValueError('Temporary extraction supports PDF, DOCX and PPTX only.')
        plain_directory(self.directory)
        info = self.target.lstat()
        if (not stat.S_ISREG(info.st_mode) or self.target.is_symlink() or info.st_nlink != 1
                or getattr(info, 'st_file_attributes', 0) & 0x400
                or not 0 < info.st_size <= MAX_FILE_BYTES):
            raise ValueError('Scratch file is not an acceptable bounded regular file.')
        with self.target.open('rb') as handle:
            opened = os.fstat(handle.fileno())
            if (opened.st_ino, opened.st_dev) != (info.st_ino, info.st_dev):
                raise ValueError('Scratch file changed before reading.')
            value = handle.read(MAX_FILE_BYTES+1)
        signature=b'%PDF-' if media_type=='application/pdf' else b'PK\x03\x04'
        if len(value) > MAX_FILE_BYTES or not value.startswith(signature):
            raise ValueError('Scratch document size or signature is invalid.')
        return value

    def extract_pdf(self):
        return extract_document(self.read_bytes('application/pdf'), 'application/pdf')


@contextmanager
def private_scratch(root=None):
    """Create/protect one random job and remove only that job on normal exit.

    root override is for isolated tests only. Existing siblings are never scanned
    or deleted. A crash can leave this job behind: no broad cleanup on startup.
    """
    root = Path(root) if root is not None else SCRATCH_ROOT
    root = root.absolute()
    # Check existing ancestors before mkdir so a link cannot redirect creation.
    for parent in reversed((root, *root.parents)):
        if parent.exists() or parent.is_symlink():
            plain_directory(parent)
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    plain_directory(root)
    protect(root)
    job = Path(tempfile.mkdtemp(prefix='read-', dir=root))
    identity = job.stat()
    try:
        protect(job)
        yield ScratchRead(job)
    finally:
        # Refuse cleanup if someone replaced the job itself. Python's rmtree
        # does not traverse directory symlinks / Windows junctions within it.
        plain_directory(root)
        plain_directory(job)
        current = job.stat()
        if (current.st_ino, current.st_dev) != (identity.st_ino, identity.st_dev):
            raise ValueError('Scratch job changed; automatic cleanup refused.')
        if job.parent != root or not job.name.startswith('read-'):
            raise ValueError('Scratch cleanup escaped its job.')
        shutil.rmtree(job)
