from datetime import date

import pytest

from wellops import tools
from wellops.contracts import RunDeps
from wellops.settings import settings
from wellops.substrate import ingest

TODAY = date(2026, 10, 17)
settings.rag_embed = False  # no embedding calls from tests; test_rag installs a fake embedder


@pytest.fixture(scope="session")
def db(tmp_path_factory):
    path = tmp_path_factory.mktemp("wellops") / "t.duckdb"
    con = ingest.connect(path)
    con.execute("SET TimeZone='UTC'")  # raw timestamps are UTC; never ingest in a local zone
    ingest.ingest(settings.data_dir, "sample", con)
    con.close()
    d = tools.Db(path)
    tools.set_db(d)
    return d


@pytest.fixture(autouse=True)
def clean(db):
    yield
    db.run("DELETE FROM assertions WHERE source = 'chat'")
    db.run("DELETE FROM episodes")
    db.run("DELETE FROM handles")
    tools.store(db).forget()
    tools.store(db).counters.clear()


@pytest.fixture
def deps():
    def make(principal="U-201", chat="c1"):
        return RunDeps(principal=principal, chat_id=chat, today=TODAY)
    return make
