import shutil
from pathlib import Path
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from nanobot.cli.commands import app
from nanobot.config.schema import Config
from nanobot.providers.litellm_provider import LiteLLMProvider
from nanobot.providers.openai_codex_provider import _strip_model_prefix
from nanobot.providers.registry import find_by_model

runner = CliRunner()


@pytest.fixture
def mock_paths():
    """Mock config/workspace paths for test isolation."""
    with patch("nanobot.config.loader.get_config_path") as mock_cp, \
         patch("nanobot.config.loader.save_config") as mock_sc, \
         patch("nanobot.config.loader.load_config") as mock_lc, \
         patch("nanobot.utils.helpers.get_workspace_path") as mock_ws:

        base_dir = Path("./test_onboard_data")
        if base_dir.exists():
            shutil.rmtree(base_dir)
        base_dir.mkdir()

        config_file = base_dir / "config.json"
        workspace_dir = base_dir / "workspace"

        mock_cp.return_value = config_file
        mock_ws.return_value = workspace_dir
        mock_sc.side_effect = lambda config: config_file.write_text("{}")

        yield config_file, workspace_dir

        if base_dir.exists():
            shutil.rmtree(base_dir)


def test_onboard_fresh_install(mock_paths):
    """No existing config — should create from scratch."""
    config_file, workspace_dir = mock_paths

    result = runner.invoke(app, ["onboard"])

    assert result.exit_code == 0
    assert "Created config" in result.stdout
    assert "Created workspace" in result.stdout
    assert "nanobot is ready" in result.stdout
    assert config_file.exists()
    assert (workspace_dir / "AGENTS.md").exists()
    assert (workspace_dir / "memory" / "MEMORY.md").exists()


def test_onboard_existing_config_refresh(mock_paths):
    """Config exists, user declines overwrite — should refresh (load-merge-save)."""
    config_file, workspace_dir = mock_paths
    config_file.write_text('{"existing": true}')

    result = runner.invoke(app, ["onboard"], input="n\n")

    assert result.exit_code == 0
    assert "Config already exists" in result.stdout
    assert "existing values preserved" in result.stdout
    assert workspace_dir.exists()
    assert (workspace_dir / "AGENTS.md").exists()


def test_onboard_existing_config_overwrite(mock_paths):
    """Config exists, user confirms overwrite — should reset to defaults."""
    config_file, workspace_dir = mock_paths
    config_file.write_text('{"existing": true}')

    result = runner.invoke(app, ["onboard"], input="y\n")

    assert result.exit_code == 0
    assert "Config already exists" in result.stdout
    assert "Config reset to defaults" in result.stdout
    assert workspace_dir.exists()


def test_onboard_existing_workspace_safe_create(mock_paths):
    """Workspace exists — should not recreate, but still add missing templates."""
    config_file, workspace_dir = mock_paths
    workspace_dir.mkdir(parents=True)
    config_file.write_text("{}")

    result = runner.invoke(app, ["onboard"], input="n\n")

    assert result.exit_code == 0
    assert "Created workspace" not in result.stdout
    assert "Created AGENTS.md" in result.stdout
    assert (workspace_dir / "AGENTS.md").exists()


def test_research_init_creates_research_workspace(tmp_path):
    workspace_dir = tmp_path / "research-workspace"

    result = runner.invoke(
        app,
        ["research-init", "--workspace", str(workspace_dir), "--topic", "Interactive theorem proving"],
    )

    assert result.exit_code == 0
    assert "Research workspace ready" in result.stdout
    assert (workspace_dir / "problem.md").exists()
    assert (workspace_dir / "RESEARCH.md").exists()
    assert (workspace_dir / "skills" / "scout-lite" / "SKILL.md").exists()


def test_research_scout_command_reports_summary(tmp_path):
    workspace_dir = tmp_path / "research-scout-workspace"

    class _FakeResult:
        workspace = workspace_dir
        query_expansions = ["graph anomaly detection", "graph anomaly detection survey"]
        unresolved = ["primary metric contract is still unspecified"]
        saved_cards = ["memory/papers/paper-1.md"]
        saved_artifacts = ["artifacts/framing_report/report.md", "artifacts/literature_map/map.md"]
        next_anchor = "gap-finder"

    async def _fake_run(self, inputs):
        return _FakeResult()

    with patch("nanobot.research.scout.ScoutService.run", _fake_run):
        result = runner.invoke(
            app,
            ["research-scout", "--topic", "graph anomaly detection", "--workspace", str(workspace_dir)],
        )

    assert result.exit_code == 0
    assert "Research scout finished" in result.stdout
    assert "Next anchor" in result.stdout
    assert "Unresolved scout questions" in result.stdout


def test_research_command_bootstraps_research_workspace(tmp_path):
    workspace_dir = tmp_path / "research-cli-workspace"
    captured = {}

    class _DummyProvider:
        def get_default_model(self):
            return "dummy/model"

    async def _fake_process_direct(self, *args, **kwargs):
        captured["skill_names"] = kwargs.get("skill_names")
        return "stub research response"

    with patch("nanobot.cli.commands._make_provider", return_value=_DummyProvider()), \
         patch("nanobot.agent.loop.AgentLoop.process_direct", _fake_process_direct):
        result = runner.invoke(
            app,
            [
                "research",
                "--workspace",
                str(workspace_dir),
                "--topic",
                "Graph anomaly detection",
                "--message",
                "Summarize the current framing.",
            ],
        )

    assert result.exit_code == 0
    assert (workspace_dir / "RESEARCH.md").exists()
    assert (workspace_dir / "skills" / "scout-lite" / "SKILL.md").exists()
    assert captured["skill_names"] == ["scout-lite"]


