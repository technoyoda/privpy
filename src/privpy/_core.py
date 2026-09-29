import contextvars
import inspect
from pathlib import Path
import textwrap
import threading
import uuid
import weakref

from ._compiler import Compiler
from ._native import native
from ._wire import encode
from .errors import (
    CrossRegionError, NoActiveRegionError, PrivateAccessError, RegionClosedError,
    UnsupportedSyntaxError,
)

_active = contextvars.ContextVar("privpy", default=None)
_reference_token = object()


class PrivateRef:
    """Opaque reference. Its slots contain ownership and a native handle, never plaintext."""
    __slots__ = ("_owner", "_handle")
    __hash__ = None

    def __init__(self, token, owner, handle):
        if token is not _reference_token:
            raise TypeError("Private references are created by the runtime")
        self._owner = weakref.ref(owner)
        self._handle = handle

    def __repr__(self):
        return "<PrivateRef>"

    __str__ = __repr__

    def __format__(self, spec):
        if spec:
            self._deny()
        return repr(self)

    def _deny(self, *args, **kwargs):
        raise PrivateAccessError("Private contents are accessible only in protected functions or explicit export")

    __bool__ = __len__ = __iter__ = __next__ = _deny
    __int__ = __float__ = __bytes__ = __index__ = _deny
    __getitem__ = __setitem__ = __contains__ = _deny
    __eq__ = __ne__ = __lt__ = __le__ = __gt__ = __ge__ = _deny
    __add__ = __radd__ = __sub__ = __rsub__ = __mul__ = __rmul__ = _deny
    __truediv__ = __rtruediv__ = __floordiv__ = __mod__ = _deny
    __reduce__ = __reduce_ex__ = __getattr__ = _deny

    def __copy__(self):
        return self

    def __deepcopy__(self, memo):
        return self


class PrivateFunction:
    _is_private_function = True

    def __init__(self, function):
        if not inspect.isfunction(function) or inspect.iscoroutinefunction(function):
            raise UnsupportedSyntaxError("private_function requires a synchronous Python function")
        try:
            self._source = textwrap.dedent(inspect.getsource(function))
        except (OSError, TypeError):
            raise UnsupportedSyntaxError("Private functions require inspectable source in a Python file") from None
        self._function = function
        self._signature = inspect.signature(function)
        self._id = uuid.uuid4().hex
        self._compiled = None
        self._dependencies = ()
        self._compile_lock = threading.RLock()
        self.__name__ = function.__name__
        self.__doc__ = function.__doc__
        self.__module__ = function.__module__
        self.__qualname__ = function.__qualname__

    def _compile(self):
        with self._compile_lock:
            if self._compiled is None:
                try:
                    self._compiled, self._dependencies = Compiler(self).compile()
                except RecursionError:
                    raise UnsupportedSyntaxError("Function syntax exceeds the supported nesting depth") from None
        return self._compiled

    def __call__(self, *args, **kwargs):
        region = _active.get()
        if region is None:
            raise NoActiveRegionError("A private function requires an active PrivateRegion")
        return region._invoke(self, args, kwargs)

    def __repr__(self):
        return "<PrivateFunction {}>".format(self.__name__)


def private_function(function):
    return PrivateFunction(function)


class PrivateRegion:
    """Own native data and dispatch protected functions for one lexical lifetime."""
    def __init__(self, *, max_steps=100_000):
        if type(max_steps) is not int or not 1 <= max_steps <= 10_000_000:
            raise ValueError("max_steps must be an integer between 1 and 10000000")
        self._max_steps = max_steps
        self._id = None
        self._entered = False
        self._closed = False
        self._lock = threading.RLock()
        self._registered = set()
        self._token = None
        self._finalizer = None

    def __enter__(self):
        with self._lock:
            if self._entered or self._closed:
                raise RegionClosedError("A region may be entered only once")
            self._native = native()
            self._id = self._native.create(self._max_steps)
            self._entered = True
            self._finalizer = weakref.finalize(self, self._native.close, self._id)
            self._token = _active.set(self)
            return self

    def __exit__(self, exc_type, exc, traceback):
        with self._lock:
            self._require_active()
            # Reset first, so exit in the wrong context cannot silently invalidate another caller.
            _active.reset(self._token)
            try:
                self._finalizer()
            finally:
                self._closed = True
                self._registered.clear()
        return False

    def _require_active(self):
        if self._closed:
            raise RegionClosedError("The region is closed")
        if not self._entered:
            raise NoActiveRegionError("The region has not been entered")
        active = _active.get()
        if active is None:
            raise NoActiveRegionError("The region is not active in this context")
        if active is not self:
            raise CrossRegionError("A different region is active")

    def _check_ref(self, value):
        if type(value) is not PrivateRef:
            raise TypeError("export requires a private reference")
        owner = value._owner()
        if owner is None or owner._closed:
            raise RegionClosedError("The reference belongs to a closed region")
        if owner is not self:
            raise CrossRegionError("The reference belongs to another region")
        return value._handle

    def _register(self, function, visiting=None):
        if function._id in self._registered:
            return
        visiting = set() if visiting is None else visiting
        if function._id in visiting:
            return
        visiting.add(function._id)
        program = function._compile()
        for dependency in function._dependencies:
            self._register(dependency, visiting)
        self._native.register(self._id, function._id, program)
        self._registered.add(function._id)

    def _invoke(self, function, args, kwargs):
        with self._lock:
            self._require_active()
            bound = function._signature.bind(*args, **kwargs)
            self._register(function)
            encoded = []
            for parameter in function._signature.parameters:
                value = bound.arguments[parameter]
                if type(value) is PrivateRef:
                    encoded.append({"ref": self._check_ref(value)})
                else:
                    encoded.append({"value": encode(value)})
            handle = self._native.call(self._id, function._id, encoded)
            return PrivateRef(_reference_token, self, handle)

    def export(self, value, *, to=None, transform=None):
        with self._lock:
            self._require_active()
            self._check_ref(value)
            if to is not None:
                if not isinstance(to, Path):
                    raise TypeError("to must be an absolute pathlib.Path or None")
                if not to.is_absolute() or ".." in to.parts or "\0" in str(to):
                    raise ValueError("to must be an absolute path without parent traversal or null bytes")
            if transform is not None:
                if type(transform) is not PrivateFunction:
                    raise TypeError("transform must be a private_function")
                value = self._invoke(transform, (value,), {})
            handle = self._check_ref(value)
            if to is None:
                return self._native.export_value(self._id, handle)
            self._native.export_file(self._id, handle, str(to))
            return None
