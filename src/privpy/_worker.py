"""Parent side of the experimental bridge. No private interpreter runs here.

Frames: 4-byte unsigned big-endian length, followed by UTF-8 JSON.
The worker resolves handles in its own region. Only export_value returns data.
"""
import json
import os
from pathlib import Path
import socket
import struct
import subprocess
import threading
import time

from ._native import raise_native_error
from ._wire import decode
from .errors import PrivateExecutionError, ResourceLimitError

MAX_FRAME = 32 * 1024 * 1024
ERROR_CODES = {
    "ClosedRegion", "ResourceLimit", "FileExists", "ExportTypeError", "InvalidDestination",
    "OutputError", "InvalidData", "InvalidProgram", "InvalidRequest", "InvalidConfiguration",
    "InvalidReference", "UnknownFunction", "DuplicateFunction", "NameError", "TypeError",
    "ValueError", "IndexError", "KeyError", "NumericError", "InputError", "InternalError",
    "WorkerSetup",
}


class WorkerError(PrivateExecutionError):
    """The worker stopped, timed out, or sent an invalid protocol response."""


def worker_path():
    return Path(os.environ.get("PRIVPY_WORKER", str(Path(__file__).with_name("_worker_runtime")))).resolve()


class WorkerNative:
    def __init__(self, *, executable=None, timeout=30.0):
        self._owner_pid = os.getpid()
        self._lock = threading.RLock()
        self._timeout = timeout
        self._stopped = False
        self._region = None
        self._process = None
        path = Path(executable).resolve() if executable is not None else worker_path()
        if not path.is_file():
            raise WorkerError("Worker is not built. Run: python -m privpy.build --worker")
        self._socket, child = socket.socketpair()
        try:
            # No shell, loader injection environment, inherited standard streams,
            # or unrelated descriptors. Only this connection reaches the worker.
            self._process = subprocess.Popen(
                [str(path), "--fd", str(child.fileno())], pass_fds=(child.fileno(),),
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                close_fds=True, start_new_session=True,
                env={"PATH": os.defpath, "LANG": "C", "LC_ALL": "C"},
            )
            child.close()
            self.pid = self._process.pid
            ready = self._read_frame(time.monotonic() + self._timeout)
            if (type(ready) is not dict or ready.get("protocol") != 1
                    or ready.get("pid") != self.pid
                    or ready.get("core_dumps_disabled") is not True
                    or ready.get("inspection_restriction") not in {"linux-nondumpable", "darwin-deny-attach"}):
                raise WorkerError("Worker could not establish its required startup protections")
            self.protections = ready
        except Exception:
            child.close()
            self._shutdown()
            raise WorkerError("Worker startup failed before a private region was created") from None

    def _set_deadline(self, deadline):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError
        self._socket.settimeout(remaining)

    def _read_exact(self, size, deadline):
        chunks = bytearray()
        while len(chunks) < size:
            self._set_deadline(deadline)
            block = self._socket.recv(min(size - len(chunks), 65536))
            if not block:
                raise EOFError
            chunks.extend(block)
        return bytes(chunks)

    def _read_frame(self, deadline):
        size = struct.unpack(">I", self._read_exact(4, deadline))[0]
        if not 0 < size <= MAX_FRAME:
            raise ValueError("Invalid frame size")
        return json.loads(self._read_exact(size, deadline).decode("utf-8"))

    def _exchange(self, request):
        """One serialized request/reply. Never log request or response contents."""
        if os.getpid() != self._owner_pid:
            raise WorkerError("Worker connections cannot be used from a forked process")
        with self._lock:
            if self._stopped:
                raise WorkerError("The region's worker is no longer available")
            payload = json.dumps(request, ensure_ascii=True, separators=(",", ":")).encode("ascii")
            if len(payload) > MAX_FRAME:
                raise ResourceLimitError("Worker request exceeds the transport limit")
            try:
                deadline = time.monotonic() + self._timeout
                self._set_deadline(deadline)
                self._socket.sendall(struct.pack(">I", len(payload)) + payload)
                reply = self._read_frame(deadline)
                if type(reply) is not dict or type(reply.get("ok")) is not bool:
                    raise ValueError("Invalid response")
                if reply["ok"]:
                    if set(reply) != {"ok", "result"}:
                        raise ValueError("Invalid response")
                    return reply["result"]
                if set(reply) != {"ok", "error"} or reply["error"] not in ERROR_CODES:
                    raise ValueError("Invalid response")
            except (OSError, EOFError, ValueError, TypeError, RecursionError):
                self._shutdown()
                raise WorkerError("Worker connection failed, timed out, or returned an invalid response") from None
            raise_native_error(reply["error"])

    def _require_region(self, region_id):
        if self._region is None or region_id != self._region:
            raise WorkerError("The request does not belong to this worker session")

    def create(self, max_steps):
        try:
            region = self._exchange({"op": "create", "max_steps": max_steps})
            if type(region) is not int or region <= 0:
                raise WorkerError("Invalid worker region response")
            self._region = region
            return region
        except Exception:
            self._shutdown()
            raise

    def register(self, region_id, function_id, program):
        self._require_region(region_id)
        self._exchange({"op": "register", "name": function_id, "program": program})

    def call(self, region_id, function_id, arguments):
        self._require_region(region_id)
        handle = self._exchange({"op": "call", "name": function_id, "arguments": arguments})
        if type(handle) is not int or handle <= 0:
            self._shutdown()
            raise WorkerError("Invalid worker reference response")
        return handle

    def export_value(self, region_id, reference):
        self._require_region(region_id)
        wire = self._exchange({"op": "export_value", "handle": reference})
        try:
            return decode(wire)
        except (ValueError, TypeError, KeyError, IndexError, RuntimeError, RecursionError):
            self._shutdown()
            raise WorkerError("Invalid worker export response") from None

    def export_file(self, region_id, reference, path):
        self._require_region(region_id)
        self._exchange({"op": "export_file", "handle": reference, "path": path})

    def close(self, region_id):
        if os.getpid() != self._owner_pid:
            self._shutdown()
            raise WorkerError("Worker connections cannot be closed from a forked process")
        with self._lock:
            if self._stopped:
                return
            try:
                self._require_region(region_id)
                self._exchange({"op": "close"})
            finally:
                self._shutdown()

    def _shutdown(self):
        self._stopped = True
        self._socket.close()
        process = self._process
        if process is None or os.getpid() != self._owner_pid:
            return
        try:
            process.wait(timeout=0.2)
        except subprocess.TimeoutExpired:
            process.terminate()
            try:
                process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
