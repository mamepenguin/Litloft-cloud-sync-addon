from __future__ import annotations

import pytest
from pydantic import ValidationError

from addons.cloud_sync.schemas import SyncConfig

MAPPING = {"drive": "Photos", "remote": "gdrive:photos"}


def test_a_config_without_max_delete_gets_the_default():
    assert SyncConfig(mappings=[MAPPING]).max_delete == 200


def test_max_delete_is_read_from_the_config():
    assert SyncConfig(mappings=[MAPPING], max_delete=5).max_delete == 5


@pytest.mark.parametrize("value", [0, -1, "many", 1.5, None, True, "5", 5.0])
def test_max_delete_must_be_a_positive_integer(value):
    with pytest.raises(ValidationError):
        SyncConfig(mappings=[MAPPING], max_delete=value)
