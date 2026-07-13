from pathlib import Path

from focusproof.config.env import get_env_status, load_project_env
from focusproof.openhands_adapter.llm_config import build_openhands_llm_config, get_llm_config_status


def test_missing_env_file_returns_missing_status_without_secrets(tmp_path: Path) -> None:
    values = load_project_env(tmp_path)
    status = get_env_status(tmp_path)

    assert values == {}
    assert status["envFileExists"] is False
    assert status["hasOpenAIKey"] is False
    assert status["hasDashScopeKey"] is False
    assert "apiKey" not in status


def test_fake_env_detects_keys_and_model_without_leaking_values(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text(
        "OPENAI_API_KEY=sk-test-secret\n"
        "OPENAI_BASE_URL=https://example.test/v1\n"
        "OPENHANDS_LLM_MODEL=qwen-plus\n",
        encoding="utf-8",
    )

    values = load_project_env(tmp_path)
    status = get_env_status(tmp_path)

    assert values["OPENAI_API_KEY"] == "sk-test-secret"
    assert status["hasOpenAIKey"] is True
    assert status["hasBaseUrl"] is True
    assert status["model"] == "qwen-plus"
    assert "sk-test-secret" not in repr(status)


def test_powershell_env_syntax_is_reported_but_not_loaded_as_dotenv(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text('$env:OPENAI_API_KEY="sk-powershell"\n', encoding="utf-8")

    status = get_env_status(tmp_path)

    assert status["envFileExists"] is True
    assert status["hasPowerShellEnvSyntax"] is True
    assert status["dotenvFormatValid"] is False
    assert status["hasOpenAIKey"] is False


def test_llm_config_status_reports_missing_fields_without_secret(tmp_path: Path) -> None:
    status = get_llm_config_status(tmp_path)

    assert status["canBuildConfig"] is False
    assert "credential" in status["missingFields"]
    assert "apiKey" not in status


def test_build_openhands_llm_config_uses_openai_compatible_env(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text(
        "DASHSCOPE_API_KEY=ds-secret\n"
        "OPENAI_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1\n"
        "DASHSCOPE_MODEL=qwen-turbo\n",
        encoding="utf-8",
    )

    config = build_openhands_llm_config(tmp_path)
    status = get_llm_config_status(tmp_path)

    assert config is not None
    assert config.model == "qwen-turbo"
    assert config.api_key.get_secret_value() == "ds-secret"
    assert status["canBuildConfig"] is True
    assert status["providerHint"] == "dashscope-openai-compatible"
    assert "ds-secret" not in repr(status)
