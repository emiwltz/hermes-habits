import threading

import pytest

from habits.ledger import Ledger, Rules
from habits.runner import Result

HABIT = "tidy"


class Clock:
    def __init__(self, t=1_000_000.0):
        self.t = t

    def __call__(self):
        return self.t


def ledger_at(tmp_path, clock=None, promote="auto"):
    """promote: auto, the thresholds alone, as before promote: ask (the tests of the yes ask for it)."""
    return Ledger(tmp_path / "ledger.db", Rules(promote=promote), clock=clock or Clock())


def result(planned=1, backends=("fake",), failed=False, status="end"):
    writes = [{"tool": "terminal", "args": {"command": f"mv ~/a{i} ~/b/"}} for i in range(planned)]
    return Result(status=status, message="Done.", node="done", failed=failed, planned=writes,
                  backends=list(backends), ms=12)


def compared_run(ledger, matched, planned=1, mode="shadow", version="v1", backends=("fake",)):
    run = ledger.record(HABIT, version, "message", mode, result(planned, backends))
    ledger.compared(run, matched)
    return run


def promoted(ledger, version="v1"):
    """An active habit: 12 shadow runs that all agree."""
    for _ in range(12):
        compared_run(ledger, 1, version=version)
        ledger.evaluate(HABIT, version)
    assert ledger.status(HABIT, version) == "active"
    return ledger


def test_a_new_habit_starts_in_shadow(tmp_path):
    ledger = ledger_at(tmp_path, Clock(500.0))
    assert ledger.status(HABIT, "v1") == "shadow"
    info = ledger.info(HABIT)
    assert (info["version"], info["status"], info["since"]) == ("v1", "shadow", 500.0)


def test_a_new_version_goes_back_to_shadow_with_fresh_scores(tmp_path):
    # Section 11: any change to the graph (its fingerprint) sends it back to shadow.
    ledger = promoted(ledger_at(tmp_path))
    assert ledger.status(HABIT, "v2") == "shadow"
    assert ledger.info(HABIT)["note"] == "new version"
    assert ledger.agreement(HABIT, "v2") == (None, 0)
    compared_run(ledger, 1, version="v2")
    assert ledger.evaluate(HABIT, "v2") == "shadow"


def test_a_retired_habit_comes_back_in_shadow_only_with_a_new_version(tmp_path):
    ledger = ledger_at(tmp_path)
    ledger.set_status(HABIT, "v1", "retired")
    for _ in range(12):
        compared_run(ledger, 1)
    assert ledger.evaluate(HABIT, "v1") == "retired"
    assert ledger.status(HABIT, "v2") == "shadow"


def test_record_stores_the_run_and_its_trace(tmp_path):
    ledger = ledger_at(tmp_path, Clock(42.0))
    run = ledger.record(HABIT, "v1", "message", "shadow", result(3, ("jev", "fake", "jev")), turn_ms=900, woke=True)
    row = ledger.runs(HABIT)[0]
    assert row["id"] == run
    assert (row["mode"], row["at"], row["planned"], row["s1"], row["turn_ms"], row["woke"]) == (
        "shadow", 42.0, 3, "fake,jev", 900, 1)
    assert row["compared"] == 0
    assert ledger.last_trace(HABIT)["planned"][0]["tool"] == "terminal"


def test_runs_lists_the_newest_first(tmp_path):
    ledger = ledger_at(tmp_path)
    ids = [ledger.record(HABIT, "v1", "message", "shadow", result()) for _ in range(4)]
    assert [r["id"] for r in ledger.runs(HABIT, limit=3)] == ids[:0:-1]


def test_agreement_is_pooled_not_a_mean_of_percentages(tmp_path):
    # Section 11, step 4: matched writes / planned writes, summed over the compared runs.
    ledger = ledger_at(tmp_path)
    compared_run(ledger, 0, planned=1)
    compared_run(ledger, 0, planned=1)
    for _ in range(10):
        compared_run(ledger, 10, planned=10)
    assert ledger.agreement(HABIT, "v1") == (100 / 102, 12)
    assert ledger.evaluate(HABIT, "v1") == "active"


def test_a_few_big_misses_outweigh_many_small_hits(tmp_path):
    ledger = ledger_at(tmp_path)
    for _ in range(11):
        compared_run(ledger, 1, planned=1)
    compared_run(ledger, 0, planned=10)
    assert ledger.agreement(HABIT, "v1") == (11 / 21, 12)
    assert ledger.evaluate(HABIT, "v1") == "shadow"


def test_runs_without_planned_writes_or_comparison_do_not_count(tmp_path):
    # Section 11, step 4: a run without a planned write does not count.
    ledger = ledger_at(tmp_path)
    compared_run(ledger, 0, planned=0)
    ledger.record(HABIT, "v1", "message", "shadow", result(2))
    ledger.record(HABIT, "v1", "adhoc", "adhoc", result(2))
    compared_run(ledger, 1, planned=2)
    assert ledger.agreement(HABIT, "v1") == (0.5, 1)


def test_runs_answered_by_the_llm_fallback_do_not_count(tmp_path):
    # Section 10: the fallback LLM declares its own confidence, so its runs leave the agreement out.
    ledger = ledger_at(tmp_path)
    for _ in range(12):
        compared_run(ledger, 0, backends=("llm",))
    compared_run(ledger, 1, backends=("jev",))
    assert ledger.agreement(HABIT, "v1") == (1.0, 1)
    assert ledger.evaluate(HABIT, "v1") == "shadow"


