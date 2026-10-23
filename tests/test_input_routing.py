import pairai
from tests.test_reply import make_llm_runner


def test_typing_a_still_flags_even_with_an_open_check(git_repo):
    """'a' + Enter must always flag-and-ask, never get swallowed as a reply
    to whatever Finding happens to be open — that's a documented shortcut."""
    (git_repo / "foo.py").write_text(
        "def foo():\n    return 1 if True else 2\n", encoding="utf-8"
    )
    llm = make_llm_runner(["[important] foo.py:2 — why the conditional?"])
    nav = pairai.Navigator(git_repo, "sonnet", 15, True, "en", 8, llm_runner=llm)
    nav.review(manual=True, flagged=True)
    active_before = nav.open_checks.active()
    assert active_before is not None

    nav.handle_input("a")

    # Only one llm call happened (dedupe short-circuits the re-review of an
    # unchanged diff) — a call to reply()'s _ask_reply would have raised
    # (only one canned response was supplied), proving "a" routed to ask(),
    # not reply().
    assert len(llm.calls) == 1
    still_active = nav.open_checks.active()
    assert still_active.uid == active_before.uid
    assert still_active.followups == 0
