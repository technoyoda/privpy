import copy
import unittest

from hypothesis import strategies as st
from hypothesis.stateful import Bundle, RuleBasedStateMachine, initialize, invariant, rule

from privpy import PrivateRegion, PrivateAccessError, CrossRegionError, RegionClosedError, PrivateExecutionError
from tests import fixtures as f
from tests import workloads as w
from tests.strategies import values


class RegionMachine(RuleBasedStateMachine):
    """Explore sequences of create, compute, copy, export, nest, close and reopen."""
    references = Bundle("references")

    def __init__(self):
        super().__init__()
        self.region = PrivateRegion()
        self.region.__enter__()
        self.generation = 0
        self.all_refs = []

    @rule(target=references, value=values)
    def create(self, value):
        ref = f.identity(value)
        model = (ref, copy.deepcopy(value), self.generation)
        self.all_refs.append(model)
        return model

    @rule(reference=references)
    def export(self, reference):
        ref, expected, generation = reference
        if generation == self.generation:
            assert self.region.export(ref) == expected
        else:
            with unittest.TestCase().assertRaises(RegionClosedError):
                self.region.export(ref)

    @rule(target=references, reference=references)
    def compute_identity(self, reference):
        ref, expected, generation = reference
        if generation != self.generation:
            with unittest.TestCase().assertRaises(RegionClosedError):
                f.identity(ref)
            return reference
        new_ref = f.identity(ref)
        model = (new_ref, expected, generation)
        self.all_refs.append(model)
        return model

    @rule(reference=references)
    def copy_handle(self, reference):
        assert copy.copy(reference[0]) is reference[0]
        assert copy.deepcopy(reference[0]) is reference[0]

    @rule(reference=references)
    def nested_region_cannot_use_outer_reference(self, reference):
        ref, _, generation = reference
        with PrivateRegion() as inner:
            error = CrossRegionError if generation == self.generation else RegionClosedError
            with unittest.TestCase().assertRaises(error):
                inner.export(ref)
            with unittest.TestCase().assertRaises(error):
                f.identity(ref)

    @rule()
    def close_and_reopen(self):
        self.region.__exit__(None, None, None)
        self.region = PrivateRegion()
        self.region.__enter__()
        self.generation += 1

    @invariant()
    def every_reference_remains_opaque(self):
        for ref, _, _ in self.all_refs:
            assert repr(ref) == "<PrivateRef>"
            with unittest.TestCase().assertRaises(PrivateAccessError):
                bool(ref)

    def teardown(self):
        self.region.__exit__(None, None, None)


TestRegionStateMachine = RegionMachine.TestCase


class LedgerMachine(RuleBasedStateMachine):
    """Branch historical snapshots; failed transfers and host edits cannot rewrite history."""
    ledgers = Bundle("ledgers")

    def __init__(self):
        super().__init__()
        self.region = PrivateRegion()
        self.region.__enter__()
        self.history = []

    @initialize(target=ledgers, initial=st.fixed_dictionaries({
        key: st.integers(-10000, 10000) for key in "abc"
    }))
    def start(self, initial):
        self.total = sum(initial.values())
        entry = (f.identity(initial), dict(initial))
        self.history.append(entry)
        return entry

    @rule(target=ledgers, ledger=ledgers, sender=st.sampled_from(list("abc")),
          recipient=st.sampled_from(list("abc")), amount=st.integers(-1000, 1000))
    def fork_transfer(self, ledger, sender, recipient, amount):
        ref, old = ledger
        expected = dict(old)
        expected[sender] -= amount
        expected[recipient] += amount
        entry = (w.transfer(ref, sender, recipient, amount), expected)
        self.history.append(entry)
        return entry

    @rule(ledger=ledgers, amount=st.integers(-1000, 1000))
    def failed_transfer(self, ledger, amount):
        ref, expected = ledger
        with unittest.TestCase().assertRaises(PrivateExecutionError):
            w.transfer(ref, "a", "absent", amount)
        assert self.region.export(ref) == expected

    @rule(ledger=ledgers)
    def edit_exported_copy(self, ledger):
        ref, expected = ledger
        exported = self.region.export(ref)
        exported.clear()
        assert self.region.export(ref) == expected

    @rule(ledger=ledgers)
    def export_summary(self, ledger):
        assert self.region.export(ledger[0], transform=w.ledger_summary) == {"accounts": 3, "total": self.total}

    @invariant()
    def every_historical_snapshot_is_unchanged(self):
        for ref, expected in self.history:
            assert self.region.export(ref) == expected
            assert sum(expected.values()) == self.total

    def teardown(self):
        self.region.__exit__(None, None, None)


TestLedgerStateMachine = LedgerMachine.TestCase
