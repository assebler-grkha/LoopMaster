"""Scenario 7: ShellExecutor pipeline — run commands, analyze output with LLM."""

from loopmaster import Loop, ShellExecutor, Step

MODEL = "stealth/ox-alpha"

SYSINFO_CMD = (
    "import sys,platform;"
    "print('Python=' + sys.version.split()[0]"
    " + ' OS=' + platform.system()"
    " + ' Arch=' + platform.machine())"
)

FILES_CMD = "import os;print('\\n'.join(sorted(os.listdir('.'))[:10]))"


@Loop(name="test_shell_pipeline", version="1.0.0")
def test_shell_pipeline(ctx):
    Step(
        "get_system_info",
        executor=ShellExecutor(command=["python", "-c", SYSINFO_CMD]),
    )
    Step(
        "list_files",
        executor=ShellExecutor(command=["python", "-c", FILES_CMD]),
    )
    Step(
        "analyze",
        model=MODEL,
        prompt=(
            "Given this system info: {get_system_info.stdout}\n"
            "And these files in the current dir: {list_files.stdout}\n"
            "Write a 2-sentence summary."
        ),
    )
    return ctx
