import copy
import json
from pathlib import Path

import pytest
import yaml

from leadpipe.config import Config

REPO = Path(__file__).resolve().parents[1]
EXAMPLE = REPO / "config.example.yaml"
FIXTURES = REPO / "tests" / "fixtures" / "listings.json"


@pytest.fixture(scope="session")
def example_data():
    return yaml.safe_load(EXAMPLE.read_text(encoding="utf-8"))


@pytest.fixture
def cfg(example_data):
    """A fresh Config off the shipped example, safe to mutate per test."""
    return Config(copy.deepcopy(example_data), EXAMPLE)


@pytest.fixture(scope="session")
def listings():
    return json.loads(FIXTURES.read_text(encoding="utf-8"))
