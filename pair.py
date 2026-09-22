#!/usr/bin/env python3
"""
pair.py — ein Navigator, der beim Programmieren danebensitzt.

Du bist Driver. Das Tool schreibt keine Zeile Code. Es beobachtet, was du
aenderst, und meldet sich, wenn du eine Denkpause machst — mit Fragen,
nicht mit Loesungen.

    pip install watchdog
    python pair.py .                # aktuelles Projekt beobachten
    python pair.py . --fast         # billigeres Modell
    python pair.py . --budget 10    # max. 10 LLM-Calls pro Stunde

Token-Sparmassnahmen (in dieser Reihenfolge):
  1. Lokale Linter zuerst  — faengt Mechanisches kostenlos ab
  2. Nur Diff-Hunks        — nie ganze Dateien
  3. Hash-Dedupe           — derselbe Code wird nie zweimal geprueft
  4. Stundenbudget         — hartes Limit auf LLM-Calls
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
    sys.exit("watchdog fehlt:  pip install watchdog")

# ---------------------------------------------------------------- Konfiguration

IDLE_SECONDS = 25  # so lange musst du nicht tippen, bevor er sich meldet
MIN_CHANGED_LINES = 3  # kleinere Aenderungen ignorieren
MAX_DIFF_LINES = 160  # Obergrenze fuer den Kontext, der rausgeht
DEFAULT_BUDGET = 15  # LLM-Calls pro Stunde
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

SYSTEM_PROMPT = """Du bist Navigator in einer Pair-Programming-Session. Der Mensch
hat die Tastatur — du nicht.

Absolute Regel: Du schreibst niemals Code. Kein Snippet, keine korrigierte Zeile,
keine Loesung in Prosa, die nur noch abgetippt werden muss. Wer die Loesung
bekommt, lernt nichts.

Du darfst: auf eine Stelle zeigen, fragen was in einem bestimmten Fall passiert,
ein Konzept beim Namen nennen, einen Trade-off aufmachen.

Du siehst nur einen kleinen Ausschnitt mitten in der Arbeit. Code, der
unfertig aussieht, ist wahrscheinlich unfertig — meckere nicht ueber fehlende
Teile, die offensichtlich gerade entstehen. Melde dich nur, wenn du etwas
siehst, das der Mensch spaeter teuer bezahlt: falsche Annahme, uebersehener
Fehlerfall, Struktur die nicht traegt, Sicherheitsproblem.

Wenn nichts davon zutrifft, antworte exakt: SKIP

Sonst maximal zwei Punkte, jeweils in diesem Format:
[schweregrad] datei:zeile — deine Frage (ein Satz)