def test_agreed_counts_the_runs_that_agree_on_their_own_since_the_last_shadow(tmp_path):
    # An active habit is checked on every run until 3 agree. A run the LLM fallback decided counts there (the
    # judge compared its answer), unlike in the scores; one under the promotion bar does not.
    ledger = ledger_at(tmp_path)
    ledger.set_status(HABIT, "v1", "active", "saved active")
    compared_run(ledger, 1, mode="spot", backends=("llm",))
    compared_run(ledger, 2, planned=3, mode="spot")  # 67 %: not agreeing on its own
    compared_run(ledger, 1, mode="spot")
    ledger.record(HABIT, "v1", "message", "spot", result())  # never compared
    assert ledger.agreed(HABIT, "v1") == 2
    ledger.set_status(HABIT, "v1", "shadow", "reset")
    assert ledger.agreed(HABIT, "v1") == 0
    assert promoted(ledger_at(tmp_path / "b")).agreed(HABIT, "v1") == 12


def test_runs_that_asked_no_question_still_count(tmp_path):
    # Section 10 only excludes the LLM fallback; section 11 counts every compared run with planned writes.
    ledger = ledger_at(tmp_path)
    for _ in range(12):
        compared_run(ledger, 1, backends=())
    assert ledger.agreement(HABIT, "v1") == (1.0, 12)
    assert ledger.evaluate(HABIT, "v1") == "active"


def test_extra_matches_never_push_agreement_above_one(tmp_path):
    # Section 11, step 5: the LLM's extra writes do not count, for or against the habit.
    ledger = ledger_at(tmp_path)
    compared_run(ledger, 5, planned=2)
    assert ledger.agreement(HABIT, "v1") == (1.0, 1)


def test_compared_can_correct_the_planned_count(tmp_path):
    ledger = ledger_at(tmp_path)
    run = ledger.record(HABIT, "v1", "message", "shadow", result(2))
    ledger.compared(run, 3, judged=1, planned=4)
    row = ledger.runs(HABIT)[0]
    assert (row["planned"], row["matched"], row["judged"], row["compared"]) == (4, 3, 1, 1)
    assert ledger.agreement(HABIT, "v1") == (0.75, 1)


def test_promotion_needs_twelve_compared_runs(tmp_path):
    # Section 11: shadow -> active after at least 12 compared runs at 90 % or more.
    ledger = ledger_at(tmp_path)
    for _ in range(11):
        compared_run(ledger, 1)
        assert ledger.evaluate(HABIT, "v1") == "shadow"
    compared_run(ledger, 1)
    assert ledger.evaluate(HABIT, "v1") == "active"
    assert ledger.info(HABIT)["note"] == "100% over 12 runs"


def test_promotion_at_exactly_ninety_percent(tmp_path):
    ledger = ledger_at(tmp_path)
    for _ in range(12):
        compared_run(ledger, 9, planned=10)
    assert ledger.evaluate(HABIT, "v1") == "active"


def test_no_promotion_at_eighty_nine_percent(tmp_path):
    ledger = ledger_at(tmp_path)
    compared_run(ledger, 1, planned=12)
    for _ in range(11):
        compared_run(ledger, 8, planned=8)
    assert ledger.agreement(HABIT, "v1") == (0.89, 12)
    assert ledger.evaluate(HABIT, "v1") == "shadow"


def spots(ledger, *pairs):
    for planned, matched in pairs:
        compared_run(ledger, matched, planned=planned, mode="spot")
        status = ledger.evaluate(HABIT, "v1")
    return status


def test_an_active_habit_retires_under_75_percent_over_the_last_20(tmp_path):
    # Section 11: active -> retired when agreement is under 75 % over the last 20 comparisons.
    ledger = promoted(ledger_at(tmp_path))
    assert spots(ledger, (3, 3), (3, 0), (3, 3), (3, 0), (3, 3), (3, 0), (3, 3)) == "active"
    assert spots(ledger, (3, 0)) == "retired"
    assert ledger.info(HABIT)["note"] == "67% over the last 20"


def test_an_active_habit_at_exactly_75_percent_stays_active(tmp_path):
    ledger = promoted(ledger_at(tmp_path))
    assert spots(ledger, *[(3, 3), (2, 0)] * 4) == "active"
    assert ledger.agreement(HABIT, "v1", 20) == (0.75, 20)


def test_two_low_spot_checks_in_a_row_retire(tmp_path):
    # Section 11: two spot checks in a row under 75 % retire an active habit.
    ledger = promoted(ledger_at(tmp_path))
    assert spots(ledger, (4, 2)) == "active"
    assert spots(ledger, (4, 2)) == "retired"
    assert ledger.info(HABIT)["note"] == "2 spot checks in a row under 75%"


def test_low_spot_checks_that_are_not_in_a_row_do_not_retire(tmp_path):
    ledger = promoted(ledger_at(tmp_path))
    assert spots(ledger, (4, 2), (4, 4), (4, 2)) == "active"


def test_spot_checks_at_75_percent_do_not_retire(tmp_path):
    ledger = promoted(ledger_at(tmp_path))
    assert spots(ledger, (4, 3), (4, 3)) == "active"


def test_a_good_spot_check_measures_without_changing_the_status(tmp_path):
    ledger = promoted(ledger_at(tmp_path))
    assert spots(ledger, (2, 2)) == "active"
    assert ledger.agreement(HABIT, "v1") == (1.0, 13)


def test_spot_checks_answered_by_the_llm_fallback_do_not_retire(tmp_path):
    # Section 10: runs answered by the fallback LLM are excluded from the agreement.
    ledger = promoted(ledger_at(tmp_path))
    for _ in range(2):
        compared_run(ledger, 0, planned=2, mode="spot", backends=("llm",))
    assert ledger.evaluate(HABIT, "v1") == "active"


def active(ledger, failed):
    ledger.record(HABIT, "v1", "message", "active", result(0, failed=failed, status="wake" if failed else "end"))
    return ledger.evaluate(HABIT, "v1")


