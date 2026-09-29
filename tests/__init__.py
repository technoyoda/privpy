import os
from pathlib import Path

from hypothesis import settings
from hypothesis.database import DirectoryBasedExampleDatabase

database = DirectoryBasedExampleDatabase(
    Path(os.environ.get("PRIVPY_TEST_STATE", ".hypothesis")) / "examples"
)
settings.register_profile("dev", max_examples=200, deadline=None, print_blob=True, database=database)
settings.register_profile("ci", max_examples=1000, deadline=None, print_blob=True, derandomize=True)
settings.register_profile("stress", max_examples=10000, deadline=None, print_blob=True, database=database)
settings.load_profile(os.environ.get("PRIVPY_HYPOTHESIS_PROFILE", "dev"))
