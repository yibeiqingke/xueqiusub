from unittest.mock import patch
from app.llm_client import resolve_llm_channels, SDK_MAX_RETRIES

def test_sdk_max_retries():
    assert SDK_MAX_RETRIES >= 3

def test_resolve_llm_channels_mock():
    with patch("app.appconfig.get_cfg", side_effect=lambda key, default: default):
        channels = resolve_llm_channels(user=None)
        assert isinstance(channels, list)
