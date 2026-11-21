import io
import json
import urllib.error

import pytest

from reflexes import decide


class Server:
    """Stands for the decision model's HTTP endpoint: answers each request from a function of its body."""

    def __init__(self, answer):
        self.answer, self.bodies, self.headers = answer, [], []

    def __call__(self, request, timeout=None):
        body = json.loads(request.data)
        self.bodies.append(body)
        self.headers.append(dict(request.header_items()))
        reply = self.answer(body)
        if isinstance(reply, Exception):
            raise reply
        return io.BytesIO(json.dumps(reply).encode())


def test_a_choice_goes_out_typed_and_comes_back_with_its_probability():
    server = Server(lambda body: {"answers": {
        "q0": {"choice": "task", "probabilities": {"task": 0.93, "idea": 0.07}},
        "q1": {"choice": "idea", "probabilities": {"task": 0.4, "idea": 0.6}}}})
    model = decide.Typed("key", opener=server)
    assert model("What is this note?", ["task", "idea"], [("buy milk", ""), ("hmm", "")]) == [("task", 0.93), ("idea", 0.6)]
    sent = server.bodies[0]
    assert sent["model"] == "jev-latest" and sent["state"] == "(none)"
    assert sent["questions"]["q0"] == {"type": "choice", "instructions": "What is this note?\nbuy milk",
                                       "criteria": {"task": "task", "idea": "idea"}}
    assert server.headers[0]["Authorization"] == "Bearer key"


def test_yes_or_no_is_one_probability():
    server = Server(lambda body: {"answers": {"q0": {"noul": 0.9}, "q1": {"noul": 0.2}}})
    assert decide.Typed("k", opener=server)("Urgent?", ["yes", "no"], [("a", ""), ("b", "")]) == [
        ("yes", 0.9), ("no", 0.8)]
    assert server.bodies[0]["questions"]["q0"] == {"type": "noul", "instructions": "Urgent?\na"}


def test_an_item_with_its_own_context_is_asked_alone_about_it():
    server = Server(lambda body: {"answers": {"q0": {"choice": "task", "probabilities": {"task": 0.9}}}})
    model = decide.Typed("k", opener=server)
    assert model("Kind?", ["task", "idea"], [("a.txt", "Buy milk"), ("b.txt", "What if")]) == [("task", 0.9)] * 2
    assert sorted(body["state"] for body in server.bodies) == ["Buy milk", "What if"]


def test_many_items_go_out_fifty_at_a_time():
    server = Server(lambda body: {"answers": {q: {"noul": 1.0} for q in body["questions"]}})
    answers = decide.Typed("k", opener=server)("Keep?", ["yes", "no"], [(str(i), "") for i in range(120)])
    assert len(answers) == 120 and [len(body["questions"]) for body in server.bodies] == [50, 50, 20]


@pytest.mark.parametrize("reply", [
    {"nope": 1},
    {"answers": {"q0": {"choice": "task"}, "q1": {}}},
    urllib.error.HTTPError("u", 401, "no", {}, None),
    TimeoutError(),
])
def test_a_model_that_cannot_answer_says_so(reply):
    model = decide.Typed("k", opener=Server(lambda body: reply))
    with pytest.raises(decide.NoAnswer):
        model("Kind?", ["task", "idea"], [("a", ""), ("b", "")])


def test_the_machine_says_which_model_decides():
    assert decide.from_env({}) is None
    jev = decide.from_env({"TYPESAFE_API_KEY": "k"})
    assert (jev.url, jev.model, jev.key) == (decide.JEV, "jev-latest", "k")
    other = decide.from_env({"REFLEX_DECIDER_URL": "https://x/y", "REFLEX_DECIDER_KEY": "k2",
                             "REFLEX_DECIDER_MODEL": "clef-flash"})
    assert (other.url, other.model, other.key, other.name) == ("https://x/y", "clef-flash", "k2", "clef-flash")
