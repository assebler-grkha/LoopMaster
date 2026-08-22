import pytest

from loopmaster.templates import TEMPLATES, generate_code, get_template, list_templates


class TestTemplates:
    def test_get_template_reflection(self):
        desc = get_template("reflection")
        assert "Self-improving" in desc
        assert "loop" in desc.lower()

    def test_get_template_all_exist(self):
        for name in TEMPLATES:
            desc = get_template(name)
            assert desc is not None
            assert len(desc) > 0

    def test_get_template_nonexistent_raises(self):
        with pytest.raises(ValueError, match="Unknown template"):
            get_template("nonexistent")

    def test_template_count(self):
        assert len(TEMPLATES) == 7

    def test_list_templates_returns_dict(self):
        result = list_templates()
        assert isinstance(result, dict)
        assert len(result) == 7
        assert "reflection" in result
        assert "tool_use" in result

    def test_generate_code_reflection(self):
        code = generate_code(
            "reflection",
            name_var="research_loop",
            task="research AI safety",
            tool="web_search",
        )
        assert "research_loop" in code
        assert "research AI safety" in code
        assert "Step" in code
        assert "@Loop" in code

    def test_generate_code_tool_use(self):
        code = generate_code(
            "tool_use",
            name_var="agent_loop",
            task="process data",
            tool="api_call",
        )
        assert "agent_loop" in code
        assert "api_call" in code

    def test_generate_code_planning(self):
        code = generate_code("planning", name_var="plan", task="create plan")
        assert "plan" in code

    def test_generate_code_multi_agent(self):
        code = generate_code("multi_agent", name_var="team", task="coordinate agents")
        assert "team" in code
        assert "Step" in code

    def test_generate_code_critique(self):
        code = generate_code("critique", name_var="review", task="review code")
        assert "review" in code

    def test_generate_code_escalation(self):
        code = generate_code("escalation", name_var="pipeline", task="escalate issues")
        assert "pipeline" in code

    def test_generate_code_hybrid(self):
        code = generate_code("hybrid", name_var="workflow", task="hybrid task")
        assert "workflow" in code

    def test_generate_code_nonexistent_raises(self):
        with pytest.raises(ValueError, match="Unknown template"):
            generate_code("nonexistent", name_var="x", task="t")

    def test_generated_code_is_valid_python(self):
        for name in TEMPLATES:
            code = generate_code(name, name_var="loop", task="task", tool="tool")
            compile(code, f"<{name}>", "exec")

    def test_generate_code_default_params(self):
        code = generate_code("reflection")
        assert "my_loop" in code
