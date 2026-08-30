from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


def test_installable_manifest_allows_setup_before_credentials_exist():
    manifest = yaml.safe_load((ROOT / "plugin.yaml").read_text())
    assert manifest["name"] == "artifact-relay"
    # Hermes' Git installer currently accepts v1 manifests; v2 metadata such as
    # api_version and config_schema is additive and supported by the runtime.
    assert manifest["manifest_version"] == 1
    assert manifest["api_version"] == 1
    assert manifest["license"] == "MIT"
    assert manifest["homepage"] == "https://github.com/eloktev/hermes-artifact-relay"
    assert manifest["platforms"] == ["linux", "macos", "windows"]
    assert manifest["provides_tools"] == ["artifact_read", "artifact_publish"]
    assert "requires_env" not in manifest
    assert manifest["config_schema"]["base_url"]["type"] == "str"
    assert manifest["config_schema"]["base_url"]["required"] is False


def test_repository_contains_open_source_operational_docs():
    for name in ("README.md", "LICENSE", "SECURITY.md", ".github/workflows/ci.yml"):
        assert (ROOT / name).is_file(), name
    workflow = (ROOT / ".github/workflows/ci.yml").read_text()
    for runner in ("ubuntu-latest", "macos-latest", "windows-latest"):
        assert runner in workflow
    assert "NousResearch/hermes-agent" in workflow
    assert "scripts/ci_smoke.py" in workflow
    assert "scripts/assert_no_build.py" in workflow
    assert (ROOT / "scripts" / "assert_no_build.py").is_file()
    assert workflow.index("run: uv run pytest -q") < workflow.index(
        "repository: NousResearch/hermes-agent"
    )
    assert (
        "hermes plugins install eloktev/hermes-artifact-relay" in (ROOT / "README.md").read_text()
    )


def test_repository_is_git_distributed_not_a_partial_python_wheel():
    project = (ROOT / "pyproject.toml").read_text()
    assert 'build-backend = "no_build_backend"' in project
    assert 'backend-path = ["."]' in project
    assert (ROOT / "no_build_backend.py").is_file()


def test_skill_is_generic_cross_platform_and_automatic():
    skill = (ROOT / "skills" / "artifact-publishing" / "SKILL.md").read_text()
    assert "platforms: [linux, macos, windows]" in skill
    assert "Publish automatically" in skill
    assert "artifact_publish" in skill
    assert "artifact_read" in skill
    assert "Telegram" not in skill
    assert "hermes artifact-relay setup" in skill
    assert "new session" in skill


def test_repository_has_only_the_documented_hosted_control_plane():
    text = "\n".join(
        path.read_text(errors="replace")
        for path in ROOT.rglob("*")
        if path.is_file()
        and ".git" not in path.parts
        and ".venv" not in path.parts
        and "tests" not in path.parts
    )
    assert 'DEFAULT_CONTROL_PLANE = "https://relay.lok-labs.com"' in text
    for forbidden in ("Egor's", "artifacts.lok", "eloktev@"):
        assert forbidden not in text


def test_readme_documents_safe_hosted_setup_and_status():
    readme = (ROOT / "README.md").read_text()
    assert "hermes artifact-relay setup" in readme
    assert "hermes artifact-relay status" in readme
    assert "https://relay.lok-labs.com" in readme
    assert "does not restart" in readme
    assert "never prints" in readme
