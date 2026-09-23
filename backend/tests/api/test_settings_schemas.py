import pytest
from pydantic import SecretStr, TypeAdapter, ValidationError

from backend.app.api.schemas import EventView, Gid
from backend.app.config import ROOT, Settings
from backend.tests.api.fakes import GIDS, NOW


@pytest.mark.parametrize(
    "setting,value",
    [
        ("backend_port", 0),
        ("backend_port", 65536),
        ("openai_timeout_seconds", 0),
        ("openai_max_tool_calls", 8),
        ("openai_model", ""),
        ("sse_poll_seconds", 0),
        ("database_url", "postgres://localhost/db"),
        ("database_url", "sqlite:///:memory:"),
        ("cors_origins", "*"),
        ("cors_origins", "file:///private"),
        ("cors_origins", "http://host/path"),
        ("cors_origins", "http://user:password@host"),
        ("app_env", "unknown"),
        ("api_max_active_runs", 0),
        ("sse_max_streams", 0),
    ],
)
def test_settings_reject_invalid_values(setting, value):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **{setting: value})


def test_env_loading_relative_paths_and_secret_hiding(tmp_path, monkeypatch):
    monkeypatch.setenv("DEMO_MODE", "false")
    monkeypatch.setenv("OPENAI_API_KEY", "secret-fixture-key")
    monkeypatch.setenv("CORS_ORIGINS", "http://localhost:5173,https://app.example")
    settings = Settings(_env_file=None, data_dir="data", artifacts_dir="artifacts")
    assert settings.demo_mode is False
    assert settings.origins == ["http://localhost:5173", "https://app.example"]
    assert settings.data_dir == ROOT / "data"
    assert settings.database_path == ROOT / "aml_agent.db"
    assert isinstance(settings.openai_api_key, SecretStr)
    assert "secret-fixture-key" not in repr(settings)
    assert "secret-fixture-key" not in settings.model_dump_json()


@pytest.mark.parametrize(
    "value", [9007199254741000, 9.007199254741e15, True, "01", "-1", "9223372036854775808", "1e17"]
)
def test_gid_rejects_lossy_or_noncanonical_values(value):
    with pytest.raises(ValidationError):
        TypeAdapter(Gid).validate_python(value)


def test_gid_preserves_every_digit():
    assert TypeAdapter(Gid).validate_python(GIDS[0]) == GIDS[0]
    assert TypeAdapter(Gid).validate_python("9223372036854775807") == "9223372036854775807"


def test_trace_rejects_arbitrary_tool_names_and_raw_metadata():
    from uuid import uuid4

    base = dict(
        event_id=uuid4(),
        run_id=uuid4(),
        sequence=1,
        kind="tool_completed",
        summary="Safe",
        created_at=NOW,
    )
    with pytest.raises(ValidationError):
        EventView(**base, tool_name="shell")
    with pytest.raises(ValidationError):
        EventView(**base, payload_json={"prompt": "raw internal output"})
