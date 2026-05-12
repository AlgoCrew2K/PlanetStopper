"""
Pytest configuration and shared fixtures for AlphaBot test suite.
"""

import pathlib

import pytest


@pytest.fixture
def fixtures_dir() -> pathlib.Path:
    """Return the absolute path to the tests/fixtures directory."""
    return pathlib.Path(__file__).parent / "fixtures"
