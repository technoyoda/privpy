"""Supported operations whose implementations execute entirely in native code."""
from .errors import PrivateAccessError


class _Intrinsic:
    __slots__ = ("name",)

    def __init__(self, name):
        self.name = name

    def __call__(self, *args, **kwargs):
        raise PrivateAccessError("This operation is available only inside a private function")

    def __repr__(self):
        return "<PrivateIntrinsic>"


read_json = _Intrinsic("read_json")
read_text = _Intrinsic("read_text")
read_bytes = _Intrinsic("read_bytes")
json_bytes = _Intrinsic("json_bytes")
csv_bytes = _Intrinsic("csv_bytes")
utf8_bytes = _Intrinsic("utf8_bytes")
