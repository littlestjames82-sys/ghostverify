"""Tests for the mini fixture project."""

from app import checkout


def test_checkout():
    assert checkout([10.0]) == "$10.80"