def test_three_failed_active_runs_in_a_row_retire(tmp_path):
    # Section 11: three errors in a row retire an active habit.
    ledger = promoted(ledger_at(tmp_path))
    assert [active(ledger, True) for _ in range(3)] == ["active", "active", "retired"]
    assert ledger.info(HABIT)["note"] == "3 errors in a row"


def test_a_decision_model_outage_never_counts_toward_retirement(tmp_path):
    # No S1 answered: the source of the habit's decisions was down, not the habit (evening-prep at 21 h).
    ledger = promoted(ledger_at(tmp_path))

    def down():
        failed = result(0, failed=True, status="wake")
        failed.outage = True
        ledger.record(HABIT, "v1", "message", "active", failed)
        return ledger.evaluate(HABIT, "v1")
    assert [down() for _ in range(4)] == ["active"] * 4
    assert [active(ledger, True), down(), active(ledger, True), down(), active(ledger, True)] == [
        "active", "active", "active", "active", "retired"]  # an outage neither counts nor breaks the row


def test_a_retirement_by_the_thresholds_is_told_once_and_a_reset_forgets_it(tmp_path):
    ledger = promoted(ledger_at(tmp_path))
    assert ledger.untold() == []
    for _ in range(3):
        active(ledger, True)
    assert ledger.untold() == [(HABIT, "3 errors in a row")]
    assert ledger.untold() == []  # told once, by whichever reply or wake comes first
    other = promoted(ledger_at(tmp_path / "b"))
    for _ in range(3):
        active(other, True)
    other.set_status(HABIT, "v1", "shadow", "reset by the user")
    assert other.untold() == []  # back before anyone was told: nothing to say
    other.set_status(HABIT, "v1", "retired", "retired by the user")
    assert other.untold() == []  # a person retired it: they know


def test_failures_broken_by_a_success_do_not_retire(tmp_path):
    ledger = promoted(ledger_at(tmp_path))
    for failed in (True, True, False, True, True):
        assert active(ledger, failed) == "active"


def ended(ledger, status="wake", mode="active", failed=False, kind="message"):
    ledger.record(HABIT, "v1", kind, mode, result(0, failed=failed, status=status))
    return ledger.evaluate(HABIT, "v1")


def test_three_wakes_in_a_row_send_an_active_habit_back_to_shadow(tmp_path):
    ledger = promoted(ledger_at(tmp_path))
    assert [ended(ledger), ended(ledger)] == ["active", "active"]
    assert ended(ledger, failed=True) == "active"  # a failure counts toward retirement, not here
    assert ended(ledger, mode="spot") == "active"  # a spot check that wakes is not an end either
    assert ended(ledger) == "shadow"
    info = ledger.info(HABIT)
    assert (info["version"], info["note"]) == ("v1", "woke the model 3 times in a row")
    assert ledger.agreement(HABIT, "v1") == (None, 0)  # a new shadow period of the same version


def test_an_end_between_wakes_keeps_the_habit_active(tmp_path):
    ledger = promoted(ledger_at(tmp_path))
    for status, mode in (("wake", "active"), ("wake", "active"), ("end", "spot"), ("wake", "active"),
                         ("wake", "active"), ("end", "active"), ("wake", "active"), ("wake", "active")):
        assert ended(ledger, status, mode) == "active", (status, mode)


def test_a_watch_that_wakes_stays_active(tmp_path):
    ledger = promoted(ledger_at(tmp_path))
    assert [ended(ledger, kind="watch") for _ in range(4)] == ["active"] * 4  # waking is its job


def test_status_is_kept_per_version(tmp_path):
    ledger = promoted(ledger_at(tmp_path))
    for _ in range(3):
        compared_run(ledger, 0, version="v2")
    assert ledger.status(HABIT, "v2") == "shadow"
    assert ledger.agreement(HABIT, "v2") == (0.0, 3)
    assert ledger.agreement(HABIT, "v1") == (1.0, 12)


def test_going_back_to_an_earlier_graph_starts_from_zero(tmp_path):
    # Section 11: promotion needs 12 compared runs since the last change of the graph.
    ledger = promoted(ledger_at(tmp_path))
    assert ledger.status(HABIT, "v2") == "shadow"
    assert ledger.status(HABIT, "v1") == "shadow"
    assert ledger.evaluate(HABIT, "v1") == "shadow"


def test_gain_compares_median_turn_times_of_shadow_and_active(tmp_path):
    # Section 11: the gain is the ratio of the median whole-turn times.
    ledger = ledger_at(tmp_path)
    for ms in (3000, 1000, 2000):
        ledger.record(HABIT, "v1", "message", "shadow", result(), turn_ms=ms)
    ledger.record(HABIT, "v1", "message", "shadow", result())
    run = ledger.record(HABIT, "v1", "message", "active", result())
    ledger.set_turn(run, 400)
    ledger.record(HABIT, "v1", "message", "spot", result(), turn_ms=9000)
    ledger.record(HABIT, "v2", "message", "active", result(), turn_ms=1)
    # A spot check is a turn of the LLM alone, like shadow: median of 1000, 2000, 3000, 9000.
    assert ledger.gain(HABIT, "v1") == (2500, 400)
    assert ledger.gain(HABIT, "v3") == (None, None)


def test_gain_median_of_an_even_count(tmp_path):
    ledger = ledger_at(tmp_path)
    for ms in (1000, 2000):
        ledger.record(HABIT, "v1", "message", "shadow", result(), turn_ms=ms)
    assert ledger.gain(HABIT, "v1") == (1500, None)


