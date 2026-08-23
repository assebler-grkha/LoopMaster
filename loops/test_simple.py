"""Simple test loop for LoopMaster MCP verification."""

from loopmaster.core.types import Loop, Step


@Loop(name="simple_test", version="0.1.0")
def simple_test_loop(ctx):
    Step("greet", model="gpt-4", prompt="Say hello to the user")
    Step("task", model="gpt-4", prompt="Explain what LoopMaster is in 2 sentences")
    Step("summary", model="gpt-4", prompt="Summarize what we discussed: {{greet}} and {{task}}")
    return ctx
