# Plan: from reflexes to a plugin anyone can install

Version 0.2 is the reflex, measured, and a Hermes plugin of two files that tells the agent which reflexes it has.
This page is the work from there to 1.0: habits, the dream, and what makes a plugin something a stranger installs
and keeps. The reasons for what exists are in [DESIGN.md](DESIGN.md); the measures in [the lab](../bench/README.md).

## The idea, in one picture

An agent's cognition is its loop, from the message it gets to the answer it gives. A habit is a reflex grafted at
one place of that loop, where the situation decides and no language has to be guessed. The model keeps what needs
understanding.

```text
clock ────► [1 watch] ── nothing ──► silence: the model is not woken
                └── something ──┐
message ───────────────────────►[2 spotting] ──► MODEL: understand, decide
                                                    │
                                 [3 closed choices] ▼
                                    tool ──► [4 follow-up and filter] ──► (loop)
                                                    │
                                 [5 check]          ▼
                                    MODEL: put it in words ──► answer

at rest: [6 the dream] reads everything again, and proposes
```

| Graft | What the model does today | What the habit does | Proven? |
| --- | --- | --- | --- |
| 1. Watch | a scheduled job wakes it, it reads its whole prompt, makes four reads, finds nothing new | the reflex makes the reads and compares with last time; the model is woken only for something, with the finding in hand | elsewhere: 87 checks, 1 wake |
| 2. Spotting | it finds out where it stands by trial | **selection**: the reflexes that concern this message are added to it; **preparation**: a reflex that only reads runs ahead and its result comes with the message | to measure |
| 3. Closed choices | it decides again what was decided | the reflex carries the convention; `rx.ask` puts closed questions to a small model, and a doubt goes up | measured (0 of 18 by hand, 20 of 20) |
| 4. Follow-up and filter | after A it nearly always does B, one more round trip; it reads 300 lines to find 3 | B runs by itself after A and comes back with A's result; a known output is cut to what matters, saying what was cut | to measure |
| 5. Check | it sometimes says "done" without checking | the check reflex runs; if it fails the agent goes back to work with the failure | to measure |
| 6. The dream | nothing: it has no rest | with no model, it finds what comes back, and puts at most three proposals before the user, each with its evidence | to build and measure |

## What "finished" means

1.0 is done when each line is true and shown, not when the code exists.

1. One command installs it. The first minute works with no key and nothing to configure.
2. Each graft is one line to turn on or off, and each has a measured gain in the lab on a cheap model. A graft
   without one is not in the plugin.
3. The dream proposes at most three things at a time, each with what it saw and what it would save. Nothing that
   changes anything runs without a yes, given once.
4. One screen, `/habits`, on every way of talking to Hermes: what runs, what is proposed, what rests; yes, no,
   wrong, undo.
5. One optional key, for the small model. Nothing else to set.
6. The README's numbers come from the lab, and its limits are said.
7. It runs on Linux and macOS, x86 and ARM, in CI.
8. Plugin, habits and dream together stay under about a thousand lines beyond the kernel.
9. Its owner has used it for two weeks in real life, and what that showed is fixed.

## Rules of the work

- **Measure before shipping.** Each piece gets a scenario in the lab and a number it must reach. A piece that
  misses is dropped, as the skill and the tool were.
- **The model keeps the last word.** A habit watches, prepares, follows up, filters or checks. It does not answer
  in the model's place.
- **A moment is a fact, not a guess.** An hour, a tool that just ran, an edit, a job on a schedule. Never the
  meaning of a sentence.
- **Reading is free, changing needs a yes.** A reflex that only reads can run ahead, beside the model, on trial.
  One that changes something runs by itself only after one yes, bound to what it does: edit it and the yes is gone.
- **The dream compiles, it does not invent.** It starts from what already comes back: scheduled jobs that find
  nothing, skills read again and again, reflexes called at the same moment, an action followed by its check.
