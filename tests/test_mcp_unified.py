"""Tests for unified loopmaster_mcp.py tools and LoopEngine delegation."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from loopmaster.llm import LLMResponse
from scripts.loopmaster_mcp import (
    _find_loop_files,
    _load_loop_def_object,
    loop_get,
    loop_list,
    loop_result,
    loop_run,
    loop_status,
)


class TestMCPUnified:
    def test_find_loop_files(self):
        loops_dir = Path("loops")
        files = _find_loop_files(loops_dir)
        assert len(files) >= 2
        names = [f.name for f in files]
        assert "test_simple.py" in names
        assert "test_error_handling.py" in names

    def test_load_loop_def_object(self):
        simple_py = Path("loops/test_simple.py")
        ldef = _load_loop_def_object(simple_py)
        assert ldef is not None
        assert ldef.name == "simple_test"
        assert callable(ldef.body)

    def test_loop_list_tool(self):
        output = loop_list(search_dir="loops")
        assert "Found" in output
        assert "simple_test" in output

    def test_loop_get_and_result_flow(self):
        get_res_str = loop_get("simple_test", search_dir="loops")
        get_res = json.loads(get_res_str)
        assert "job_id" in get_res
        assert get_res["loop"]["name"] == "simple_test"

        job_id = get_res["job_id"]
        status_res = json.loads(loop_status(job_id))
        assert status_res["status"] == "ready"

        # Report Step 1
        res1 = json.loads(loop_result(job_id, "greet", success=True, output="Hello!"))
        assert res1["status"] == "in_progress"

    def test_loop_run_with_loop_engine(self, monkeypatch):
        monkeypatch.setenv("LOOPMASTER_LLM_PROVIDER", "openai")
        monkeypatch.setenv("LOOPMASTER_OPENAI_API_KEY", "sk-mock-key")

        mock_resp = LLMResponse(
            content="Mocked LLM Output",
            prompt_tokens=10,
            completion_tokens=15,
            total_tokens=25,
            model="gpt-4o",
            duration_ms=120.0,
        )

        with patch("loopmaster.llm.client.LLMClient.complete", return_value=mock_resp):
            output_str = loop_run(
                loop_name="simple_test",
                context='{"name": "Alice"}',
                search_dir="loops",
            )
            output = json.loads(output_str)

            assert output["status"] == "completed"
            assert output["loop_name"] == "simple_test"
            assert output["steps_completed"] == 3
            assert "greet" in output["results"]
            assert output["results"]["greet"] == "Mocked LLM Output"
            assert output["total_tokens"] > 0
            assert output["total_cost"] >= 0.0
