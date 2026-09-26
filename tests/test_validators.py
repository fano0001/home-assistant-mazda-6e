import pytest

from custom_components.mazda_6e.helpers.validators import temperature


@pytest.mark.parametrize("raw", [None, 0, "0"])
def test_temperature_treats_zero_as_unavailable(raw):
    assert temperature(raw) is None


def test_temperature_converts_tenths_of_a_degree():
    assert temperature(215) == 21.5