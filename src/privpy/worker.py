"""Local experiment: the existing region API backed by a native subprocess.

Use: from privpy.worker import PrivateRegion
Build first: python -m privpy.build --worker
"""
from ._core import PrivateRegion as _PrivateRegion
from ._worker import WorkerError, WorkerNative

__all__ = ["PrivateRegion", "WorkerError"]


class PrivateRegion(_PrivateRegion):
    def _make_native(self):
        return WorkerNative()

    @property
    def worker_pid(self):
        """Public process metadata for studying the experiment, not a data handle."""
        return self._native.pid if self._entered else None

    @property
    def worker_protections(self):
        """Return a copy of the startup report; not proof of complete isolation."""
        return dict(self._native.protections) if self._entered else {}
