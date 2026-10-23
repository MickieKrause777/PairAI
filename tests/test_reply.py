import json

import pairai


def make_llm_runner(responses):
    """Fake llm_runner: returns each response in `responses` in order,
    (exit code 0, that text, ''). Raises if called more times than provided."""
    calls = []

    def runner(cmd, cwd=None, timeout=60):
        calls.append(cmd)
        text = responses[len(calls) - 1]
        return 0, text, ""

    runner.calls = calls
    return runner


def test_reply_resolves_an_open_check(git_repo):
    (git_repo / "foo.py").write_text(
        "def foo():\n    return 1 if True else 2\n", encoding="utf-8"
    )
    llm = make_llm_runner(
        [
            "[important] foo.py:2 — why the conditional?",
            "RESOLVED",
        ]
    )
    nav = pairai.Navigator(git_repo, "sonnet", 15, True, "en", 8, llm_runner=llm)

    nav.review(manual=True, flagged=True)
    assert nav.open_checks.active() is not None

    budget_before = nav.budget.left()
    nav.reply("because True branch is the intended default path")

    assert nav.open_checks.active() is None
    assert nav.budget.left() == budget_before

    saved = json.loads((git_repo / ".review" / "open_checks.json").read_text())
    assert saved[0]["resolved"] is True
