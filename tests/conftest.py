"""Shared Home Assistant test fixtures."""

import pytest


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(recorder_db_url, enable_custom_integrations):
    """Prepare Recorder first, then allow loading this custom integration."""
    yield