def test_summary(tmp_path):
    ledger = promoted(ledger_at(tmp_path, Clock(7.0)))
    ledger.record(HABIT, "v1", "message", "active", result(0), turn_ms=500, woke=True)
    ledger.record(HABIT, "v1", "message", "shadow", result(1), turn_ms=4000)
    assert ledger.summary(HABIT, "v1") == {
        "status": "active", "since": 7.0, "note": "100% over 12 runs", "agreement": 1.0, "compared": 12,
        "runs": 14, "woke": 1, "shadow_ms": 4000, "active_ms": 500, "shadow_tokens": None, "active_tokens": None,
        "shadow_calls": None, "active_calls": None, "saved": {"runs": 1, "ms": 3500, "tokens": None},
        "origin": "asked", "ready": False}


def test_turn_tokens_and_calls(tmp_path):
    ledger = ledger_at(tmp_path)
    alone = [ledger.record(HABIT, "v1", "message", "shadow", result()) for _ in range(3)]
    for run_id, (ms, tokens, calls) in zip(alone, ((6000, 40_000, 8), (5000, 30_000, 6), (7000, 50_000, 9))):
        ledger.set_turn(run_id, ms, tokens, calls)
    direct = ledger.record(HABIT, "v1", "message", "active", result())
    ledger.set_turn(direct, 2000, 0, 0)
    told = ledger.record(HABIT, "v1", "message", "active", result())
    ledger.set_turn(told, 3000, 12_000, 1)
    assert ledger.gain(HABIT, "v1", "turn_tokens") == (40_000, 6000)
    assert ledger.gain(HABIT, "v1", "turn_calls") == (8, 0)  # median of 0 and 1, rounded half to even
    # Each active turn saved the LLM's median turn minus its own: (6000-2000)+(6000-3000), 40k+28k tokens.
    assert ledger.saved(HABIT, "v1") == {"runs": 2, "ms": 7000, "tokens": 68_000}
    summary = ledger.summary(HABIT, "v1")
    assert (summary["shadow_tokens"], summary["active_tokens"]) == (40_000, 6000)


def test_gain_refuses_an_unknown_column(tmp_path):
    with pytest.raises(ValueError):
        ledger_at(tmp_path).gain(HABIT, "v1", "trace; DROP TABLE runs")


def test_old_ledger_gains_the_token_columns(tmp_path):
    import sqlite3
    path = tmp_path / "old.db"
    db = sqlite3.connect(path)
    db.execute("CREATE TABLE runs (id INTEGER PRIMARY KEY, habit TEXT, version TEXT, kind TEXT, mode TEXT, at REAL, "
               "ms INTEGER, turn_ms INTEGER, status TEXT, reason TEXT, failed INTEGER, s1 TEXT, planned INTEGER, "
               "matched INTEGER, judged INTEGER, compared INTEGER DEFAULT 0, woke INTEGER DEFAULT 0, trace TEXT)")
    db.commit()
    db.close()
    ledger = Ledger(path)
    run_id = ledger.record(HABIT, "v1", "message", "shadow", result())
    ledger.set_turn(run_id, 1000, 500, 2)
    assert ledger.runs(HABIT)[0]["turn_tokens"] == 500


def test_summary_of_an_unknown_habit(tmp_path):
    summary = ledger_at(tmp_path).summary("nothing", "v1")
    assert (summary["status"], summary["runs"], summary["compared"], summary["agreement"]) == ("shadow", 0, 0, None)


def test_summary_of_a_changed_graph_shows_shadow(tmp_path):
    # Section 11: any change to the graph sends it back to shadow.
    ledger = promoted(ledger_at(tmp_path))
    assert ledger.summary(HABIT, "v2")["status"] == "shadow"


def test_memory_is_kept_per_habit_and_node(tmp_path):
    ledger = ledger_at(tmp_path)
    assert ledger.memory(HABIT) == {}
    ledger.remember(HABIT, {"seen": "a", "count": "1"})
    ledger.remember(HABIT, {"seen": "b"})
    ledger.remember("other", {"seen": "z"})
    assert ledger.memory(HABIT) == {"seen": "b", "count": "1"}
    assert ledger.memory("other") == {"seen": "z"}


def test_failed_checks_count_in_a_row_until_one_works(tmp_path):
    ledger = ledger_at(tmp_path)
    assert [ledger.failed_checks(HABIT, failed) for failed in (True, True, False, True, True, True, True)] == [
        1, 2, 0, 1, 2, 3, 4]
    assert ledger.failed_checks("other", True) == 1  # each watch its own row
    assert Ledger(tmp_path / "ledger.db").failed_checks(HABIT, True) == 5  # in the file: each tick is a new process


def test_take_calls_returns_a_sessions_calls_once(tmp_path):
    clock = Clock()
    ledger = ledger_at(tmp_path, clock)
    for tool, session in (("terminal", "s1"), ("write_file", "s2"), ("send_message", "s1")):
        ledger.note_call(session, tool, {"x": tool})
        clock.t += 1
    assert ledger.take_calls("s1") == [{"tool": "terminal", "args": {"x": "terminal"}},
                                       {"tool": "send_message", "args": {"x": "send_message"}}]
    assert ledger.take_calls("s1") == []
    assert ledger.take_calls("s2") == [{"tool": "write_file", "args": {"x": "write_file"}}]


def test_take_calls_forgets_other_sessions_after_a_day(tmp_path):
    clock = Clock()
    ledger = ledger_at(tmp_path, clock)
    ledger.note_call("old", "terminal", {})
    clock.t += 86_000
    ledger.note_call("recent", "terminal", {})
    clock.t += 1_000
    ledger.take_calls("now")
    assert ledger.take_calls("old") == []
    assert ledger.take_calls("recent") == [{"tool": "terminal", "args": {}}]


def test_a_long_call_keeps_its_arguments(tmp_path):
    # Section 11, step 3: two gestures match when the tool and the normalized arguments are equal.
    ledger = ledger_at(tmp_path)
    args = {"path": "~/notes.md", "content": "x" * 5000}
    ledger.note_call("s1", "write_file", args)
    assert ledger.take_calls("s1") == [{"tool": "write_file", "args": args}]


