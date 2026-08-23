from loopmaster.agents.adapter import CursorAdapter
from loopmaster.agents.registry import AgentRegistry


class TestCursorAdapter:
    def test_discover_no_cursor(self, tmp_path, monkeypatch):
        fake_home = tmp_path / "nonexistent_home"
        fake_home.mkdir()
        monkeypatch.setattr(CursorAdapter, "CONFIG_DIR", fake_home / ".cursor")
        monkeypatch.setattr(CursorAdapter, "SETTINGS_FILE", fake_home / ".cursor" / "settings.json")
        monkeypatch.setattr(CursorAdapter, "RULES_FILE", fake_home / ".cursor" / "rules")
        adapter = CursorAdapter(project_root=tmp_path)
        info = adapter.discover()
        assert info.agent_type == "cursor"
        assert info.display_name == "Cursor"
        assert info.is_installed is False

    def test_discover_with_settings(self, tmp_path, monkeypatch):
        fake_home = tmp_path / "home"
        fake_home.mkdir()
        cursor_dir = fake_home / ".cursor"
        cursor_dir.mkdir()
        (cursor_dir / "settings.json").write_text('{"theme": "dark"}')
        monkeypatch.setattr(CursorAdapter, "CONFIG_DIR", cursor_dir)
        monkeypatch.setattr(CursorAdapter, "SETTINGS_FILE", cursor_dir / "settings.json")
        monkeypatch.setattr(CursorAdapter, "RULES_FILE", cursor_dir / "rules")
        adapter = CursorAdapter(project_root=tmp_path)
        info = adapter.discover()
        assert info.is_installed is True
        assert any("settings.json" in str(p) for p in info.config_paths)

    def test_read_config_no_cursor(self, tmp_path, monkeypatch):
        fake_home = tmp_path / "nonexistent_home"
        fake_home.mkdir()
        monkeypatch.setattr(CursorAdapter, "SETTINGS_FILE", fake_home / ".cursor" / "settings.json")
        adapter = CursorAdapter(project_root=tmp_path)
        config = adapter.read_config()
        assert config == {}

    def test_read_config_with_settings(self, tmp_path, monkeypatch):
        fake_home = tmp_path / "home"
        fake_home.mkdir()
        cursor_dir = fake_home / ".cursor"
        cursor_dir.mkdir()
        (cursor_dir / "settings.json").write_text('{"theme": "dark", "fontSize": 14}')
        monkeypatch.setattr(CursorAdapter, "SETTINGS_FILE", cursor_dir / "settings.json")
        adapter = CursorAdapter(project_root=tmp_path)
        config = adapter.read_config()
        assert len(config) == 1
        values = list(config.values())[0]
        assert values["theme"] == "dark"
        assert values["fontSize"] == 14

    def test_read_system_prompt_no_cursor(self, tmp_path, monkeypatch):
        fake_home = tmp_path / "nonexistent_home"
        fake_home.mkdir()
        monkeypatch.setattr(CursorAdapter, "RULES_FILE", fake_home / ".cursor" / "rules")
        adapter = CursorAdapter(project_root=tmp_path)
        assert adapter.read_system_prompt() == ""

    def test_read_system_prompt_from_rules(self, tmp_path, monkeypatch):
        fake_home = tmp_path / "home"
        fake_home.mkdir()
        cursor_dir = fake_home / ".cursor"
        cursor_dir.mkdir()
        (cursor_dir / "rules").write_text("Be helpful and concise.")
        monkeypatch.setattr(CursorAdapter, "RULES_FILE", cursor_dir / "rules")
        adapter = CursorAdapter(project_root=tmp_path)
        prompt = adapter.read_system_prompt()
        assert prompt == "Be helpful and concise."

    def test_write_config(self, tmp_path, monkeypatch):
        fake_home = tmp_path / "home"
        fake_home.mkdir()
        cursor_dir = fake_home / ".cursor"
        cursor_dir.mkdir()
        (cursor_dir / "settings.json").write_text('{"theme": "light"}')
        monkeypatch.setattr(CursorAdapter, "SETTINGS_FILE", cursor_dir / "settings.json")
        adapter = CursorAdapter(project_root=tmp_path)
        adapter.write_config({str(cursor_dir / "settings.json"): {"theme": "dark"}})
        import json

        content = json.loads((cursor_dir / "settings.json").read_text())
        assert content["theme"] == "dark"

    def test_validate_config_true(self, tmp_path, monkeypatch):
        fake_home = tmp_path / "home"
        fake_home.mkdir()
        cursor_dir = fake_home / ".cursor"
        cursor_dir.mkdir()
        (cursor_dir / "settings.json").write_text("{}")
        monkeypatch.setattr(CursorAdapter, "SETTINGS_FILE", cursor_dir / "settings.json")
        monkeypatch.setattr(CursorAdapter, "CONFIG_DIR", cursor_dir)
        adapter = CursorAdapter(project_root=tmp_path)
        assert adapter.validate_config() is True

    def test_validate_config_false(self, tmp_path, monkeypatch):
        fake_home = tmp_path / "nonexistent"
        fake_home.mkdir()
        monkeypatch.setattr(CursorAdapter, "SETTINGS_FILE", fake_home / "settings.json")
        monkeypatch.setattr(CursorAdapter, "CONFIG_DIR", fake_home / ".cursor")
        adapter = CursorAdapter(project_root=tmp_path)
        assert adapter.validate_config() is False

    def test_config_files_property(self, tmp_path, monkeypatch):
        fake_home = tmp_path / "home"
        fake_home.mkdir()
        cursor_dir = fake_home / ".cursor"
        cursor_dir.mkdir()
        (cursor_dir / "settings.json").write_text("{}")
        monkeypatch.setattr(CursorAdapter, "SETTINGS_FILE", cursor_dir / "settings.json")
        adapter = CursorAdapter(project_root=tmp_path)
        files = adapter.config_files
        assert any("settings.json" in str(f) for f in files)


class TestAgentRegistry:
    def test_cursor_registered(self, tmp_path, monkeypatch):
        fake_home = tmp_path / "nonexistent"
        fake_home.mkdir()
        monkeypatch.setattr(CursorAdapter, "CONFIG_DIR", fake_home / ".cursor")
        monkeypatch.setattr(CursorAdapter, "SETTINGS_FILE", fake_home / "settings.json")
        monkeypatch.setattr(CursorAdapter, "RULES_FILE", fake_home / "rules")
        registry = AgentRegistry(project_root=tmp_path)
        adapter = registry.get_adapter("cursor")
        assert isinstance(adapter, CursorAdapter)

    def test_get_all_adapters(self, tmp_path, monkeypatch):
        fake_home = tmp_path / "nonexistent"
        fake_home.mkdir()
        monkeypatch.setattr(CursorAdapter, "CONFIG_DIR", fake_home / ".cursor")
        monkeypatch.setattr(CursorAdapter, "SETTINGS_FILE", fake_home / "settings.json")
        monkeypatch.setattr(CursorAdapter, "RULES_FILE", fake_home / "rules")
        registry = AgentRegistry(project_root=tmp_path)
        adapters = registry.get_all_adapters()
        assert "cursor" in adapters
        assert "opencode" in adapters
        assert "claude-code" in adapters

    def test_unknown_adapter_raises(self, tmp_path):
        registry = AgentRegistry(project_root=tmp_path)
        import pytest

        with pytest.raises(ValueError, match="Unknown agent type"):
            registry.get_adapter("nonexistent")
