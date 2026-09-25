import pairai

from tests.test_reply import make_llm_runner


def test_context_reaches_the_prompt_and_caps_large_files(git_repo):
    (git_repo / "CONTEXT.md").write_text(
        "# Glossary\n\n**Driver**: the human at the keyboard.\n", encoding="utf-8"
    )
    (git_repo / "docs" / "adr").mkdir(parents=True)
    (git_repo / "docs" / "adr" / "0001-cloud-backend.md").write_text(
        "# Use the hosted Claude CLI\n\nWe accept the cloud dependency.\n",
        encoding="utf-8",
    )

    (git_repo / "foo.py").write_text(
        "def foo():\n    return 1 if True else 2\n", encoding="utf-8"
    )
    llm = make_llm_runner(["SKIP"])
    nav = pairai.Navigator(git_repo, "sonnet", 15, True, "en", 8, llm_runner=llm)
    nav.review(manual=True)

    prompt = llm.calls[0][2]  # ["claude", "-p", prompt, ...]
    assert "def foo():" in prompt  # full touched-file content
    assert "Driver**: the human at the keyboard" in prompt  # CONTEXT.md
    assert "Use the hosted Claude CLI" in prompt  # ADR

    big_file = "\n".join(f"line {i}" for i in range(500))
    (git_repo / "foo.py").write_text(big_file, encoding="utf-8")
    llm2 = make_llm_runner(["SKIP"])
    nav2 = pairai.Navigator(git_repo, "sonnet", 15, True, "en", 8, llm_runner=llm2)
    nav2.review(manual=True)

    prompt2 = llm2.calls[0][2]
    assert "(full file)" not in prompt2  # silently falls back to diff-only
    assert "line 499" not in prompt2