def test_several_threads_and_ledgers_can_record_at_once(tmp_path):
    ledgers = [ledger_at(tmp_path), ledger_at(tmp_path)]
    errors = []

    def work(ledger, n):
        try:
            for _ in range(25):
                compared_run(ledger, 1, planned=2)
                ledger.note_call(f"s{n}", "terminal", {"n": n})
                ledger.evaluate(HABIT, "v1")
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=work, args=(ledgers[n % 2], n)) for n in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    assert ledgers[0].agreement(HABIT, "v1") == (0.5, 200)
    assert ledgers[1].summary(HABIT, "v1")["runs"] == 200
    assert len(ledgers[0].take_calls("s3")) == 25


def test_a_ledger_deleted_under_a_live_process_starts_again(tmp_path):
    # The Pi gateway lives for days: deleting habits.db must not break every habit until a restart.
    ledger = ledger_at(tmp_path)
    promoted(ledger)
    for suffix in ("", "-wal", "-shm"):
        (tmp_path / f"ledger.db{suffix}").unlink(missing_ok=True)
    assert ledger.status(HABIT, "v1") == "shadow"  # statuses before the deletion are lost
    assert ledger.record(HABIT, "v1", "message", "shadow", result()) == 1


def test_a_ledger_whose_folder_is_deleted_starts_again(tmp_path):
    # rm -rf ~/.hermes/plugin-data/habits to start over: sqlite cannot create a file in a missing folder.
    import shutil
    ledger = ledger_at(tmp_path / "habits")
    promoted(ledger)
    shutil.rmtree(tmp_path / "habits")
    assert ledger.status(HABIT, "v1") == "shadow"


# ---- the learning layer (PLUGIN-LAYER.md, component 1) ------------------------

def ready(ledger, version="v1", runs=12):
    """promote: ask: 12 agreeing shadow runs, each one evaluated."""
    for _ in range(runs):
        compared_run(ledger, 1, version=version)
        assert ledger.evaluate(HABIT, version) == "shadow"
    return ledger


def test_ask_mode_keeps_a_ready_habit_in_shadow_with_one_ready_row(tmp_path):
    ledger = ready(ledger_at(tmp_path, promote="ask"))
    assert ledger.status(HABIT, "v1") == "shadow" and ledger.summary(HABIT, "v1")["ready"] is True
    compared_run(ledger, 1)
    assert ledger.evaluate(HABIT, "v1") == "shadow"  # it keeps learning, and is not told again
    assert [(n["habit"], n["version"], n["kind"], n["note"]) for n in ledger.news(take=False)] == [
        (HABIT, "v1", "ready", "100% over 12 runs")]
    assert ledger.untold() == []  # the old channel tells retirements only
    assert len(ledger.news()) == 1 and ledger.news() == []  # taken once


def test_a_bad_promote_value_waits_for_the_yes(tmp_path):
    ledger = Ledger(tmp_path / "l.db", Rules(promote="sometimes"), clock=Clock())
    assert ready(ledger).status(HABIT, "v1") == "shadow"


