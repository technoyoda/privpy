from hypothesis import strategies as st

text = st.text(alphabet=st.characters(blacklist_categories=("Cs",)), max_size=80)
small_int = st.integers(-1_000_000, 1_000_000)
scalar = st.one_of(st.none(), st.booleans(), small_int,
                   st.floats(min_value=-1e6, max_value=1e6, allow_nan=False, allow_infinity=False),
                   text, st.binary(max_size=100))
values = st.recursive(
    scalar,
    lambda children: st.one_of(st.lists(children, max_size=5),
                              st.dictionaries(text, children, max_size=5)),
    max_leaves=25,
)
json_scalar = st.one_of(st.none(), st.booleans(), small_int,
                        st.floats(min_value=-1e6, max_value=1e6, allow_nan=False, allow_infinity=False), text)
json_values = st.recursive(
    json_scalar,
    lambda children: st.one_of(st.lists(children, max_size=5), st.dictionaries(text, children, max_size=5)),
    max_leaves=25,
)
rows = st.lists(st.fixed_dictionaries({
    "country": text,
    "amount": st.integers(-100_000, 100_000),
    "status": st.sampled_from(["paid", "pending", "cancelled"]),
}), max_size=35)

# Expressions carry their own Python oracle; generated code contains only this grammar.
expressions = st.recursive(
    st.one_of(st.just("x"), st.integers(-10, 10).map(repr)),
    lambda children: st.one_of(
        st.tuples(children, st.sampled_from(["+", "-", "*"]), children)
          .map(lambda parts: "(" + parts[0] + parts[1] + parts[2] + ")"),
        children.map(lambda value: "(-(" + value + "))"),
    ),
    max_leaves=8,
)
