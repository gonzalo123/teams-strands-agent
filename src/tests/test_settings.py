import base64

import pytest
from pydantic import ValidationError

from teams_agent.settings import Settings


@pytest.mark.parametrize(
    "invalid_key",
    [base64.b64encode(b"x" * 32).decode() + "=", "not-base64!", "", "aGVsbG8=\n"],
)
def test_invalid_hmac_identifies_field_without_exposing_configuration(settings, invalid_key):
    values = settings.model_dump()
    values["teams_hmac_secret"] = invalid_key
    with pytest.raises(ValidationError) as caught:
        Settings(_env_file=None, **values)
    assert caught.value.errors()[0]["loc"] == ("teams_hmac_secret",)
    message = str(caught.value)
    assert "TEAMS_HMAC_SECRET" in message
    assert "input_value" not in message
    assert settings.teams_workflow_url.get_secret_value() not in message


def test_model_validation_also_hides_configuration(settings):
    values = settings.model_dump()
    values["teams_workflow_url"] = "http://example.test/workflow?sig=private"
    with pytest.raises(ValidationError) as caught:
        Settings(_env_file=None, **values)
    assert "HTTPS" in str(caught.value)
    assert "input_value" not in str(caught.value)
    assert "sig=private" not in str(caught.value)
