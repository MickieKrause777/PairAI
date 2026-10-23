# PairAI

A pair-programming companion that sits beside you while you code. You're the
**Driver** — you hold the keyboard. `pairai` is the **Navigator**: it never
writes a line of code, only asks questions, and only when it's actually
worth interrupting you for.

## Install

```
git clone https://github.com/MickieKrause777/PairAI.git
cd PairAI
pip install .
```

This installs a `pairai` command on your `PATH` (works from any directory,
not just this one). Prefer an isolated install so it doesn't clash with
other projects' dependencies:

```
pipx install .
# or, straight from GitHub, without cloning first:
pipx install git+https://github.com/MickieKrause777/PairAI
```

You also need the [Claude Code CLI](https://claude.com/claude-code)
(`claude`) on your `PATH` — that's what the Navigator calls to generate its
questions.

## Run it

```
pairai .
```

Point it at any git repo — not just this one (see [Using it on other
repositories](#using-it-on-other-repositories) below).

While it's running:

| Key | Effect |
|---|---|
| `Enter` | **Ask** — review whatever's changed, right now |
| `a` + `Enter` | flag your last change as AI-assisted, then review it |
| `Ctrl-C` | quit |

It also speaks up on its own after a pause — see **Nudge** below.

## How it works

**Two ways a review gets triggered:**

- **Nudge** — passive. If you stop typing for `--idle` seconds (default 8),
  the Navigator looks at your unstaged `git diff` and reviews it
  automatically. This is the "senior dev glancing over" behavior — it
  catches things even when you don't think to ask.
- **Ask** — on demand. Press `Enter` in the Navigator's terminal and it
  reviews immediately, skipping the idle wait *and* the hourly budget below.
  You asked directly, so it's not throttled.

**Before any LLM call, two free/local checks run first:**

1. `ruff` / `mypy`, if installed — catches mechanical issues for nothing.
2. Dedupe — the exact same diff is never sent twice.

**Flagged changes.** A diff gets treated differently if it looks
AI-generated — either because you marked it with `a` + `Enter`, or because
it's unusually large compared to your normal edit size (more than 3× your
recent rolling average, and at least 40 changed lines). Flagged diffs get a
different prompt: instead of a normal review, the Navigator asks one
question meant to test whether *you* can explain the change — what it does,
why this approach, what would break it. The point isn't to review the
code's correctness, it's to check you actually understand code before it
becomes yours.

**The rule that never bends:** the Navigator never writes code. No
snippets, no corrected lines, no solution in prose that just needs typing
out. It can point at a spot, ask what happens in a specific case, name a
concept, or raise a trade-off — that's the ceiling.

**Findings and Weaknesses.** Each thing the Navigator flags is a *Finding*.
Findings are tracked across sessions in `.review/history.json` (inside the
repo you're running it on); once the same kind of Finding recurs three
times, it's promoted into `.review/weaknesses.md` as a **Weakness** —
recurring patterns you're told about proactively at startup ("watching
for: ...") and that get checked first on every future review.

**Budget.** Automatic Nudges are capped at `--budget` calls/hour (default
15) so an idle afternoon doesn't quietly rack up API cost. Manual Asks are
never throttled.

## Flags

```
pairai [path] [options]

  path              directory to watch (default: current directory)
  --model MODEL     Claude model to use (default: sonnet)
  --fast            use a cheaper model (haiku)
  --budget N        automatic Nudge calls per hour (default: 15)
  --idle SECONDS    pause before a passive Nudge fires (default: 8)
  --lang {en,de}    language for prompts and output (default: en)
  --no-lint         skip the local ruff/mypy pass
```

## Using it on other repositories

`pairai` isn't tied to this project. It only needs `<path>/.git` to exist —
point it anywhere:

```
pairai ~/some-other-project
# or, from inside that project:
cd ~/some-other-project && pairai .
```

All of its memory (`.review/history.json`, `.review/weaknesses.md`) is
written *inside the repo you point it at*, not here — so each repo builds
its own independent Weakness history, and you'll want a `.review/` entry in
that repo's `.gitignore` too (this repo already ignores its own).

## Why the Claude CLI, not a fully local model

Right now the Navigator calls the hosted `claude` CLI rather than a local
model runtime (Ollama, etc.) — see
[`docs/adr/0001-cloud-claude-cli-backend.md`](docs/adr/0001-cloud-claude-cli-backend.md)
for why, and the condition under which that gets revisited.

## More

- [`CONTEXT.md`](CONTEXT.md) — the project's glossary (Driver, Navigator,
  Nudge, Ask, Finding, Weakness, ...)
- [`docs/adr/`](docs/adr/) — architectural decisions and why they were made

## License

[MIT](LICENSE)
