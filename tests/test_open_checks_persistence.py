import pairai
from tests.test_reply import make_llm_runner


def test_lifo_reply_and_persistence_across_restart(git_repo):
    (git_repo / "foo.py").write_text(
        "def foo():\n    return 1 if True else 2\n", encoding="utf-8"
    )
    llm = make_llm_runner(
        [
            "[important] foo.py:2 — why the conditional?",  # review 1 -> check A
            "[important] foo.py:2 — why not just return 1?",  # review 2 -> check B
            "RESOLVED",  # reply -> resolves B, the most recent
        ]
    )
    nav = pairai.Navigator(git_repo, "sonnet", 15, True, "en", 8, llm_runner=llm)

    nav.review(manual=True, flagged=True)
    check_a = nav.open_checks.active()

    (git_repo / "foo.py").write_text(
        "def foo():\n    return 1 if True else 3\n", encoding="utf-8"
    )
    nav.seen.clear()  # force a second, distinct review to run
    nav.review(manual=True, flagged=True)
    check_b = nav.open_checks.active()
    assert check_b.uid != check_a.uid

    nav.reply("second answer")
    assert nav.open_checks.active().uid == check_a.uid

    # simulate a restart: a fresh Navigator loading the same repo's .review/
    nav2 = pairai.Navigator(git_repo, "sonnet", 15, True, "en", 8, llm_runner=llm)
    assert nav2.open_checks.open_count() == 1
    assert nav2.open_checks.active().uid == check_a.uid
