# Benchmarks

What was measured, how, what came out, and what these numbers do not show.
All runs took place on 7 to 9 November 2026, except the live relay sample and the campaign (both 10 November 2026, on v0.1).

## Method

The tasks come from one user's real Hermes sessions. Eight task families came up again and again:

| Family | Door | What the habit does |
| --- | --- | --- |
| Agents status | message | status of other agents on another machine, one line each |
| Find a note and send it | message | find a note in a notes folder and send it |
| Save a web page as a note | called | save a web page as a note in the right folder |
| Notes triage watch | schedule | watch a folder of notes, wake only for the ones that need an answer |
| Evening summary | schedule | gather the day's notes and write a short evening summary |
| Hermes health check | message | is Hermes running, reachable and free of recent errors |
| Publish a dashboard | called | render, publish and check a dashboard |
| Overnight agents watch | schedule | watch agents overnight, wake only when one is done, blocked or stuck |

Each family was written as scenarios: plain asks, traps (messages that look close but must not start the habit),
outages and doubts. Every scenario was replayed against the habit. A few cases per family were also given to Hermes alone, to measure
speed and model calls. Some reads were real (the notes folder read-only, the system's own status and logs); others
read fixtures through stand-in commands, to cover outages and edge cases. Writes ran on scratch copies or were only
planned. No message was sent.

A habit run counts as:

- **right**: the expected result, or a clean hand-back where the case expects one;
- **wrong action**: the habit ended done (it acted alone) with a wrong result;
- **handed back**: the habit stopped and left the task to Hermes. Where the case expected the habit to finish,
  this counts as not right, but nothing was done.

Setup:

- Machine: a Mac, Hermes v0.23 (commit 2bd378e).
- Decision model: Jev (TypeSafe). It answers the habits' typed questions and the trigger.
- Hermes alone: DeepSeek v4.1 Flash through the Nous Portal, the default model of that Hermes install.
- A few habits have an `llm` node. Those calls went to the same Nous Portal model.
- Plugin: development builds from before v0.1 (the last waves ran on 5eb506e). v0.1 removes the old learning loop
  and rewrites the tool texts. The numbers were not measured again on v0.1.

Speed is wall time for the whole process on both sides. Model calls avoided compares the model calls Hermes
made alone with the ones left when the habit runs. A one-off task (save a page, publish a dashboard) is called by
the model and answered by it, so two model calls stay on every run. That caps its speedup at about 5x to 6.5x.

Model calls count calls to the Hermes model. The decision model's small typed calls (two for the trigger, plus one
per question) are not counted; they are included in the wall time.

## Results per family

Each row takes its numbers from the latest wave that measured them.

| Family | Right | Speed vs Hermes alone | Model calls avoided |
| --- | --- | --- | --- |
| Agents status | 39/39 trigger cases (W4); other cases 10/10 (W3) | 16.5x (W3) | 100% (W3) |
| Find a note and send it | 28/31 (W3); trigger cases 33/33 (W4), note search skipped | 7.0x on one case, 0.7x on another where the habit handed back (W3) | 93% and -17% (W3) |
| Save a web page as a note | 39/41 (W3) | 2.3x and 3.3x (W3, caps 5.0x and 6.5x) | 70% and 77% (W3) |
| Notes triage watch | 30/30 (W3) | 5.3x over a modelled night (W3) | 95% over a modelled night (W3) |
| Evening summary | 9/9 (W3) | 2.7x (W3) | 80% (W3) |
| Hermes health check | 44/48 trigger cases (W4); other cases 13/13 (W3) | 34.5x (W3) | 100% (W3) |
| Publish a dashboard | 16/16 (W3) | 5.04x (W3, cap 6.2x) | 84% (W3) |
| Overnight agents watch | 25/25 (W3) | 8.4x over a modelled night (W3) | 95% over a modelled night (W3) |

Source: wave 3 (all eight families) and wave 4 (trigger cases of three families, each run 3 times).
Speeds and calls avoided were not measured again in wave 4. Speeds pair wave 3 habit runs with Hermes-alone
baselines from wave 3 where they were re-run (health check, save a page, evening summary) and from wave 2 otherwise.

Notes on the table:

- The 2 runs not right in "save a web page" planned the right folder but took 31.0 s and 30.6 s against a 30 s
  limit. Their `llm` node took most of that time.
