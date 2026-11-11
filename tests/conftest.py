import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def no_shipped_reflex_does_df(monkeypatch, request):
    """The tests learn `df -h ~` from made-up histories. The shipped disk-space example does that very command, and
    the dream drafts no second reflex for a command a reflex already does: so in the tests' world disk-space is not
    counted, except in the tests of that rule, which ask for the real examples with the fixture `shipped`."""
    if "shipped" in request.fixturenames:
        return
    from habits import nursery
    doers = nursery.Dream._doers

    def without_disk_space(self):
        return {key: kept for key, found in doers(self).items()
                if (kept := [(name, folder) for name, folder in found if name != "disk-space"])}

    monkeypatch.setattr(nursery.Dream, "_doers", without_disk_space)


@pytest.fixture
def shipped():
    """The shipped examples count as they do for a user (see no_shipped_reflex_does_df)."""