def test_two_threads_meeting_the_thresholds_give_one_ready_row(tmp_path):
    ledgers = [ledger_at(tmp_path, promote="ask"), ledger_at(tmp_path, promote="ask")]
    for _ in range(12):
        compared_run(ledgers[0], 1)
    gate = threading.Barrier(8)

    def work(ledger):
        gate.wait()
        for _ in range(5):
            ledger.evaluate(HABIT, "v1")

    threads = [threading.Thread(target=work, args=(ledgers[n % 2],)) for n in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert [n["kind"] for n in ledgers[1].news(take=False)] == ["ready"]


def test_auto_mode_promotes_as_before_and_says_who(tmp_path):
    ledger = promoted(ledger_at(tmp_path))
    assert ledger.news(take=False) == []  # a promotion by the thresholds alone is no news
    assert [(h["from"], h["to"], h["by"]) for h in ledger.history(HABIT)] == [
        ("shadow", "active", "rules"), (None, "shadow", "habits")]


def test_the_yes_makes_this_version_active_and_keeps_its_scores(tmp_path):
    clock = Clock()
    ledger = ready(ledger_at(tmp_path, clock, promote="ask"))
    clock.t += 60
    assert ledger.approve(HABIT, "v1") is True
    info = ledger.info(HABIT)
    assert (info["status"], info["note"], info["since"]) == ("active", "approved", clock.t)
    assert ledger.history(HABIT)[0] | {"id": 0} == {"id": 0, "habit": HABIT, "version": "v1", "from": "shadow",
                                                    "to": "active", "by": "user", "note": "approved", "at": clock.t}
    assert ledger.agreement(HABIT, "v1") == (1.0, 12) and ledger.agreed(HABIT, "v1") == 12
    assert ledger.news(take=False) == []  # the ready line nobody read is moot
    assert ledger.approve(HABIT, "v1") is False  # already active: nothing moves


def test_the_yes_fails_after_a_version_change(tmp_path):
    ledger = ready(ledger_at(tmp_path, promote="ask"))
    assert ledger.status(HABIT, "v2") == "shadow"  # the file changed while the card was open
    assert ledger.approve(HABIT, "v1") is False
    assert ledger.status(HABIT, "v2") == "shadow" and ledger.summary(HABIT, "v2")["ready"] is False
    assert ledger.news(take=False) == []  # one ready row per version: v1's went with it
    assert ledger.approve(HABIT, "v2") is True  # a yes to what is there now (the CLI's promote)


def test_later_asks_again_after_half_the_runs(tmp_path):
    ledger = ready(ledger_at(tmp_path, promote="ask"))
    assert ledger.later(HABIT, "v1") is True and ledger.later(HABIT, "v1") is False
    assert ledger.news(take=False) == [] and ledger.summary(HABIT, "v1")["ready"] is False
    ready(ledger, runs=5)
    assert ledger.news(take=False) == []
    ready(ledger, runs=1)
    assert [n["kind"] for n in ledger.news(take=False)] == ["ready"]  # asked again, after 6 more runs


def test_adopt_records_an_asked_habit_and_tells_nothing(tmp_path):
    ledger = ledger_at(tmp_path)
    ledger.adopt("asked-one", "a1", "asked")
    assert ledger.info("asked-one")["origin"] == "asked" and ledger.history("asked-one")[0]["by"] == "asked"
    assert ledger.status("asked-one", "a1") == "shadow" and ledger.news() == []
    for origin in ("review", "portal"):  # "learned" is accepted since the nursery
        with pytest.raises(ValueError):
            ledger.adopt("x", "v1", origin)


def test_news_kinds_are_taken_once_by_whoever_tells_them(tmp_path):
    ledger = promoted(ledger_at(tmp_path))
    for _ in range(3):
        ended(ledger)
    assert [(n["kind"], n["note"]) for n in ledger.news(take=False)] == [("demoted", "woke the model 3 times in a row")]
    ledger.retire("other", "v1", "3 errors in a row")
    rows = ledger.news(take=False)
    assert ledger.news(names=["other"], kinds=["ready"], take=False) == []
    assert ledger.told([rows[1]["id"]]) == [rows[1]["id"]] and ledger.told([rows[1]["id"]]) == []
    assert [n["kind"] for n in ledger.news()] == ["demoted"]


def test_a_retirement_seen_twice_is_told_once(tmp_path):
    ledger = promoted(ledger_at(tmp_path))
    ledger.retire(HABIT, "v1", "3 errors in a row")
    ledger.retire(HABIT, "v1", "3 errors in a row")
    assert ledger.untold() == [(HABIT, "3 errors in a row")] and ledger.untold() == []


def test_wrong_sends_this_sessions_last_answer_back_to_shadow_as_a_disagreement(tmp_path):
    ledger = promoted(ledger_at(tmp_path))
    mine = ledger.record(HABIT, "v1", "request", "active", result(0), session="s1")
    ledger.record(HABIT, "v1", "request", "spot", result(0), session="s1")  # the model answered that one
    ledger.record(HABIT, "v1", "request", "active", result(0), session="s2")
    assert ledger.wrong("nobody") is None
    assert ledger.wrong("s1") == HABIT
    run = next(r for r in ledger.runs(HABIT, 10) if r["id"] == mine)
    assert (run["compared"], run["planned"], run["matched"], run["verdict"]) == (1, 1, 0, "wrong, said the user")
    assert ledger.status(HABIT, "v1") == "shadow"
    assert ledger.agreement(HABIT, "v1") == (0.0, 1)  # the new scores start with that disagreement
    assert ledger.history(HABIT)[0]["by"] == "user"
    assert ledger.wrong("s1") == HABIT and ledger.status(HABIT, "v1") == "shadow"  # again: counted, nothing moves


def test_undo_restores_the_state_before_the_users_last_change_within_24_hours(tmp_path):
    clock = Clock()
    ledger = ready(ledger_at(tmp_path, clock, promote="ask"))
    assert ledger.undo() is None  # nothing the user did
    ledger.approve(HABIT, "v1")
    clock.t += 3600
    undone = ledger.undo()
    assert (undone["habit"], undone["from"], undone["to"], undone["note"]) == (HABIT, "shadow", "active", "approved")
    assert ledger.status(HABIT, "v1") == "shadow"
    assert ledger.summary(HABIT, "v1")["ready"] is True and ledger.agreement(HABIT, "v1") == (1.0, 12)  # as before
    assert ledger.news(take=False) == []  # ready already, not told again
    assert ledger.undo()["to"] == "shadow" and ledger.status(HABIT, "v1") == "active"  # a second undo puts it back
    clock.t += 86_401
    assert ledger.undo() is None  # more than 24 h ago


def test_undo_refuses_once_the_habit_moved_on(tmp_path):
    ledger = ready(ledger_at(tmp_path, promote="ask"))
    ledger.approve(HABIT, "v1")
    ledger.status(HABIT, "v2")  # a new version since the yes
    assert ledger.undo() is None and ledger.status(HABIT, "v2") == "shadow"
    ledger.set_status("other", "v1", "retired", "at rest", by="user")
    assert ledger.undo(HABIT) is None and ledger.undo("other") is None  # first sight was from nothing


def test_an_old_db_migrates(tmp_path):
    import sqlite3
    path = tmp_path / "old.db"
    db = sqlite3.connect(path)
    db.executescript("""
        CREATE TABLE runs (id INTEGER PRIMARY KEY, habit TEXT, version TEXT, kind TEXT, mode TEXT, at REAL,
          ms INTEGER, turn_ms INTEGER, status TEXT, reason TEXT, failed INTEGER, s1 TEXT, planned INTEGER,
          matched INTEGER, judged INTEGER, compared INTEGER DEFAULT 0, woke INTEGER DEFAULT 0, trace TEXT,
          turn_tokens INTEGER, turn_calls INTEGER, outage INTEGER DEFAULT 0, verdict TEXT);
        CREATE TABLE habits (habit TEXT PRIMARY KEY, version TEXT, status TEXT, since REAL, note TEXT,
          allowed INTEGER DEFAULT 0, start REAL, news INTEGER DEFAULT 0);
        INSERT INTO habits VALUES ('tidy', 'v1', 'active', 5.0, 'by hand', 0, 0, 0);
        INSERT INTO habits VALUES ('gone', 'g1', 'retired', 6.0, '3 errors in a row', 0, 0, 1);
        INSERT INTO runs (habit, version, kind, mode, at, planned, matched, compared, s1)
          VALUES ('tidy', 'v1', 'request', 'active', 4.0, 1, 1, 1, 'fake');""")
    db.commit()
    db.close()
    for _ in range(2):  # the gateway and a cron tick open it, one after the other
        ledger = Ledger(path, clock=Clock())
    with ledger._db() as db:
        Ledger._add(db, "runs", "session", "TEXT")  # the other one added it first: no error
    assert ledger.info("tidy")["origin"] == "asked" and ledger.status("tidy", "v1") == "active"
    assert ledger.untold(take=False) == [("gone", "3 errors in a row")]
    assert ledger.record("tidy", "v1", "request", "active", result(), session="s") == 2
    assert ledger.wrong("s") == "tidy"


def test_an_active_status_with_no_move_on_record_goes_back_to_shadow(tmp_path):
    ledger = ledger_at(tmp_path, promote="ask")
    ledger.adopt("made", "v1", "asked")
    ledger._do("UPDATE habits SET origin = 'learned' WHERE habit = 'made'")  # an older version's learned habit
    ledger.adopt("asked", "a1", "asked")
    ledger._do("UPDATE habits SET status = 'active'")  # written by hand: no history row says so
    assert ledger.status("made", "v1") == "shadow" and ledger.status("asked", "a1") == "shadow"
    ledger.set_status("asked", "a1", "active", "saved active")  # the plugin's own move, on record
    assert ledger.status("asked", "a1") == "active"
    ledger.set_status("made", "v1", "active", "by hand")  # a learned habit acts only on the user's yes or the rules
    assert ledger.status("made", "v1") == "shadow"
    assert ledger.approve("made", "v1") and ledger.status("made", "v1") == "active"


def test_a_ledger_from_before_the_history_keeps_its_active_habits(tmp_path):
    import sqlite3
    path = tmp_path / "old.db"
    Ledger(path).set_status("tidy", "v1", "shadow")
    with sqlite3.connect(path) as db:  # an older file: a history, and a habit made active before it was kept
        db.execute("UPDATE habits SET status = 'active', origin = 'asked'")  # older files gave a first sight asked
        db.execute("DELETE FROM history")
        db.execute("PRAGMA user_version = 0")
    ledger = Ledger(path)
    assert ledger.status("tidy", "v1") == "active"
    ledger.set_status("tidy", "v1", "retired", "by hand (retire)", by="cli")
    ledger._do("UPDATE habits SET status = 'active'")  # once migrated, a hand-made state is no longer trusted
    assert ledger.status("tidy", "v1") == "shadow"


def test_a_hand_written_active_state_in_a_v1_file_is_never_vouched_by_an_upgrade(tmp_path):
    path = tmp_path / "v1.db"
    Ledger(path)._do("INSERT INTO habits (habit, version, status, since, origin) VALUES ('h', 'v1', 'active', 0, 'asked')")
    ledger = Ledger(path)  # opened again, as after an update: the step that kept old active states never runs twice
    assert ledger.status("h", "v1") == "shadow"
    assert not [r for r in ledger.history("h") if r["note"] == "active before the history was kept"]


def v1_file(path):
    """A file as the version with the learning layer left it: its tables, a proposed habit, its news."""
    import sqlite3
    Ledger(path)
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE IF NOT EXISTS turns (id INTEGER PRIMARY KEY, session TEXT, at REAL, message TEXT, "
                   "keys TEXT)")
        db.execute("CREATE TABLE IF NOT EXISTS candidates (id INTEGER PRIMARY KEY, keys TEXT UNIQUE, state TEXT, "
                   "why TEXT, until REAL, at REAL, wrote REAL, habit TEXT)")
        db.execute("INSERT INTO turns (session, at, message, keys) VALUES ('s', 1, 'my agents?', '[\"hc ls\"]')")
        db.execute("INSERT INTO candidates (keys, state, at) VALUES ('[\"hc ls\"]', 'spotted', 1)")
        db.execute("INSERT INTO habits (habit, version, status, since, origin) VALUES ('p', 'p1', 'proposed', 1, "
                   "'learned')")
        db.execute("INSERT INTO habits (habit, version, status, since, origin) VALUES ('q', 'q1', 'active', 1, "
                   "'portal')")
        db.execute('INSERT INTO history (habit, version, "from", "to", "by", note, at) VALUES '
                   "('q', 'q1', 'shadow', 'active', 'user', 'approved', 1)")
        for kind in ("learned", "staged", "forgotten", "ready"):
            db.execute("INSERT INTO news (habit, version, kind, note, at) VALUES ('q', 'q1', ?, '', 1)", (kind,))
        db.execute("PRAGMA user_version = 1")


def test_an_old_ledger_with_learning_tables_and_proposed_habits_opens_and_upgrades_once(tmp_path):
    path = tmp_path / "old.db"
    v1_file(path)
    for _ in range(2):  # the gateway and a cron tick open it, one after the other
        ledger = Ledger(path)
    with ledger._db() as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 3
        tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert not {"turns", "candidates"} & tables
    assert ledger.info("p")["status"] == "retired"
    assert [(r["from"], r["to"], r["by"]) for r in ledger.history("p")] == [("proposed", "retired", "habits")]
    assert [n["kind"] for n in ledger.news(take=False)] == ["ready"]
    assert ledger.status("q", "q1") == "active"  # the user's yes, on record: still vouched


def test_an_undo_right_after_the_upgrade_never_brings_back_proposed(tmp_path):
    clock = Clock()
    ledger = ledger_at(tmp_path, clock)
    ledger.set_status(HABIT, "v1", "shadow")
    ledger._do('INSERT INTO history (habit, version, "from", "to", "by", note, at) VALUES '
               "(?, 'v1', 'proposed', 'shadow', 'user', 'on trial, said the user', ?)", (HABIT, clock.t))
    assert ledger.undo() is not None
    assert ledger.status(HABIT, "v1") == "retired"


# ---- a file the ledger cannot use ----------------------------------------------------------------------------------

def test_a_corrupt_file_is_kept_aside_under_another_name_and_a_fresh_ledger_starts(tmp_path, caplog):
    path = tmp_path / "ledger.db"
    path.write_bytes(b"this is not a database at all" * 50)
    (tmp_path / "ledger.db-wal").write_bytes(b"junk")
    ledger = Ledger(path, Rules(), clock=Clock())
    assert ledger.runs(HABIT) == []  # a working, empty ledger
    kept = sorted(p.name for p in tmp_path.glob("ledger.db.broken-*"))
    assert len(kept) == 1 and not kept[0].endswith(("-wal", "-shm"))
    assert (tmp_path / kept[0]).read_bytes().startswith(b"this is not a database")  # nothing lost
    line = [r.getMessage() for r in caplog.records if "kept aside" in r.getMessage()]
    assert len(line) == 1 and "ledger.db" in line[0] and kept[0] in line[0]


def test_a_file_cut_in_half_is_kept_aside_too(tmp_path):
    path = tmp_path / "ledger.db"
    good = ledger_at(tmp_path)
    for _ in range(40):
        compared_run(good, True)
    data = path.read_bytes()
    for extra in ("-wal", "-shm"):
        (tmp_path / f"ledger.db{extra}").unlink(missing_ok=True)
    path.write_bytes(data[: len(data) // 2] if len(data) > 8192 else b"x" * 9000)
    ledger = Ledger(path, Rules(), clock=Clock())
    assert ledger.runs(HABIT) == []
    assert list(tmp_path.glob("ledger.db.broken-*"))


def test_a_file_of_a_newer_version_is_refused_and_left_untouched(tmp_path):
    import sqlite3
    path = tmp_path / "ledger.db"
    ledger_at(tmp_path)
    db = sqlite3.connect(path)
    db.execute("PRAGMA user_version = 7")
    db.commit()
    db.close()
    before = path.read_bytes()
    with pytest.raises(RuntimeError, match=r"newer.*version 7.*knows 3"):
        Ledger(path, Rules(), clock=Clock())
    assert path.read_bytes() == before and not list(tmp_path.glob("ledger.db.broken-*"))


def test_a_read_only_folder_is_not_taken_for_a_corrupt_file(tmp_path):
    import os
    import sqlite3
    ledger_at(tmp_path)
    os.chmod(tmp_path, 0o500)
    try:
        try:
            Ledger(tmp_path / "ledger.db", Rules(), clock=Clock())
        except sqlite3.Error:
            pass
        assert not list(tmp_path.glob("ledger.db.broken-*"))
    finally:
        os.chmod(tmp_path, 0o700)


# ---- a habit that runs often -----------------------------------------------------------------------------------------

def test_recording_a_run_does_not_scan_the_whole_runs_table(tmp_path):
    ledger = ledger_at(tmp_path)
    with ledger._db() as db:
        plan = " ".join(str(r[3]) for r in db.execute(
            "EXPLAIN QUERY PLAN UPDATE runs SET said = NULL WHERE said IS NOT NULL AND at < 1"))
    assert "runs_said" in plan


def test_prune_drops_the_trace_of_old_runs_and_every_number_stays_true(tmp_path):
    clock = Clock()
    ledger = ledger_at(tmp_path, clock)
    for i in range(30):
        compared_run(ledger, i % 3 != 0)
        clock.t += 3600
    before = {key: ledger.summary(HABIT, "v1") [key] for key in ("runs", "compared", "agreement", "woke", "saved")}
    assert before["runs"] == 30 and before["compared"] == 30
    clock.t += 59 * 86400
    ledger.prune()
    with ledger._db() as db:
        kept = db.execute("SELECT COUNT(*) FROM runs WHERE trace IS NOT NULL").fetchone()[0]
        total = db.execute("SELECT COUNT(*) FROM runs").fetchone()[0]
    assert total == 30 and 0 < kept < 30  # the oldest runs lost their trace, the recent ones keep it
    clock.t += 40 * 86400
    ledger.prune()
    with ledger._db() as db:
        assert db.execute("SELECT COUNT(*) FROM runs WHERE trace IS NOT NULL").fetchone()[0] == 0
    after = {key: ledger.summary(HABIT, "v1")[key] for key in before}
    assert after == before  # /habits shows the same numbers
    assert ledger.last_trace(HABIT) is None  # no trace left to show, and no crash
    assert len(ledger.runs(HABIT, 100)) == 30


def test_the_scores_read_a_covering_index_not_the_wide_rows(tmp_path):
    ledger = ledger_at(tmp_path)
    with ledger._db() as db:
        for sql in ("SELECT COUNT(*), SUM(woke) FROM runs WHERE habit = 'a' AND version = 'b'",
                    "SELECT planned, matched, s1 FROM runs WHERE habit = 'a' AND version = 'b' AND compared = 1"):
            plan = " ".join(str(r[3]) for r in db.execute("EXPLAIN QUERY PLAN " + sql))
            assert "COVERING INDEX runs_scores" in plan, plan


def test_the_ledger_file_is_created_readable_by_its_owner_only(tmp_path):
    import os
    import stat
    from habits.ledger import Ledger
    ledger = Ledger(tmp_path / "habits")
    ledger.status("x", "v1")  # any write
    mode = stat.S_IMODE(os.stat(ledger.path).st_mode)
    assert mode & 0o077 == 0, oct(mode)