- **Rest, do not delete.** What stops working or stops being used is put to rest, and can come back.
- **Use what Hermes has**: its scheduler and wake gate, its hooks, its idle tasks, its history reader, its counts
  of skill use. Build nothing it already does.
- **The smaller answer first.**

## Steps

Each step ends with its measure in the lab's README and a decision: keep, change or drop.

| Step | What is built | How the lab proves it | Kept if |
| --- | --- | --- | --- |
| 1. Ground | The form of a habit: how a reflex says when it runs, and where the yes is kept. The lab learns three things: ticks of the clock, several turns in one session, made-up histories. CI on macOS and ARM | models write the "when" line from the guide; the fake model plays a tick | models get the line right as often as they get a reflex right |
| 2. Watch | a reflex as the check before a scheduled job; a trial mode where the model is woken anyway and the two are compared | a job over many ticks with events planted among empty ones | every planted event wakes the model; nine empty ticks in ten cost nothing |
| 3. Spotting | selection of the reflexes for this message, beyond the 35 of the note; then preparation by a reflex that only reads | 63 reflexes with the right one cut from the note; short routines; requests that fit nothing | the right one is taken 20 times of 24 (0 today) and no wrong one; one request instead of two; an unrelated request is answered as before |
| 4. Around a tool | follow-up after a tool, a filter on a known output, the check before "done" | edit then check; a long noisy output; a task declared done with a failing check | one round trip less, eight times in ten; half the output's tokens gone and nothing asked for again; never "done" on a failure |
| 5. The dream | four readers of history with no model (jobs that find nothing, skills read often, reflexes called at one moment, action then check); proposals; the trial beside the model; upkeep (rest after three stops, rest when unused, a repair proposed) | made-up histories with routines planted among decoys; one proposal followed to an active habit and its saving; one real history, read only, counts only | nine proposals in ten are planted routines, seven planted in ten are found; nothing proposed from a decoy changes anything |
| 6. Trust | rights by effect (read, change here, reach outside); the yes bound to what the reflex does; history that came from outside (a web page) never feeds a habit that changes something; two adversarial reviews | reviewers try to make a habit change something without a yes, and to feed the dream a planted instruction | they fail, or what they find is closed and kept as a test |
| 7. The user's side | `/habits`; the first run; the key; the settings; the pages of the README | the first-minute check, extended: install, first proposal, yes, undo | a new home goes from install to a working habit with no file edited by hand |
| 8. Real life | two weeks on the owner's machine; then merge, tag, publish | the journal: what ran, what was skipped, what was wrong | the owner keeps it on |

Steps 2 to 4 do not depend on each other and can change order. Step 5 needs 2 to 4: the dream proposes habits, so
habits come first. Step 6 runs beside 2 to 5 and closes after them.

## Out of 1.0

- A habit that answers in the model's place. It is possible on messaging platforms only, and a first try recognised
  14 new requests of 20. Later, by choice, if at all.
- Habits inside subagents. They are kept out by default until something is measured.
- A second host.
- Trust decided by a model that judges another.
- Sharing reflexes between people.

## What is carried, not hidden

- **Where the yes lives.** The agent's terminal can write wherever its user can. A yes bound to a fingerprint
  survives an accident, not an agent that sets out to forge it. Step 6 says how far that goes, in plain words.
- **What the dream yields on a real history.** A first attempt elsewhere found little in chats (2 % of replies).
  Scheduled jobs and skills are the bet; one read-only count on a real history would tell.
- **Hermes learns by itself too** (skills it writes, a curator that archives them). The dream builds on that and
  must not fight it.
- **macOS and ARM** have run nothing yet.

## What is needed from the owner

- Decisions: the name of the command (`reflex` is also a web framework's), PyPI, the Hermes catalog, when to merge.
- The scheduled jobs in real use, one line each: the first watches come from there.
- One read-only count on a real history, if agreed.
- Two weeks of real use at the end.
