#!/usr/bin/env python3
"""
pair.py — a Navigator that sits beside you while you code.

You're the Driver. This tool never writes a line of code. It watches what
you change and speaks up when there's something worth a question — not an
answer.

    pip install watchdog
    python pair.py .                # watch the current project
    python pair.py . --fast         # cheaper model
    python pair.py . --budget 10    # max. 10 LLM calls per hour
    python pair.py . --lang de      # German instead of English

While it's running:
    Enter        ask the Navigator to review right now
    a + Enter    flag your last change as AI-assisted, then review it

Token-saving measures (in this order):
  1. Local linters first  — catch the mechanical stuff for free
  2. Diff hunks only      — never whole files
  3. Hash dedupe          — the same code is never checked twice
  4. Hourly budget        — a hard cap on automatic LLM calls (manual
                             Ask requests bypass it — you asked, you pay)
"""

import argparse
import hashlib
import json
import re
import subprocess
import sys
import time
from collections import deque
from pathlib import Path
from threading import Timer

try:
    from watchdog.events import FileSystemEventHandler
    from watchdog.observers import Observer
except ImportError:
    sys.exit("watchdog missing:  pip install watchdog")

# ---------------------------------------------------------------- Configuration

DEFAULT_IDLE_SECONDS = 8  # how long you can pause before the passive Nudge fires
MIN_CHANGED_LINES = 3  # ignore smaller changes (Nudge only — Ask always runs)
MAX_DIFF_LINES = 160  # cap on the context sent out
DEFAULT_BUDGET = 15  # LLM calls per hour, Nudges only
AI_HEURISTIC_MULTIPLIER = 3  # a diff this many times your rolling average size...
AI_HEURISTIC_FLOOR = 40  # ...and at least this many changed lines...
WEAKNESS_PROMOTE_THRESHOLD = 3  # ...gets treated as a Flagged (likely AI) change
WATCH_SUFFIXES = {".py", ".ts", ".tsx", ".js", ".jsx", ".go", ".rs", ".java"}
IGNORE_PARTS = {
    ".git",
    "node_modules",
    "__pycache__",
    ".venv",
    "venv",
    "dist",
    "build",
    ".mypy_cache",
    ".ruff_cache",
    ".review",
}

C = {
    "red": "\033[31m",
    "yellow": "\033[33m",
    "blue": "\033[34m",
    "grey": "\033[90m",
    "bold": "\033[1m",
    "off": "\033[0m",
}

SEVERITY_COLOR = {
    "blocker": "red",
    "important": "yellow",
    "wichtig": "yellow",
    "minor": "blue",
    "klein": "blue",
}

