import copy
import unittest

from hypothesis import strategies as st
from hypothesis.stateful import Bundle, RuleBasedStateMachine, invariant, rule

from privpy import PrivateRegion, PrivateAccessError, CrossRegionError, RegionClosedError
from tests import fixtures as f
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