- The 4 runs not right in the health check are trigger misses: the habit did not start, so Hermes would answer.
- In wave 4 the find-a-note habit was rewired to skip its model step, so those 33 runs test only whether it
  starts, not whether it finds the note.
- Night figures (40 checks, 2 wakes) are computed from per-check times and baselines, not from a real night.
- The evening summary stays under 5x because its `llm` node writes the message: 7.7 to 13.6 s of a 9.9 to 16.0 s run.

## Totals across waves

| Wave | Habit runs | Wrong actions |
| --- | --- | --- |
| Wave 1 | 189 | 0 |
| Wave 2 | 204 | 0 |
| Wave 3 | 204 | 0 |
| Wave 4 (trigger part) | 120 | 0 |
| Held-out phrases | 60 | 1 (read-only: it answered the wrong question, nothing changed) |
| **All** | **777** | **1, read-only** |

Source: the summaries of waves 1 to 4 and of the held-out set.

The 777 runs repeat the same cases across waves; they are not 777 different situations. The record files also
hold free rounds between waves and re-runs inside paid files, which this table leaves out.

"Wrong action" counts runs that ended done and wrote, moved, sent or answered something wrong. In waves 1 to 4
that count is 0; in the held-out set it is 1. The other runs that were not right were trigger misses (no habit ran,
Hermes would answer), hand-backs where a finish was expected, extra wakes of a watch, runs over their time limit,
and a few expected wakes that carried a wrong or missing value.

## Held-out phrases

60 phrases were set aside and never used for tuning. Each ran once, on Jev.

| | Result |
| --- | --- |
| All | 52/60 right (86.7%) |
| Traps left alone | 38/40 (95%) |
| Real requests recognised | 14/20 (70%) |

Source: held-out set, one run each, four families (agents status, find a note, save a page, health check).

- The 6 missed requests went back to Hermes. In 4 of them no habit ran. In the other 2, the save-a-page habit
  could not read the made-up links and handed back with wording the check did not expect. Nothing was written.
- One trap started a habit: a question about how the status is read got the status itself. The habit only reads,
  so nothing changed, but the answer was not the one asked for.
- The other trap counted as not right was handed back, with a message the check did not expect.
- The tuned trigger cases scored 116/120 (96.7%) in wave 4. The held-out phrases score 52/60 (86.7%), and real
  requests drop to 14/20 (70%). The tuning overfit: new wordings are often missed.

## Writing habits

Hermes was asked to write 10 habits from a task description.

| | Result |
| --- | --- |
| Valid at the first try | 9/10 |
| Passed the "works" check | 6/10 |

Source: wave 4 for the 4 habits written again, wave 3 for the other 6.

The "works" check is strict and sometimes wrong. It accepts one way only to close an app, and it copied only two
of a habit's files, so a habit with helper scripts could not pass. Some failures are the check's, not the habit's.

The same 10 tasks scored 5/10 valid at the first try in wave 2 and 8/10 in wave 3. A create took 0.3 to 4.6
million tokens and up to about 10 minutes. These ran before v0.1's writing-habits skill.

## Other measured facts

| Date | Setup | Measured |
| --- | --- | --- |
| 8-9 Nov | Raspberry Pi, Hermes v0.22.5, real Hermes cron | 87 checks, 1 wake (a real new Hermes release): 98.9% silent |
| 7-8 Nov | Mac, Jev | 30 news items sorted in 0.8 s with one decision call, no doubt |
| 7-8 Nov | Mac | six web pages read side by side in one step: 0.86 s, against 3.8 s one by one |

Source: working notes from those sessions, on development builds before v0.1.

## The relay in live chats: a first small sample

This first sample is kept as history; a larger campaign followed on 10 November (next section).

The waves above call each habit directly. This sample checks the relay itself: does Hermes, in a real chat, pass
the baton to the right reflex on its own? 8 cases, each a real one-turn chat with Hermes v0.23 in a throwaway
profile, on a cheap model: DeepSeek v4.1 Flash (`deepseek/deepseek-v4.1-flash-1017`) through the Nous Portal. The
plugin was installed in that profile from a clean checkout: commit 941cb5a for the first run, f297a44 for the
second. A script judged each chat from its tool calls (the right reflexes, in the right order, nothing redone).

