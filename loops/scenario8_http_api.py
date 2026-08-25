"""Scenario 8: HTTPExecutor calling public API + LLM processing."""

import os

from loopmaster import HTTPExecutor, Loop, Step

MODEL = "stealth/ox-alpha"
JOKE_URL = os.environ.get("LM_DEMO_JOKE_URL", "https://official-joke-api.appspot.com/random_joke")
GITHUB_URL = os.environ.get("LM_DEMO_GITHUB_URL", "https://api.github.com")


@Loop(name="test_http_api", version="1.0.0")
def test_http_api(ctx):
    Step(
        "fetch_joke",
        executor=HTTPExecutor(
            url=JOKE_URL,
            method="GET",
            json_output=True,
        ),
    )
    Step(
        "fetch_ip",
        executor=HTTPExecutor(
            url=GITHUB_URL,
            method="GET",
            json_output=True,
            headers={"User-Agent": "LoopMaster/0.1.0"},
        ),
    )
    Step(
        "summarize",
        model=MODEL,
        prompt=(
            "I fetched a joke and my IP info.\n\n"
            "Joke: {fetch_joke.body}\n\n"
            "IP info: {fetch_ip.body}\n\n"
            "Write a one-paragraph humorous summary combining both."
        ),
    )
    return ctx
