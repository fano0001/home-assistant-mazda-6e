"""Tire status labels."""

import pytest

from test_asia_status import load_module


@pytest.mark.parametrize("code,language,expected", [
    (0, "da", "OK"), (2, "da", "Lavt tryk"), (1, "da", "Ukendt"),
    (None, "da", "Ukendt"), (3, "da", "Ukendt"), (False, "da", "Ukendt"),
    (0, "en", "OK"), (2, "en", "Low pressure"), (1, "en", "Unknown"),
    (2, "de", "Niedriger Druck"), (2, "da-DK", "Lavt tryk"),
    (2, "fr", "Low pressure"),
])
def test_tire_warning_labels(monkeypatch, code, language, expected):
    module = load_module(monkeypatch, "tire_status_validators", "helpers/validators.py")
    assert module.tire_warning_label(code, language) == expected
