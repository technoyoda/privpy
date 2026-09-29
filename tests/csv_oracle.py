"""Version-independent minimal CSV quoting using Python's standard writer."""
import csv
import io


def csv_bytes(rows):
    rows = list(rows)
    characters = {char for row in rows for value in row for char in str(value)}
    # Older CPython uses NUL internally for an unset escapechar, which forces
    # quoting or raises an error for NUL fields. An absent, non-NUL escape character
    # avoids that quirk without changing or excluding any generated input.
    escape = next(chr(code) for code in range(0xE000, 0x110000)
                  if chr(code) not in characters)
    output = io.StringIO(newline="")
    csv.writer(output, escapechar=escape).writerows(rows)
    return output.getvalue().encode("utf-8")