def test_research_command_resolves_gap_finder_when_papers_exist(tmp_path):
    workspace_dir = tmp_path / "research-cli-gap-workspace"
    captured = {}

    class _DummyProvider:
        def get_default_model(self):
            return "dummy/model"

    from nanobot.research.store import ResearchStore
    from nanobot.research.workspace import ResearchWorkspaceService

    ResearchWorkspaceService(workspace_dir).initialize(topic="Graph anomaly detection")
    store = ResearchStore(workspace_dir)
    # Workflow resolver requires at least 3 papers before promoting to gap-finder
    for i in range(1, 4):
        store.save_card(
            "paper",
            {
                "card_type": "paper",
                "card_id": f"paper-{i}",
                "title": f"Graph Anomaly Detection Paper {i}",
                "authors": [],
                "year": 2024,
                "venue": "arXiv",
                "url": f"https://example.com/p{i}",
                "task": "graph anomaly detection",
                "method_family": "graph encoder",
                "core_mechanism": "consistency regularization",
                "contributions": [],
                "limitations": ["Still brittle under noisy edges."],
                "keywords": ["robustness"],
                "source_queries": [],
                "notes": "",
            },
        )

    async def _fake_process_direct(self, *args, **kwargs):
        captured["skill_names"] = kwargs.get("skill_names")
        return "stub research response"

    with patch("nanobot.cli.commands._make_provider", return_value=_DummyProvider()), \
         patch("nanobot.agent.loop.AgentLoop.process_direct", _fake_process_direct):
        result = runner.invoke(
            app,
            [
                "research",
                "--workspace",
                str(workspace_dir),
                "--message",
                "What is the next stage?",
            ],
        )

    assert result.exit_code == 0
    assert captured["skill_names"] == ["gap-finder"]


def test_research_gap_idea_shortlist_commands(tmp_path):
    workspace_dir = tmp_path / "research-flow-workspace"

    runner.invoke(
        app,
        ["research-init", "--workspace", str(workspace_dir), "--topic", "Graph anomaly detection"],
    )

    from nanobot.research.store import ResearchStore

    store = ResearchStore(workspace_dir)
    store.save_card(
        "problem",
        {
            "card_type": "problem",
            "card_id": "problem",
            "title": "Graph anomaly detection",
            "topic": "Graph anomaly detection",
            "objective": "Find a robust innovation point",
            "constraints": [],
            "baselines": ["GADBench"],
            "evaluation_targets": ["AUROC"],
            "user_preferences": ["robustness"],
            "notes": "",
        },
    )
    store.save_card(
        "paper",
        {
            "card_type": "paper",
            "card_id": "paper-1",
            "title": "Robust Graph Anomaly Detection",
            "authors": ["A. Researcher"],
            "year": 2024,
            "venue": "arXiv",
            "url": "https://example.com/robust",
            "task": "graph anomaly detection",
            "method_family": "graph encoder",
            "core_mechanism": "consistency regularization",
            "contributions": ["Strong average performance."],
            "limitations": ["Still brittle under noisy edges and distribution shift."],
            "keywords": ["robustness", "noise"],
            "source_queries": ["graph anomaly detection robustness"],
            "notes": "",
        },
    )

    gap_result = runner.invoke(app, ["research-gaps", "--workspace", str(workspace_dir)])
    idea_result = runner.invoke(app, ["research-ideas", "--workspace", str(workspace_dir)])
    shortlist_result = runner.invoke(app, ["research-shortlist", "--workspace", str(workspace_dir), "--top-k", "1"])

    assert gap_result.exit_code == 0
    assert "Research gaps synthesized" in gap_result.stdout
    assert idea_result.exit_code == 0
    assert "Research ideas generated" in idea_result.stdout
    assert shortlist_result.exit_code == 0
    assert "Research shortlist ready" in shortlist_result.stdout
    assert any((workspace_dir / "memory" / "gaps").glob("*.md"))
    assert any((workspace_dir / "memory" / "ideas").glob("*.md"))
    assert any((workspace_dir / "memory" / "decisions").glob("*.md"))


def test_config_matches_github_copilot_codex_with_hyphen_prefix():
    config = Config()
    config.agents.defaults.model = "github-copilot/gpt-5.3-codex"

    assert config.get_provider_name() == "github_copilot"


def test_config_matches_openai_codex_with_hyphen_prefix():
    config = Config()
    config.agents.defaults.model = "openai-codex/gpt-5.1-codex"

    assert config.get_provider_name() == "openai_codex"


def test_find_by_model_prefers_explicit_prefix_over_generic_codex_keyword():
    spec = find_by_model("github-copilot/gpt-5.3-codex")

    assert spec is not None
    assert spec.name == "github_copilot"


def test_litellm_provider_canonicalizes_github_copilot_hyphen_prefix():
    provider = LiteLLMProvider(default_model="github-copilot/gpt-5.3-codex")

    resolved = provider._resolve_model("github-copilot/gpt-5.3-codex")

    assert resolved == "github_copilot/gpt-5.3-codex"


def test_openai_codex_strip_prefix_supports_hyphen_and_underscore():
    assert _strip_model_prefix("openai-codex/gpt-5.1-codex") == "gpt-5.1-codex"
    assert _strip_model_prefix("openai_codex/gpt-5.1-codex") == "gpt-5.1-codex"