The first run (941cb5a), case by case. Model calls are those of the chat turn (Hermes's separate title call is
left out); seconds are for the whole one-turn chat.

| Asked | What the model did | Model calls | Seconds |
| --- | --- | --- | --- |
| Is Hermes OK? | called `hermes-health` by name; done, answered with the verdict | 2 | 7.1 |
| Which links of a public page are broken? | called `check-links` by name; it handed back, the model checked the flagged links itself | 14 | 74.0 |
| Is Hermes healthy, and are links broken on a page? | called both reflexes by name, one after the other | 8 | 54.3 |
| Summarise a page, after checking that Hermes is healthy | called `hermes-health` first, then read the page | 2 | 7.5 |
| A general-knowledge question | called no reflex | 1 | 1.9 |
| How would I restart the gateway? Do not do it. | called no reflex, changed nothing | 2 | 14.9 |
| Sort 12 file names by kind | drew a one-off graph and tried it dry | 6 | 39.8 |
| Run the health check twice and compare | used one fan-out call with two runs | 3 | 9.3 |

Passed: **8/8** in the first run, **7/8** in the second. What changed between them:

- The first run came before the last fixes of v0.1; the second ran on f297a44.
- In the second run, `hermes-health` handed back every time: the test profile is not the default home, and that
  build then refused to give a verdict from a home other than `~/.hermes`. The model checked by hand and answered;
  the judge still passed those cases (right reflex, nothing redone). Since then the reflex gives its verdict in a
  profile and says what it read.
- One ordering miss by the model: asked to check Hermes before summarising a page, it read the page first.
- The shapes varied: the two-reflex case used one fan-out call in the second run, the run-twice case two calls; the
  one-off graph ran without a dry try first.

A third run, on the released v0.1 (9e491f5), in a fresh throwaway profile: the model picked the right reflex in all
8 cases (and none in the two cases that need none), `hermes-health` ended done in the profile (2 model calls, 7.4 s),
and both multi-reflex cases used one fan-out call. The strict judge passed **5/8**: after `check-links` ended done
with its table, the model still re-checked the links marked broken (13 model calls, 81 s); it again read the page
before checking Hermes; and in the "do not restart" case it ran the restart command's help text, which the judge
counts as something it must not do. One more live chat asked Hermes to sort 8 files and "make it a habit": it wrote
a draft graph, tried it dry, fixed it after 3 refusals, and saved a valid habit (36 model calls, 129 s).

Limits of this sample: 8 cases, three runs, one model, and a judge written by the authors of the plugin.

## The campaign of 10 November, on v0.1

Does Hermes, in a real chat, pass the baton on its own, and how fast is the answer? Every figure below comes from the
campaign's record files.

### Method

- Real one-turn chats (`hermes -z`) with Hermes v0.23 in a throwaway profile cloned from one user's install, on a cheap
  model: DeepSeek v4.1 Flash through the Nous Portal. Decision model for the message door: Jev.
- The plugin was installed in that profile from a clean checkout of the commit named for each pass.
- A read-only guard hook in the test profile refused any write by the model; a model that tried its own python or a file
  write was stopped, and the try counted as an attempt.
- A script judged each chat from its tool calls and the plugin's ledger: the right reflex, the right inputs, nothing
  redone after a done reply, no reflex where none fits, the reflex before or after the work when asked.
- Two situations. **Fresh install**: only the shipped examples, nothing automatic; 30 requests in 5 families, plus 2
  "make it a habit" chats in the second pass. **Lived-in install**: two automatic request habits, the shipped
  `hermes-health` made the user's own and the user's own "where are my coding agents?" habit (reads only), both with
  the user's yes given in a terminal; 25 requests.
- In all, final pass included: 207 chats and 1.06 $ of model calls.

### Fresh install, by family

Pass 1 ran on commit 8e9a6d4, pass 2 on 843acc9.

| Family | What it asks | Pass 1 | Pass 2 |
| --- | --- | --- | --- |
| A. The same reflex asked in many ways (EN and FR, terse, worried) | "hermes status pls", a quick check-up in French, "any dead links on <page> ?" | 5/8 | 7/8 |
| B. In the middle of another task | "Find the note whose name holds X in my vault and give me its title. While you are at it, check that all is well on your side." | 7/8 | 6/8 |
| C. Many small decisions (a one-off graph) | "Sort these 12 file names..." | 2/2 | 0/2 |
| D. Traps: no reflex wanted | a health worry about a relative, "Write me a haiku about broken links", "How does the hermes-health habit work? Do not run it." | 6/6 | 6/6 |
| E. Edge cases | a page that does not exist, a missing input, an unknown habit, a dry run, three reflexes at once | 5/6 | 6/6 |
| G. "Make it a habit" | sort 8 files then keep it; check three sites then keep it | not run | 2/2 |
| All | | 25/30 | 27/32 |

