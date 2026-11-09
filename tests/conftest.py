"""Load pure modules without importing the HA entry point."""

import importlib.util
from pathlib import Path
import sys
from types import ModuleType


if importlib.util.find_spec("homeassistant") is None:
    package = ModuleType("custom_components.mazda_6e")
    package.__path__ = [str(Path(__file__).parents[1] / "custom_components" / "mazda_6e")]
    sys.modules.setdefault(package.__name__, package)
