import hashlib
import linecache

from privpy import PrivateRegion, private_function
from privpy.intrinsics import read_json, read_text, read_bytes, json_bytes, csv_bytes, utf8_bytes


def make_function(body, parameters="value", extra=None):
    """Compile generated test source without touching user files or invoking unknown code."""
    source = "@private_function\ndef generated(" + parameters + "):\n"
    source += "".join("    " + line + "\n" for line in body.splitlines())
    filename = "<property-program-" + hashlib.sha256(source.encode()).hexdigest() + ">"
    linecache.cache[filename] = (len(source), None, source.splitlines(True), filename)
    namespace = {
        "private_function": private_function, "read_json": read_json, "read_text": read_text,
        "read_bytes": read_bytes, "json_bytes": json_bytes, "csv_bytes": csv_bytes,
        "utf8_bytes": utf8_bytes,
    }
    namespace.update(extra or {})
    exec(compile(source, filename, "exec"), namespace)
    return namespace["generated"]


def run(function, *args, **kwargs):
    with PrivateRegion() as region:
        return region.export(function(*args, **kwargs))
