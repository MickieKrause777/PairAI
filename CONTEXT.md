# PairAI

A pair-programming companion that sits beside a developer as they code, watching for moments worth a question rather than handing over answers.

## Language

**Driver**:
The human at the keyboard. Writes all the code; the one the session exists to help improve.
_Avoid_: User, developer (when "Driver" is meant specifically — use "Driver" whenever the role in the pairing session, not just "someone using the software," is meant)

**Navigator**:
The AI role that watches the Driver's changes and raises questions, but never writes code itself.
_Avoid_: Assistant, bot, reviewer, copilot

**Finding**:
A single flagged observation the Navigator raises about the Driver's code — one severity, one location, one question.
_Avoid_: Issue, comment, suggestion (suggestion implies a proposed fix, which the Navigator never gives)

**Severity**:
The urgency label on a Finding: `blocker`, `important`, or `minor`.

**Nudge**:
A passive review the Navigator initiates on its own after the Driver pauses.
_Avoid_: notification, alert

**Ask**:
An on-demand review the Driver explicitly triggers, right when they want feedback, independent of any pause.
_Avoid_: request, trigger

**Weakness**:
A Finding pattern that has recurred across multiple sessions, promoted from one-off into the Driver's persistent, automatically-maintained learning history.
_Avoid_: issue, bad habit

**Flagged change**:
A diff the Navigator treats as likely AI-authored — either by size heuristic or the Driver marking it explicitly — which gets an Understanding Check instead of an ordinary review.

**Understanding Check**:
A question that tests whether the Driver can explain a Flagged change, rather than reviewing it for correctness.