SYSTEM_PROMPTS = {
    "en": {
        "default": """You are the Navigator in a pair-programming session. The human
holds the keyboard — you don't.

Absolute rule: you never write code. No snippet, no corrected line, no
prose solution that just needs typing out. Whoever gets the solution
learns nothing.

You may: point at a spot, ask what happens in a specific case, name a
concept, open up a trade-off.

You only see a small slice of work in progress. Code that looks
unfinished is probably unfinished — don't complain about missing parts
that are obviously still being written. Only speak up when you see
something the human will pay for later: a wrong assumption, a missed
error case, a structure that won't hold, a security problem.

If none of that applies, reply with exactly: SKIP

Otherwise, at most two points, each in this format:
[severity] file:line — your question (one sentence)

Severity is one of: blocker, important, minor.
No preamble, no closing remarks, no encouragement.""",
        "flagged": """You are the Navigator in a pair-programming session. The human
holds the keyboard — you don't.

This diff is unusually large for how this Driver normally edits — it may
be AI-generated or AI-assisted code that was pasted or accepted rather
than typed incrementally.

Absolute rule: you never write code, never give a fix.

Your only job here: ask ONE question that tests whether the Driver can
explain this change — what it does, why this approach, or what would
break it. Don't do a generic style/correctness review — probe
understanding.

If the change is trivial enough that no understanding check is
warranted (e.g. generated boilerplate/config with no real logic), reply
with exactly: SKIP

Format:
[severity] file:line — your question (one sentence)

Severity is one of: blocker, important, minor.
No preamble, no closing remarks, no encouragement.""",
    },
    "de": {
        "default": """Du bist Navigator in einer Pair-Programming-Session. Der Mensch
hat die Tastatur — du nicht.

Absolute Regel: Du schreibst niemals Code. Kein Snippet, keine korrigierte
Zeile, keine Loesung in Prosa, die nur noch abgetippt werden muss. Wer die
Loesung bekommt, lernt nichts.

Du darfst: auf eine Stelle zeigen, fragen was in einem bestimmten Fall
passiert, ein Konzept beim Namen nennen, einen Trade-off aufmachen.

Du siehst nur einen kleinen Ausschnitt mitten in der Arbeit. Code, der
unfertig aussieht, ist wahrscheinlich unfertig — meckere nicht ueber
fehlende Teile, die offensichtlich gerade entstehen. Melde dich nur, wenn
du etwas siehst, das der Mensch spaeter teuer bezahlt: falsche Annahme,
uebersehener Fehlerfall, Struktur die nicht traegt, Sicherheitsproblem.

Wenn nichts davon zutrifft, antworte exakt: SKIP

Sonst maximal zwei Punkte, jeweils in diesem Format:
[schweregrad] datei:zeile — deine Frage (ein Satz)

Schweregrad ist eines von: blocker, wichtig, klein.
Keine Einleitung, kein Abschluss, keine Ermutigung.""",
        "flagged": """Du bist Navigator in einer Pair-Programming-Session. Der Mensch
hat die Tastatur — du nicht.

Dieser Diff ist ungewoehnlich gross fuer die Art, wie dieser Driver
normalerweise arbeitet — moeglicherweise KI-generierter oder KI-gestuetzter
Code, der eingefuegt statt schrittweise getippt wurde.

Absolute Regel: Du schreibst niemals Code, gibst niemals eine Loesung.

Deine einzige Aufgabe hier: Stelle EINE Frage, die prueft, ob der Driver
diese Aenderung erklaeren kann — was sie tut, warum dieser Ansatz, oder
was sie kaputt machen wuerde. Keine generische Stil-/Korrektheitspruefung
— pruefe Verstaendnis.

Wenn die Aenderung trivial genug ist, dass keine Verstaendnisfrage
angebracht ist (z.B. generiertes Boilerplate/Konfiguration ohne echte
Logik), antworte exakt: SKIP

Format:
[schweregrad] datei:zeile — deine Frage (ein Satz)

Schweregrad ist eines von: blocker, wichtig, klein.
Keine Einleitung, kein Abschluss, keine Ermutigung.""",
    },
}

MSG = {
    "en": {
        "no_git": "Not a git repo — the Navigator needs diffs.",
        "no_claude": "Claude Code CLI not found.",
        "running": "Navigator running",
        "controls": "Enter = ask now · 'a' + Enter = flag last change as "
        "AI-assisted and ask · Ctrl-C to quit",
        "info": "speaks up after {idle}s of pause · max {budget} calls/hour"
        " · model: {model}",
        "watching_for": "watching for: {items}",
        "session_ended": "Session ended.",
        "budget_exhausted": "(hourly budget used up — local checks keep running)",
        "nothing_pending": "(nothing to review yet)",
        "already_reviewed": "(no new changes since the last review)",
        "ai_flagged_notice": "(this looks like an AI-assisted change)",
        "tag_lint": "lint",
        "tag_navigator": "navigator",
        "calls_left": "{n} calls left",
    },
    "de": {
        "no_git": "Kein Git-Repo — der Navigator braucht Diffs.",
        "no_claude": "Claude Code CLI nicht gefunden.",
        "running": "Navigator laeuft",
        "controls": "Enter = jetzt fragen · 'a' + Enter = letzte Aenderung als "
        "KI-unterstuetzt markieren und fragen · Strg-C zum Beenden",
        "info": "meldet sich nach {idle}s Pause · max. {budget} Calls/Stunde"
        " · Modell: {model}",
        "watching_for": "achtet auf: {items}",
        "session_ended": "Session beendet.",
        "budget_exhausted": "(Stundenbudget aufgebraucht — lokale Checks laufen weiter)",
        "nothing_pending": "(noch nichts zu pruefen)",
        "already_reviewed": "(keine neuen Aenderungen seit der letzten Pruefung)",
        "ai_flagged_notice": "(sieht nach einer KI-unterstuetzten Aenderung aus)",
        "tag_lint": "lint",
        "tag_navigator": "navigator",
        "calls_left": "{n} Calls uebrig",
    },
}


