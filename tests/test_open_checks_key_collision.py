import pairai
from tests.test_reply import make_llm_runner


def test_same_finding_text_across_restart_does_not_cross_resolve(git_repo):
    (git_repo / "foo.py").write_text(
        "def foo():\n    return 1 if True else 2\n", encoding="utf-8"
    )
    llm1 = make_llm_runner(["[important] foo.py:2 — why the conditional?"])
    nav1 = pairai.Navigator(git_repo, "sonnet", 15, True, "en", 8, llm_runner=llm1)
    nav1.review(manual=True, flagged=True)
    old_check = nav1.open_checks.active()
    # Driver quits without answering — session 1 ends.

    # Session 2: the identical finding text recurs (self.raised resets on restart).
    (git_repo / "foo.py").write_text(
        "def foo():\n    return 1 if True else 3\n", encoding="utf-8"
    )
    llm2 = make_llm_runner(
        [
            "[important] foo.py:2 — why the conditional?",  # same text -> same content key
            "RESOLVED",
        ]
    )
    nav2 = pairai.Navigator(git_repo, "sonnet", 15, True, "en", 8, llm_runner=llm2)
    assert nav2.open_checks.open_count() == 1  # the old one, loaded from disk

    nav2.review(manual=True, flagged=True)
    assert nav2.open_checks.open_count() == 2  # old + new, sharing a content key

    nav2.reply(
        "because True branch is the intended default"
    )  # resolves only the active one

    assert nav2.open_checks.open_count() == 1  # the old one must still be open
    assert nav2.open_checks.active().uid == old_check.uid
