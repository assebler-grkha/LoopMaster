"""Scenario 8: HTTPExecutor calling public API + LLM processing."""

from loopmaster import HTTPExecutor, Loop, Step

MODEL = "stealth/ox-alpha"


@Loop(name="test_http_api", version="1.0.0")
def test_http_api(ctx):
    Step(
        "fetch_joke",
        executor=HTTPExecutor(
            url="https://official-joke-api.appspot.com/random_joke",
            method="GET",
            json_output=True,
        ),
    )
    Step(
        "fetch_ip",
        executor=HTTPExecutor(
            url="https://api.github.com",
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