# ---------------------------------------------------------------- Helpers


def run(cmd, cwd=None, timeout=60):
    try:
        r = subprocess.run(
            cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout
        )
        return r.returncode, r.stdout, r.stderr
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return 1, "", ""


def have(tool):
    return run(["which", tool])[0] == 0


class Budget:
    """A rolling one-hour window for LLM calls."""

    def __init__(self, limit):
        self.limit = limit
        self.calls = deque()

    def _trim(self):
        cutoff = time.time() - 3600
        while self.calls and self.calls[0] < cutoff:
            self.calls.popleft()

    def allows(self):
        self._trim()
        return len(self.calls) < self.limit

    def spend(self):
        self.calls.append(time.time())

    def left(self):
        self._trim()
        return self.limit - len(self.calls)


# ---------------------------------------------------------------- Navigator


class Navigator:
    def __init__(self, root, model, budget, quiet_lint, lang, idle_seconds):
        self.root = Path(root).resolve()
        self.model = model
        self.budget = Budget(budget)
        self.quiet_lint = quiet_lint
        self.lang = lang
        self.msg = MSG[lang]
        self.idle_seconds = idle_seconds
        self.seen = set()  # hashes of diffs already reviewed
        self.raised = set()  # finding keys already shown this session
        self.diff_sizes = deque(maxlen=20)  # rolling diff-size baseline
        self.weaknesses = self._load_weaknesses()
        self.history = self._load_history()
        self.timer = None
        self.pending = set()

    # -- Context -----------------------------------------------------

    def _load_weaknesses(self):
        """Recurring weaknesses from the review log — headings only."""
        path = self.root / ".review" / "weaknesses.md"
        if not path.exists():
            return []
        heads = re.findall(
            r"^##\s+(.+)$", path.read_text(encoding="utf-8"), re.MULTILINE
        )
        return heads[:4]

    def _load_history(self):
        path = self.root / ".review" / "history.json"
        if not path.exists():
            return {}
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}

    def _save_history(self):
        path = self.root / ".review" / "history.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.history, indent=2), encoding="utf-8")

    def _record_finding(self, key, text):
        """Track a Finding across sessions; promote it to a Weakness once
        it has recurred often enough."""
        entry = self.history.get(key, {"text": text, "count": 0, "promoted": False})
        entry["count"] += 1
        entry["text"] = text
        self.history[key] = entry
        self._save_history()
        if not entry["promoted"] and entry["count"] >= WEAKNESS_PROMOTE_THRESHOLD:
            entry["promoted"] = True
            self._save_history()
            self._promote_weakness(text)

    def _promote_weakness(self, text):
        path = self.root / ".review" / "weaknesses.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        heading = re.sub(r"^\[.*?\]\s*", "", text).strip()[:80]
        existing = path.read_text(encoding="utf-8") if path.exists() else ""
        if heading.lower() in existing.lower():
            return
        with path.open("a", encoding="utf-8") as f:
            f.write(f"\n## {heading}\n\nRecurring since {time.strftime('%Y-%m-%d')}.\n")

    def _diff(self):
        """Unstaged changes, capped to a sane size."""
        code, out, _ = run(["git", "diff", "--unified=3", "--no-color"], cwd=self.root)
        if code != 0 or not out.strip():
            code, out, _ = run(["git", "diff", "--cached", "--no-color"], cwd=self.root)
        if not out.strip():
            return None
        lines = out.splitlines()
        if len(lines) > MAX_DIFF_LINES:
            lines = lines[:MAX_DIFF_LINES] + ["... (truncated)"]
        return "\n".join(lines)

    def _looks_ai_generated(self, changed):
        if not self.diff_sizes:
            return False
        avg = sum(self.diff_sizes) / len(self.diff_sizes)
        return changed > max(AI_HEURISTIC_FLOOR, AI_HEURISTIC_MULTIPLIER * avg)

    # -- Stage 1: local, free ----------------------------------------

    def _lint(self, files):
        findings = []
        py = [f for f in files if f.suffix == ".py"]
        if py and have("ruff"):
            _, out, _ = run(
                ["ruff", "check", "--output-format=concise", *map(str, py)],
                cwd=self.root,
            )
            findings += [l for l in out.splitlines() if l.strip()][:6]
        if py and have("mypy"):
            _, out, _ = run(
                ["mypy", "--no-error-summary", "--follow-imports=skip", *map(str, py)],
                cwd=self.root,
                timeout=40,
            )
            findings += [l for l in out.splitlines() if ": error:" in l][:4]
        return findings

    # -- Stage 2: LLM --------------------------------------------------

    def _ask(self, diff, flagged=False):
        ctx = ""
        if self.weaknesses:
            label = (
                "Recurring weaknesses for this developer, check these first:"
                if self.lang == "en"
                else "Wiederkehrende Schwaechen dieses Entwicklers, zuerst pruefen:"
            )
            ctx = "\n" + label + "\n" + "\n".join(f"- {w}" for w in self.weaknesses) + "\n"
        prompt = f"{ctx}\nCurrent changes:\n\n```diff\n{diff}\n```"

        system_prompt = SYSTEM_PROMPTS[self.lang]["flagged" if flagged else "default"]
        code, out, err = run(
            [
                "claude",
                "-p",
                prompt,
                "--append-system-prompt",
                system_prompt,
                "--model",
                self.model,
            ],
            cwd=self.root,
            timeout=90,
        )
        if code != 0:
            return None if not err else f"[error] {err.strip().splitlines()[0]}"
        return out.strip()

    # -- Output ----------------------------------------------------

    def _notice(self, text):
        print(f"{C['grey']}  {text}{C['off']}")

    def _print(self, text, local=False):
        stamp = time.strftime("%H:%M")
        tag = self.msg["tag_lint"] if local else self.msg["tag_navigator"]
        print(
            f"\n{C['grey']}{stamp} · {tag} · "
            f"{self.msg['calls_left'].format(n=self.budget.left())}{C['off']}"
        )
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            word = re.sub(r"[^a-zA-Z]", "", line.split("]")[0]).lower()
            colour = C[SEVERITY_COLOR.get(word, "blue")]
            print(f"  {colour}{line}{C['off']}")
        print()

    # -- Flow --------------------------------------------------------

    def schedule(self, path):
        self.pending.add(path)
        if self.timer:
            self.timer.cancel()
        self.timer = Timer(self.idle_seconds, self.review)
        self.timer.daemon = True
        self.timer.start()

    def ask(self, mark_ai=False):
        """Manual Ask — bypasses the idle wait and the hourly budget."""
        if self.timer:
            self.timer.cancel()
            self.timer = None
        self.pending = set()
        self.review(manual=True, flagged=mark_ai)

    def review(self, manual=False, flagged=False):
        files, self.pending = list(self.pending), set()
        if not files and not manual:
            return

        # Stage 1 — local, always, free
        if not self.quiet_lint and files:
            local = self._lint(files)
            if local:
                self._print("\n".join(local), local=True)

        diff = self._diff()
        if not diff:
            if manual:
                self._notice(self.msg["nothing_pending"])
            return

        changed = sum(
            1
            for l in diff.splitlines()
            if l.startswith(("+", "-")) and not l.startswith(("+++", "---"))
        )
        if changed < MIN_CHANGED_LINES and not manual:
            return

        # Dedupe — never send the same state twice
        h = hashlib.sha256(diff.encode()).hexdigest()[:16]
        if h in self.seen:
            if manual:
                self._notice(self.msg["already_reviewed"])
            return
        self.seen.add(h)

        is_flagged = flagged or self._looks_ai_generated(changed)
        self.diff_sizes.append(changed)

        if not manual:
            if not self.budget.allows():
                self._notice(self.msg["budget_exhausted"])
                return
            self.budget.spend()

        answer = self._ask(diff, flagged=is_flagged)
        if not answer or answer.strip().upper().startswith("SKIP"):
            return

        # never repeat an already-raised finding
        fresh = []
        for line in answer.splitlines():
            key = re.sub(r"[^a-z]", "", line.lower())[:60]
            if key and key not in self.raised:
                self.raised.add(key)
                fresh.append(line)
                self._record_finding(key, line)
        if fresh:
            if is_flagged:
                self._notice(self.msg["ai_flagged_notice"])
            self._print("\n".join(fresh))