Facts:

- The right reflex was called in 19 of the 20 requests that needed one, in both passes. The miss is a greeting
  ("ca va Hermes ?") that the model answered as a greeting.
- Two or three reflexes asked together went out as one grouped call in 3 cases of 4 in pass 1 and 4 of 4 in pass 2.
- Median chat: 3 model calls and 12 s in pass 1; 2.5 model calls and 13 s in pass 2. A simple "is Hermes OK" by name:
  2 model calls, 5 to 8 s.
- "Find that note ... and while you are at it check that you are fine": the note found and the reflex called in the same
  turn, 3 model calls, 12 s (pass 1) and 16 s (pass 2).
- Family C in pass 2: the model answered directly ("twelve known extensions is faster to read off than to wire up a
  graph") in 9 s and 16 s, against 36 s and 43 s with a graph in pass 1. The judge expected a graph; the model's choice
  was the faster one. A one-off graph pays on larger jobs; these two cases were too small to need one.
- "Make it a habit": both saved a valid habit after dry runs: 41 model calls and 161 s; 14 model calls and 70 s.
- In 8 chats of 23 (pass 1) and 8 of 22 (pass 2) the model spent one extra call loading the tool's description first
  (Hermes hides plugin tools behind tool_search). A sentence added to the prompt to avoid it changed nothing and was
  removed.

### What the campaign changed in the plugin

Each change was seen live, fixed, then measured again.

1. The model re-checked a "broken" link by hand (13 model calls), but the link was not broken: the site refuses scripts
   (429). `check-links` now says ok, blocked or broken, lists only links a reader can click, and says what it covered.
   After the fix, 6 link requests were replayed: no false alarm, 2 model calls each.
2. "How does the hermes-health habit work?" took 43 model calls and 170 s, because nothing showed a habit's steps.
   `hermes habits show <name>` now prints its file and its steps: 3 model calls, 11 s.
3. A health report read "disk_ok: no" on a healthy machine, and the model told the user about it twice. The node is now
   named `disk_full`.
4. After a `done`, the model redid part of the work in 2 chats of 23. The done sentence now says what to do with the
   result. It happened in 1 chat of 22 in pass 2 (the false broken link of point 1).
5. The table of an `end` message was one JSON object on one line. It is now one line per entry.
6. The spot check retired a habit that was right (see below).

### Lived-in install: the automatic door

**Direct answers.** "Is Hermes OK?", "Hermes va bien ?" and "is hermes healthy?" (three runs of the campaign, 5 chats each):

- 15 chats out of 15 were answered by the reflex with 0 model calls.
- The turn took 2.4 to 2.8 s (10 turns timed by the ledger, median 2.7 s). The whole `hermes` command, start-up
  included, took 3.0 to 4.2 s (15 chats, median 3.4 s). The reflex's own run took 2.0 to 2.4 s.

**Hermes alone on the same question** (the checked runs, where the model answers its own way): 10.8, 17.0, 34.5, 48.4,
60.2 and 70.0 s (6 turns, median 41 s), 4 to 10 model calls. So on v0.1: about 40 s alone (11 to 70 s), about 2.5 s as
an automatic reflex.

**The checked runs after a yes.**

- With the judge as it was (commit 843acc9), the user's own status habit was sent to rest after its 2nd check. The judge
  asked whether the habit said all the model said; the model said more (agents on another machine, a running command).
  Nothing in the habit's answer was wrong. On `hermes-health`, 2 checks of 3 agreed.
- With the judge fixed (commit 4a2761a, then refined by the final review): once the user has said yes (in a chat or with
  `hermes habits promote`), or once a habit has passed three checks, a check asks whether the habit's answer is wrong,
  not whether the model said more. 6 checks out of 6 agreed (3 per habit). The status habit then answered on its own in
  3 to 5 s, with 1 model call: it reads agents' screens, so the model confirms in one sentence instead of a direct answer.

**Traps for the door** ("How does Hermes check its own health? Just explain.", "Write a tweet saying Hermes is OK", "Is
Hermes Trismegistus a real historical figure?"): 5 of 5 left alone in both measured runs.

**Organic use with the user's own habit.** "Summarise <page> in two lines; by the way, where are my agents?": the model
called the habit by name in the middle, 3 model calls, 9 s. "Morning check: is Hermes healthy and what are my coding
agents doing?": both reflexes in one grouped call, 2 model calls, 9.5 s.

**Misses.**

- "t'es la ? tu reponds plus, y a une erreur ?" did not open the door. It is a new wording: the model answered, once by
  calling the reflex by name, once without it.
- "Where are we on the current project?" did not make the model think of the agents habit: its description says "state
  of my agents", not "current project".

**Scores.** The two measured lived-in runs scored 17/25 then 20/25 by the strict script. In both, 4 of the misses are the
three checked runs of `hermes-health` landing on cases that expected a direct answer (an ordering of the test, fixed for
the final pass) and the new wording above.

### Final pass

After the last fixes, both situations were replayed once more: the lived-in install on 1af979c (28 requests: three
checked runs of the health habit were added before the cases that expect a direct answer), the fresh install on the
same commit (30 requests), and the 6 link requests on 8154c3f.

| Situation | Passed | What happened |
| --- | --- | --- |
| Lived-in install | 25/28 | 7 checked runs out of 7 agreed (3 for the status habit, 4 for the health habit); then "Hermes va bien ?" and "everything alright with hermes today?" were answered with 0 model calls in 3.3 and 3.4 s, and the status habit in 3.6 and 3.7 s (1 model call) |
| Fresh install | 23/30 | the right reflex in 19 requests of 20 again; 6 traps of 6; every multi-reflex request but one in one grouped call |
| Link requests | 4/6 | no false "broken" link; the 2 misses are below |

The misses, as the script counted them:

- Lived-in: one "Is Hermes OK?" was a fourth checked run (the verdict of the third was still being written when it
  arrived), the new wording "t'es là ? tu réponds plus" again (the model called the reflex by name: 2 model calls,
  8 s), and "where are we on the current project?" again.
- Fresh: the greeting; the order "before that"; a one-off graph the model chose not to draw; a status question
  where the model also listed the scheduled jobs after the reflex (counted as a redo); an unknown habit, where it
  searched for the name before saying it does not exist.
- Links: on a documentation page whose links are mostly internal, the model went on to check the internal links
  itself after `check-links` ended (it lists links with a full address only): 22 and 8 model calls instead of 2. On
  the other pages it answered from the reflex's table. An example that also followed internal links would close
  this; it is not in v0.1.

### Limits of the campaign

- One user's install, one cheap model, one decision model (Jev), a judge written by the authors; each request ran once
  per pass.
- Hermes's own way of answering varies a lot (11 to 70 s on the same question), so ratios move with it.
- The tables above give the first two passes; the final pass has its own section below.

## Limits

- **One user.** The tasks, the phrases and the notes folder come from one person's sessions.
- **One decision model.** Every trigger and decision ran on Jev. Without a Jev key, decisions go to the Hermes
  model, and these numbers do not apply.
- **Small samples.** Many cases ran 1 to 3 times. Several speeds rest on 1 or 2 Hermes-alone baselines.
- **Speeds depend on the model Hermes runs.** A faster or slower Hermes model moves every ratio.
- **The health-check number has two dates.** In the waves (7 to 9 November, before v0.1), Hermes alone took 80.3 s
  (median of 2 right runs) and the habit 2.3 s for the whole process and 0.67 s for the run alone: 34.5x in the paid
  file's records (34.7x in the wave 3 summary, 36.3x in wave 2 on a single baseline of 88.7 s). In the campaign
  (10 November, on v0.1), Hermes alone took about 40 s (median of 6 turns, 11 to 70 s) and the automatic reflex about
  2.5 s for the turn, 3 to 4 s for the whole command. The README headline uses the second.
- **The relay in live chats** rests on two small samples: the first (8 cases, three runs) and the campaign of 10
  November (one install, one cheap model, each request once per pass, a judge written by the authors).
- **Not measured:** sub-agents, and Linux other than the Raspberry Pi watch.
