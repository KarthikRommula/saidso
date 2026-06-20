# saidso — overview

A grounding firewall for action-taking AI agents. One rule:

  Nothing is committed (a tool argument) or spoken (a fact) unless it traces back
  to something real — what the user said, or what a tool returned.

Anything ungrounded is blocked, in code, before it can cause harm.

## Why

LLM agents don't just talk — they DO things (call tools) and STATE facts. Models
sometimes fabricate: an argument the caller never said, an id that doesn't exist, a
name nobody offered. Prompting ("never make things up") is best-effort, leaves no
proof, and degrades as you add tools. saidso runs in code and assumes the model
WILL hallucinate — it just refuses to let the hallucination matter.

## The mental model — two failure points, two defenses

  What it DOES (a tool argument)  -> ground the argument   (writes)
  What it SAYS (a spoken fact)    -> ground the speech      (reads)

- Writes: `@grounded` / `@grounded_outputs` verify a tool's arguments before the
  body runs. Ungrounded -> blocked + the agent is steered to re-ask.
- Reads: `render_spoken` builds a spoken line from grounded facts only and refuses
  if any fact is fabricated. saidso returns text; your TTS speaks it.

## Properties

- Fail-closed: a check that errors blocks; it never opens the gate.
- Deterministic & fast: pure Python, in-process, ~12us per write check.
- Zero required dependencies (rapidfuzz optional).
- Model- & platform-agnostic: no model SDK is imported anywhere.

## Next

  saidso docs quickstart      a runnable example
  saidso docs writes          guard tool arguments
  saidso docs reads           guard spoken facts
  saidso docs policies        SPOKEN / CONFIRMED / CALLER_ID / INFERABLE
  saidso docs changelog       what's new, fixed, and improved
  saidso docs --list          all topics