class Handler(FileSystemEventHandler):
    def __init__(self, nav):
        self.nav = nav

    def on_modified(self, event):
        if event.is_directory:
            return
        p = Path(event.src_path)
        if p.suffix not in WATCH_SUFFIXES:
            return
        if IGNORE_PARTS & set(p.parts):
            return
        self.nav.schedule(p)


def main():
    ap = argparse.ArgumentParser(description="Navigator for pair programming")
    ap.add_argument("path", nargs="?", default=".")
    ap.add_argument("--model", default="sonnet")
    ap.add_argument("--fast", action="store_true", help="cheaper model (haiku)")
    ap.add_argument(
        "--budget", type=int, default=DEFAULT_BUDGET, help="LLM calls per hour"
    )
    ap.add_argument(
        "--idle",
        type=float,
        default=DEFAULT_IDLE_SECONDS,
        help="seconds of pause before a passive Nudge fires",
    )
    ap.add_argument("--lang", choices=["en", "de"], default="en")
    ap.add_argument("--no-lint", action="store_true")
    args = ap.parse_args()

    root = Path(args.path).resolve()
    msg = MSG[args.lang]
    if not (root / ".git").exists():
        sys.exit(msg["no_git"])
    if not have("claude"):
        sys.exit(msg["no_claude"])

    nav = Navigator(
        root,
        "haiku" if args.fast else args.model,
        args.budget,
        args.no_lint,
        args.lang,
        args.idle,
    )

    obs = Observer()
    obs.schedule(Handler(nav), str(root), recursive=True)
    obs.start()

    print(f"{C['bold']}{msg['running']}{C['off']} — you have the keyboard.")
    print(f"{C['grey']}  {root}")
    print(
        f"  {msg['info'].format(idle=nav.idle_seconds, budget=args.budget, model=nav.model)}"
    )
    if nav.weaknesses:
        print(f"  {msg['watching_for'].format(items=', '.join(nav.weaknesses))}")
    print(f"  {msg['controls']}{C['off']}\n")

    try:
        while True:
            try:
                line = input()
            except EOFError:
                break
            nav.ask(mark_ai=line.strip().lower() == "a")
    except KeyboardInterrupt:
        pass
    obs.stop()
    print(f"\n{C['grey']}{msg['session_ended']}{C['off']}")
    obs.join()


if __name__ == "__main__":
    main()
