import sys
from pathlib import Path

# Make the repo importable when running `pytest` from anywhere.
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import pytest  # noqa: E402

CONFIGS = REPO_ROOT / "configs"


@pytest.fixture
def repo_root() -> Path:
    return REPO_ROOT


@pytest.fixture
def sharc_base_protocol() -> str:
    return (CONFIGS / "protocol" / "sharc_uv_one_well.yaml").read_text()


@pytest.fixture
def asmi_base_protocol() -> str:
    return (CONFIGS / "protocol" / "asmi_indentation_test.yaml").read_text()


@pytest.fixture
def sharc_gantry_yaml() -> str:
    return (CONFIGS / "gantry" / "sharc_gantry.yaml").read_text()


@pytest.fixture
def sharc_deck_yaml() -> str:
    return (CONFIGS / "deck" / "sharc_deck.yaml").read_text()


@pytest.fixture
def asmi_gantry_yaml() -> str:
    return (CONFIGS / "gantry" / "asmi_gantry.yaml").read_text()


@pytest.fixture
def asmi_deck_yaml() -> str:
    return (CONFIGS / "deck" / "asmi_deck.yaml").read_text()
