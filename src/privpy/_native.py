import ctypes
import json
import os
from pathlib import Path
import sys
import threading

from .errors import ExportError, PrivateExecutionError, RegionClosedError, ResourceLimitError
from ._wire import decode


class Native:
    def __init__(self):
        name = "_runtime.dylib" if sys.platform == "darwin" else "_runtime.so"
        path = Path(os.environ.get("PRIVPY_NATIVE", str(Path(__file__).parent / name)))
        if not path.is_file():
            raise RuntimeError("Native runtime is not built. Run: python -m privpy.build")
        self.lib = ctypes.CDLL(str(path.resolve()))
        signatures = {
            "pr_last_error": ([], ctypes.c_char_p),
            "pr_create": ([ctypes.c_int64], ctypes.c_uint64),
            "pr_close": ([ctypes.c_uint64], ctypes.c_int),
            "pr_register": ([ctypes.c_uint64, ctypes.c_char_p, ctypes.c_char_p], ctypes.c_int),
            "pr_call": ([ctypes.c_uint64, ctypes.c_char_p, ctypes.c_char_p], ctypes.c_uint64),
            "pr_export_value": ([ctypes.c_uint64, ctypes.c_uint64], ctypes.c_void_p),
            "pr_export_file": ([ctypes.c_uint64, ctypes.c_uint64, ctypes.c_char_p], ctypes.c_int),
            "pr_free": ([ctypes.c_void_p], None),
        }
        for name, (arguments, result) in signatures.items():
            function = getattr(self.lib, name)
            function.argtypes = arguments
            function.restype = result

    def check(self, result):
        if result:
            return result
        code = self.lib.pr_last_error().decode("ascii")
        if code == "ClosedRegion":
            raise RegionClosedError("The region is closed")
        if code == "ResourceLimit":
            raise ResourceLimitError("Private computation exceeded a resource limit")
        if code == "FileExists":
            raise FileExistsError("Export destination already exists")
        if code in {"ExportTypeError", "InvalidDestination", "OutputError"}:
            raise ExportError("Export failed: " + code)
        raise PrivateExecutionError("Private computation failed: " + code)

    def create(self, max_steps):
        return self.check(self.lib.pr_create(max_steps))

    def close(self, region_id):
        self.check(self.lib.pr_close(region_id))

    def register(self, region_id, function_id, program):
        self.check(self.lib.pr_register(region_id, function_id.encode("ascii"), program.encode("ascii")))

    def call(self, region_id, function_id, arguments):
        payload = json.dumps(arguments, ensure_ascii=True, separators=(",", ":")).encode("ascii")
        return self.check(self.lib.pr_call(region_id, function_id.encode("ascii"), payload))

    def export_value(self, region_id, reference):
        pointer = self.check(self.lib.pr_export_value(region_id, reference))
        try:
            # This is the explicit, intentional plaintext crossing to ordinary Python.
            return decode(json.loads(ctypes.string_at(pointer).decode("utf-8")))
        finally:
            self.lib.pr_free(pointer)

    def export_file(self, region_id, reference, path):
        self.check(self.lib.pr_export_file(region_id, reference, os.fsencode(path)))


_instance = None
_lock = threading.Lock()


def native():
    global _instance
    with _lock:
        if _instance is None:
            _instance = Native()
        return _instance
