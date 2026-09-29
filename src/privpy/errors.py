"""Public exceptions contain categories, never private runtime values."""


class PrivateRegionError(Exception):
    pass


class PrivateAccessError(PrivateRegionError):
    pass


class NoActiveRegionError(PrivateRegionError):
    pass


class RegionClosedError(PrivateRegionError):
    pass


class CrossRegionError(PrivateRegionError):
    pass


class UnsupportedSyntaxError(PrivateRegionError):
    pass


class PrivateExecutionError(PrivateRegionError):
    pass


class ResourceLimitError(PrivateExecutionError):
    pass


class ExportError(PrivateRegionError):
    pass
