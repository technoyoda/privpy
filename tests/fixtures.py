from privpy import private_function
from privpy.intrinsics import read_json, read_text, read_bytes, csv_bytes, json_bytes, utf8_bytes


@private_function
def identity(value):
    return value


@private_function
def add(a, b):
    return a + b


@private_function
def subtract(a, b):
    return a - b


@private_function
def multiply(a, b):
    return a * b


@private_function
def divide(a, b):
    return a / b


@private_function
def floor_divide(a, b):
    return a // b


@private_function
def modulo(a, b):
    return a % b


@private_function
def comparisons(a, b):
    return [a == b, a != b, a < b, a <= b, a > b, a >= b]


@private_function
def equal(a, b):
    return a == b


@private_function
def truth_value(value):
    return bool(value)


@private_function
def length(value):
    return len(value)


@private_function
def item(value, index):
    return value[index]


@private_function
def membership(value, element):
    return [element in value, element not in value]


@private_function
def logical(a, b):
    return [a and b, a or b, not a]


@private_function
def chained(a, b, c):
    return a < b <= c


@private_function
def conditional(value):
    if value > 1000:
        return value * 2
    return value + 50


@private_function
def conditional_expression(value):
    return value * 2 if value > 1000 else value + 50


@private_function
def total(values):
    return sum(values)


@private_function
def total_with_start(values, start):
    return sum(values, start)


@private_function
def extrema(values):
    return [min(values), max(values)]


@private_function
def explicit_sum(values):
    total = 0
    for value in values:
        total += value
    return total


@private_function
def squares(values):
    return [value * value for value in values if value >= 0]


@private_function
def triangular(n):
    total = 0
    i = 0
    while i < n:
        total += i
        i += 1
    return total


@private_function
def make_range(start, stop, step):
    return range(start, stop, step)


@private_function
def factorial(n):
    if n <= 1:
        return 1
    return n * factorial(n - 1)


@private_function
def nested_calls(value):
    return add(identity(value), 7)


@private_function
def aggregate(rows):
    totals = {}
    for row in rows:
        if row["status"] == "paid":
            country = row["country"]
            totals[country] = totals.get(country, 0) + row["amount"]
    return totals


@private_function
def as_rows(totals):
    rows = []
    for country, amount in totals.items():
        rows = rows + [{"country": country, "amount": amount}]
    return rows


@private_function
def as_csv(rows):
    return csv_bytes(rows, ["country", "amount"])


@private_function
def as_json(value):
    return json_bytes(value)


@private_function
def as_utf8(value):
    return utf8_bytes(value)


@private_function
def encode_method(value):
    return value.encode("utf-8")


@private_function
def from_json(path):
    return read_json(path)


@private_function
def from_text(path):
    return read_text(path)


@private_function
def from_bytes(path):
    return read_bytes(path)


@private_function
def update_list(value, index, replacement):
    value[index] = replacement
    return value


@private_function
def update_record(value, key, replacement):
    value[key] = replacement
    return value


@private_function
def dictionary_views(value, key, default):
    return [value.get(key, default), value.keys(), value.values(), value.items()]


@private_function
def fail_private(value):
    return value["missing"]


@private_function
def endless(value):
    while True:
        value += 1
    return value


@private_function
def divide_by_zero(value):
    return value / 0


@private_function
def source_then_length(path):
    value = read_text(path)
    return len(value)


@private_function
def as_string(value):
    return str(value)


@private_function
def any_csv(rows, columns):
    return csv_bytes(rows, columns)
