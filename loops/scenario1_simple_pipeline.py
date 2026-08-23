"""Scenario 1: Simple Sequential Pipeline — Step chaining with template variables."""

from loopmaster.core.types import Loop, Step

MODEL = "stealth/ox-alpha"


@Loop(name="test_simple_pipeline", version="1.0.0")
def test_simple_pipeline(ctx):
    Step("greet", model=MODEL, prompt="Say hello in one sentence.")
    Step("topic", model=MODEL, prompt="Name a random interesting topic in 5 words.")
    Step("summary", model=MODEL, prompt="Combine these into a fun fact: {{greet}} about {{topic}}")
    return ctx