Schweregrad ist eines von: blocker, wichtig, klein.
Keine Einleitung, kein Abschluss, keine Ermutigung."""


# ---------------------------------------------------------------- Hilfsmittel


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
    """Gleitendes Stundenfenster fuer LLM-Calls."""

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
    def __init__(self, root, model, budget, quiet_lint):
        self.root = Path(root).resolve()
        self.model = model
        self.budget = Budget(budget)
        self.quiet_lint = quiet_lint
        self.seen = set()  # Hashes bereits geprueften Codes
        self.raised = set()  # bereits genannte Findings
        self.weaknesses = self._load_weaknesses()
        self.timer = None
        self.pending = set()

    # -- Kontext ---------------------------------------------------

    def _load_weaknesses(self):
        """Wiederkehrende Schwaechen aus dem Review-Log — nur die Ueberschriften."""
        path = self.root / ".review" / "weaknesses.md"
        if not path.exists():
            return []
        heads = re.findall(
            r"^##\s+(.+)$", path.read_text(encoding="utf-8"), re.MULTILINE
        )
        return heads[:4]

    def _diff(self):
        """Ungestagete Aenderungen, auf eine sinnvolle Groesse gekappt."""
        code, out, _ = run(["git", "diff", "--unified=3", "--no-color"], cwd=self.root)
        if code != 0 or not out.strip():
            code, out, _ = run(["git", "diff", "--cached", "--no-color"], cwd=self.root)
        if not out.strip():
            return None
        lines = out.splitlines()
        if len(lines) > MAX_DIFF_LINES:
            lines = lines[:MAX_DIFF_LINES] + ["... (gekuerzt)"]
        return "\n".join(lines)

    # -- Stufe 1: lokal, kostenlos ---------------------------------

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

    # -- Stufe 2: LLM ----------------------------------------------

    def _ask(self, diff):
        ctx = ""
        if self.weaknesses:
            ctx = (
                "\nWiederkehrende Schwaechen dieses Entwicklers, zuerst pruefen:\n"
                + "\n".join(f"- {w}" for w in self.weaknesses)
                + "\n"
            )
        prompt = f"{ctx}\nAktuelle Aenderungen:\n\n```diff\n{diff}\n```"

        code, out, err = run(
            [
                "claude",
                "-p",
                prompt,
                "--append-system-prompt",
                SYSTEM_PROMPT,
                "--model",
                self.model,
            ],
            cwd=self.root,
            timeout=90,
        )
        if code != 0:
            return None if not err else f"[fehler] {err.strip().splitlines()[0]}"
        return out.strip()

    # -- Ausgabe ---------------------------------------------------

    def _print(self, text, local=False):
        stamp = time.strftime("%H:%M")
        tag = "lint" if local else "navigator"
        print(
            f"\n{C['grey']}{stamp} · {tag} · "
            f"{self.budget.left()} Calls uebrig{C['off']}"
        )
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            colour = C["blue"]
            if line.lower().startswith(("[blocker]", "blocker")):
                colour = C["red"]
            elif line.lower().startswith(("[wichtig]", "wichtig")):
                colour = C["yellow"]
            print(f"  {colour}{line}{C['off']}")
        print()

    # -- Ablauf ----------------------------------------------------

    def schedule(self, path):
        self.pending.add(path)
        if self.timer:
            self.timer.cancel()
        self.timer = Timer(IDLE_SECONDS, self.review)
        self.timer.daemon = True
        self.timer.start()

    def review(self):
        files, self.pending = list(self.pending), set()
        if not files:
            return

        # Stufe 1 — lokal, immer, kostenlos
        if not self.quiet_lint:
            local = self._lint(files)
            if local:
                self._print("\n".join(local), local=True)

        diff = self._diff()
        if not diff:
            return

        changed = sum(
            1
            for l in diff.splitlines()
            if l.startswith(("+", "-")) and not l.startswith(("+++", "---"))
        )
        if changed < MIN_CHANGED_LINES:
            return

        # Dedupe — denselben Stand nie zweimal schicken
        h = hashlib.sha256(diff.encode()).hexdigest()[:16]
        if h in self.seen:
            return
        self.seen.add(h)

        if not self.budget.allows():
            print(
                f"{C['grey']}  (Stundenbudget aufgebraucht — "
                f"lokale Checks laufen weiter){C['off']}"
            )
            return

        self.budget.spend()
        answer = self._ask(diff)
        if not answer or answer.strip().upper().startswith("SKIP"):
            return

        # bereits genannte Findings nicht wiederholen
        fresh = []
        for line in answer.splitlines():
            key = re.sub(r"[^a-z]", "", line.lower())[:60]
            if key and key not in self.raised:
                self.raised.add(key)
                fresh.append(line)
        if fresh:
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
    ap = argparse.ArgumentParser(description="Navigator fuers Pair Programming")
    ap.add_argument("path", nargs="?", default=".")
    ap.add_argument("--model", default="sonnet")
    ap.add_argument("--fast", action="store_true", help="billigeres Modell (haiku)")
    ap.add_argument(
        "--budget", type=int, default=DEFAULT_BUDGET, help="LLM-Calls pro Stunde"
    )
    ap.add_argument("--no-lint", action="store_true")
    args = ap.parse_args()

    root = Path(args.path).resolve()
    if not (root / ".git").exists():
        sys.exit("Kein Git-Repo — der Navigator braucht Diffs.")
    if not have("claude"):
        sys.exit("Claude Code CLI nicht gefunden.")

    nav = Navigator(
        root, "haiku" if args.fast else args.model, args.budget, args.no_lint
    )

    obs = Observer()
    obs.schedule(Handler(nav), str(root), recursive=True)
    obs.start()

    print(f"{C['bold']}Navigator laeuft{C['off']} — du hast die Tastatur.")
    print(f"{C['grey']}  {root}")
    print(
        f"  meldet sich nach {IDLE_SECONDS}s Pause · "
        f"max. {args.budget} Calls/Stunde · Modell: {nav.model}"
    )
    if nav.weaknesses:
        print(f"  achtet auf: {', '.join(nav.weaknesses)}")
    print(f"  Strg-C zum Beenden{C['off']}\n")

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        obs.stop()
        print(f"\n{C['grey']}Session beendet.{C['off']}")
    obs.join()


if __name__ == "__main__":
    main()
