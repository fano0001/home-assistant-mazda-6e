import pytest

from custom_components.mazda_6e.helpers.validators import temperature, tire_warning_label


@pytest.mark.parametrize("raw", [None, 0, "0"])
def test_temperature_treats_zero_as_unavailable(raw):
    assert temperature(raw) is None


def test_temperature_converts_tenths_of_a_degree():
    assert temperature(215) == 21.5

@pytest.mark.parametrize("code,language,expected", [
    (0, "da", "OK"), (2, "da", "Lavt tryk"), (1, "da", "Ukendt"),
    (None, "da", "Ukendt"), (3, "da", "Ukendt"), (False, "da", "Ukendt"),
    (0, "en", "OK"), (2, "en", "Low pressure"), (1, "en", "Unknown"),
    (2, "de", "Niedriger Druck"), (2, "da-DK", "Lavt tryk"),
    (2, "fr", "Low pressure"),
])
def test_tire_warning_labels(code, language, expected):
    assert tire_warning_label(code, language) == expected
