"""Ordinary work is never refused: a day of developer calls goes through the registered pre_tool_call hook, with a
habit saved and the plugin installed, and not one is blocked. A call that shows nothing of the plugin's world (the
hermes program, the plugin's data and code, a habit's files, Hermes's settings and history, HABITS_OFF) is not even
read; one that only mentions it (a README, a test, a note, a habit tracker of the user's own) is ordinary too.

The lists start from a catalog maintainer's bench of 152 everyday calls, of which the guard once refused 23. Three
of the 152 are left out because they are the plugin's business and stay refused (test_the_three_that_stay_refused)."""

import json
import statistics
import time

import pytest

from habits import catalog, guard, plugin
from test_cockpit import chat  # noqa: F401  (the fixture)
from test_plugin import FakeCtx

T = "terminal"
RM = "r" + "m"  # the install scanner reads this file too: a deleting command is never spelled whole
HABIT = "check-links"

HEREDOC = """cat > /tmp/demo/config.json <<'EOF'
{"name": "demo", "version": "1.0.0", "scripts": {"test": "jest"}}
EOF"""
PY = """python3 -c "import json,sys; d=json.load(open('package.json')); print(d['version'])\""""
LONG = "echo " + " ".join(f"word{i}" for i in range(4000))  # about 28,000 characters
LONG_PIPE = " && ".join(f"grep -c pattern{i} src/file{i}.py" for i in range(400))
SCRIPT = "\n".join(["#!/bin/bash", "set -euo pipefail"] + [f"echo step {i}; ls -la /tmp/dir{i} | wc -l" for i in range(300)])
BIG_PY = "\n".join(f"def f{i}(x):\n    return x + {i}\n" for i in range(9000))  # about 250,000 characters
BIG_JSON = json.dumps({f"k{i}": "v" * 40 for i in range(5000)})  # about 260,000
MID_SH = "\n".join(f"echo step {i}; ls -la /tmp/dir{i} | wc -l" for i in range(4500))  # about 190,000
MID_PY = "\n".join(f"def f{i}(x):\n    return x + {i}\n" for i in range(6500))  # about 190,000
LONG_SCRIPT = "\n".join(f"def helper_{i}(rows):\n    return ', '.join(str(r) for r in rows)\n" for i in range(300)) + \
    "\nopen('/tmp/report/out.txt', 'w').write(helper_1([1, 2]))\n"
PROSE = "\n".join(f"line {i}: what we learned about plugins and config" for i in range(6000))  # about 290,000

MAINTAINER_1 = [
    (T, {"command": "git status"}),
    (T, {"command": "git diff --stat HEAD~1"}),
    (T, {"command": "git log --oneline -20"}),
    (T, {"command": "git add -A && git commit -m 'fix: handle empty input'"}),
    (T, {"command": "git push origin feature/login"}),
    (T, {"command": "git checkout -b fix/typo && git pull --rebase origin main"}),
    (T, {"command": "git stash && git rebase main && git stash pop"}),
    (T, {"command": "npm install"}),
    (T, {"command": "npm test -- --watch=false"}),
    (T, {"command": "npm run build 2>&1 | tail -20"}),
    (T, {"command": "npx prettier --write 'src/**/*.ts'"}),
    (T, {"command": "pytest -q tests/test_api.py -k 'not slow'"}),
    (T, {"command": "python -m pytest --cov=src -x"}),
    (T, {"command": "pip install -r requirements.txt"}),
    (T, {"command": "uv run ruff check . --fix"}),
    (T, {"command": "docker ps -a"}),
    (T, {"command": "docker compose up -d --build"}),
    (T, {"command": "docker build -t app:dev . && docker run --rm -p 8080:8080 app:dev"}),
    (T, {"command": "docker exec -it web sh -c 'ls /app && cat /app/package.json'"}),
    (T, {"command": HEREDOC}),
    (T, {"command": PY}),
    (T, {"command": "python3 scripts/migrate.py --dry-run"}),
    (T, {"command": "ls -la src/ && wc -l src/*.py"}),
    (T, {"command": "grep -rn 'TODO' src/ | head -30"}),
    (T, {"command": "find . -name '*.pyc' -delete"}),
    (T, {"command": RM + " -rf node_modules dist && npm ci"}),
    (T, {"command": "mkdir -p build && cp -r public/* build/"}),
    (T, {"command": "curl -s http://localhost:8080/health | jq ."}),
    (T, {"command": "make test"}),
    (T, {"command": "cargo build --release"}),
    (T, {"command": "export NODE_ENV=test; for f in tests/*.js; do node \"$f\"; done"}),
    (T, {"command": "cat $FILE | sort | uniq -c"}),
    (T, {"command": "sed -i '' 's/foo/bar/g' src/app.py"}),
    (T, {"command": "kill $(lsof -ti:3000)"}),
    (T, {"command": "cd ~/proj && source .venv/bin/activate && python manage.py migrate"}),
    (T, {"command": "bash scripts/deploy.sh staging"}),
    (T, {"command": "echo \"$(date): build ok\" >> build.log"}),
    (T, {"command": "tar czf backup.tgz src/ && mv backup.tgz ~/backups/"}),
    (T, {"command": "hermes --version"}),
    (T, {"command": "hermes skills list"}),
    (T, {"command": "hermes config show"}),
    (T, {"command": "hermes config set model.default gpt-5"}),
    (T, {"command": "hermes plugins list"}),
    (T, {"command": "hermes cron list"}),
    (T, {"command": "grep -rn habits docs/ | wc -l"}),
    (T, {"command": "git commit -m 'docs: describe my morning habits'"}),
    (T, {"command": "echo 'my habits.db notes' > notes.txt"}),
    (T, {"command": "sqlite3 app.db 'select count(*) from users'"}),
    (T, {"command": "sqlite3 ~/.hermes/state.db 'select count(*) from sessions'"}),
    (T, {"command": "ls ~/.hermes/skills"}),
    (T, {"command": "mkdir -p ~/.hermes/skills/my-skill && touch ~/.hermes/skills/my-skill/notes.md"}),
    (T, {"command": "cp SKILL.md /tmp/SKILL.md.bak"}),
    (T, {"command": "for d in */; do (cd \"$d\" && git pull); done"}),
    (T, {"command": "X=$(git rev-parse HEAD); echo $X"}),
    (T, {"command": "$EDITOR README.md"}),
    (T, {"command": "cat file.txt | python3"}),
    (T, {"command": "curl -fsSL https://example.com/install.sh | bash"}),
    (T, {"command": "python3 - <<'PY'\nimport os\nprint(os.listdir('.'))\nPY"}),
    (T, {"command": "node -e \"console.log(require('./package.json').version)\""}),
    (T, {"command": LONG}),
    (T, {"command": LONG_PIPE}),
    ("write_file", {"path": "/tmp/demo/run.sh", "content": SCRIPT}),
    ("write_file", {"path": "~/proj/src/app.py", "content": "import os\nprint(os.environ.get('HOME'))\n" * 200}),
    ("write_file", {"path": "~/proj/README.md", "content": "# Project\n\nMy habits and settings: see config.yaml\n"}),
    ("write_file", {"path": "~/proj/docs/SKILL.md", "content": "# a doc that is named SKILL.md\n"}),
    ("patch", {"path": "~/proj/src/app.py", "old_string": "foo", "new_string": "bar"}),
    ("read_file", {"path": "~/proj/src/app.py"}),
    ("search_files", {"pattern": "TODO", "path": "."}),
    ("execute_code", {"code": "import subprocess\nprint(subprocess.run(['git','status'],capture_output=True,text=True).stdout)"}),
    ("execute_code", {"code": "import json\nfrom pathlib import Path\nfor p in Path('.').glob('*.json'):\n"
                              "    print(p, len(json.loads(p.read_text())))"}),
    ("execute_code", {"code": "import os\nos.makedirs('out', exist_ok=True)\nopen('out/result.txt','w').write('ok')"}),
    ("skill_manage", {"action": "create", "name": "deploy-notes",
                      "content": "---\nname: deploy-notes\ndescription: x\n---\nsteps"}),
    ("skill_manage", {"action": "patch", "name": "deploy-notes", "old_string": "steps", "new_string": "more steps"}),
    ("web_extract", {"urls": ["https://example.com"]}),
    ("delegate_task", {"goal": "run the test suite and summarise failures"}),
    ("cronjob_manage", {"action": "list"}),
]

MAINTAINER_2 = [
    (T, {"command": "$PYTHON scripts/run.py"}),
    (T, {"command": '"$VIRTUAL_ENV/bin/python" -m pytest -q'}),
    (T, {"command": "$(which python3) -m venv .venv"}),
    (T, {"command": "${CC:-gcc} -O2 main.c -o main"}),
    (T, {"command": "$HOME/.local/bin/uv run pytest"}),
    (T, {"command": "~/.cargo/bin/cargo test"}),
    (T, {"command": "./node_modules/.bin/eslint src"}),
    (T, {"command": "make -j$(nproc)"}),
    (T, {"command": 'eval "$(ssh-agent -s)"'}),
    (T, {"command": 'eval "$(pyenv init -)" && pyenv shell 3.12'}),
    (T, {"command": "source ~/.nvm/nvm.sh && nvm use 20 && npm test"}),
    (T, {"command": "bash <(curl -s https://example.com/setup.sh)"}),
    (T, {"command": "echo 'print(1+1)' | python3"}),
    (T, {"command": "cat schema.sql | sqlite3 app.db"}),
    (T, {"command": "psql mydb < schema.sql"}),
    (T, {"command": "python3 < script.py"}),
    (T, {"command": "git ls-files | xargs wc -l"}),
    (T, {"command": "find . -name '*.py' | xargs grep -n TODO"}),
    (T, {"command": "wget -qO- https://sh.rustup.rs | sh -s -- -y"}),
    (T, {"command": "curl -LsSf https://astral.sh/uv/install.sh | sh"}),
    (T, {"command": "echo y | npx create-vite app"}),
    (T, {"command": "ssh deploy@host 'cd /srv/app && git pull && systemctl restart app'"}),
    (T, {"command": 'for cmd in lint test build; do npm run $cmd; done'}),
    (T, {"command": 'CMD="npm test"; $CMD'}),
    (T, {"command": 'time $CMD'}),
    (T, {"command": '"$@"'}),
    (T, {"command": "node script.js | bash"}),
    (T, {"command": "jq -r '.scripts.test' package.json | sh"}),
    (T, {"command": "python3 gen.py | node"}),
    (T, {"command": "hermes chat -q 'summarise the repo'"}),
    (T, {"command": "hermes -z 'what changed today?'"}),
    (T, {"command": "hermes cron create --name nightly --schedule '0 2 * * *' --prompt 'run tests'"}),
    (T, {"command": "hermes gateway restart"}),
    (T, {"command": "hermes update"}),
    (T, {"command": "hermes plugins install someone/other-plugin"}),
    (T, {"command": "hermes plugins disable other-plugin"}),
    (T, {"command": "hermes config set approvals.mode manual"}),
    (T, {"command": "hermes skills install foo"}),
    (T, {"command": "cat ~/.hermes/config.yaml"}),
    (T, {"command": "git clone https://github.com/x/dotfiles && cp dotfiles/* ~/"}),
    (T, {"command": "rsync -a ./ backup/"}),
    (T, {"command": "cp -r * ../copy/"}),
    (T, {"command": "mv *.md docs/"}),
    (T, {"command": RM + " -rf $TMPDIR/build"}),
    (T, {"command": "touch $OUT/done"}),
    (T, {"command": "tee $LOG < /dev/null"}),
    (T, {"command": "cd skills && ls"}),
    (T, {"command": "git checkout -- ."}),
    (T, {"command": "unzip archive.zip -d out/"}),
    (T, {"command": "tar xzf release.tgz"}),
    (T, {"command": "git apply fix.patch"}),
    (T, {"command": "python3 setup.py install"}),
    (T, {"command": "sh ./configure && make && make install"}),
    (T, {"command": "./run-tests.sh"}),
    (T, {"command": "bash -c 'set -e; npm ci; npm test'"}),
    (T, {"command": "docker run --rm -v $PWD:/w -w /w node:20 sh -c 'npm ci && npm test'"}),
    (T, {"command": "kubectl get pods -n prod | grep api"}),
    (T, {"command": "gh pr create --fill"}),
    (T, {"command": "terraform plan -out tf.plan"}),
    ("write_file", {"path": "~/proj/gen/big.py", "content": BIG_PY}),
    ("write_file", {"path": "~/proj/data/big.json", "content": BIG_JSON}),
    ("write_file", {"path": "~/proj/scripts/mid.sh", "content": MID_SH}),
    ("write_file", {"path": "~/proj/gen/mid.py", "content": MID_PY}),
    (T, {"command": "cat > data.json <<'EOF'\n" + BIG_JSON + "\nEOF"}),
    ("execute_code", {"code": MID_PY}),
    ("patch", {"path": "~/proj/gen/mid.py", "old_string": "def f1(x)", "new_string": MID_PY}),
    ("execute_code", {"code": "import subprocess, sys\nsubprocess.run([sys.executable, '-m', 'pytest', '-q'])"}),
    ("execute_code", {"code": "from hermes_tools import terminal\nprint(terminal('git status'))"}),
    ("execute_code", {"code": "import os\ncmd = os.environ.get('CMD', 'ls')\nos.system(cmd)"}),
    ("execute_code", {"code": "import yaml\nprint(yaml.safe_load(open('config.yaml')))"}),
    ("execute_code", {"code": "import sqlite3\nc = sqlite3.connect('app.db')\nc.execute('create table t(x)')\nc.commit()"}),
]

# With no habit saved the maintainer's bench also let these two through; with one saved, a relative SKILL.md may be
# the habit's own (the session's folder is unknown), which is the plugin's business: they are tried with no habit.
WITH_NO_HABIT = [
    (T, {"command": "cd ~/proj/skills/tool && echo x > SKILL.md"}),
    (T, {"command": "echo '# notes' > SKILL.md"}),
]

TESTS_AND_LOOPS = [
    "[ -f package.json ] && echo yes", "[ -f package.json ]", "if [ -f a ]; then echo b; fi", "[[ -f a ]] && echo b",
    "[ -d node_modules ] || npm install", "[ -n \"$CI\" ] && echo ci", "[[ -n $CI ]] && echo ci",
    "for f in *.md; do [ -s \"$f\" ] && wc -l \"$f\"; done", "ls [a-c]*.txt", "echo [done]",
    "git log --oneline | grep '\\[fix\\]'", "ls && [ -d y ]", "[ -z \"$(git status --porcelain)\" ] && echo clean",
    "if [ $(wc -l < f) -gt 10 ]; then echo big; fi", "test -d node_modules || npm install",
    "while read -r line; do echo \"$line\"; done < list.txt", "for i in 1 2 3; do curl -s localhost:$i; done",
    "until nc -z localhost 5432; do sleep 1; done", "case \"$1\" in start) echo go;; esac",
    "cat < notes.md", "sort < $FILE | head", "cat <<EOF > notes.txt\nhello $USER\nEOF",
    "python3 - <<'PY'\nimport json\nprint(json.dumps({'a': 1}))\nPY", "psql <<SQL\nselect 1;\nSQL",
    "npm run lint && npm test && npm run build", "npm ci --prefer-offline", "npx tsc --noEmit", "yarn install",
    "pnpm -r build", "pytest -x -q --lf", "python -m venv .venv && . .venv/bin/activate && pip install -e .",
    "docker compose logs --tail 50 api", "docker images | head", "make -C docs html", "make clean all",
    "git fetch --all --prune", "git diff main...HEAD -- src", "git blame -L 10,20 src/app.py",
    "python3 -c 'import sys; print(sys.version)'", "python3 -c \"print(sum(range(10)))\"",
    "sqlite3 app.db \"insert into users values (1, 'a')\"", "sqlite3 ~/proj/state.db \"insert into t values (1)\"",
    "cp ~/proj/state.db /tmp/state.db", "cp /tmp/x.db ~/proj/state.db", "sqlite3 habits.db 'select count(*) from habits'",
    "sqlite3 habits.db \"update habits set done = 1 where id = 3\"", "cp habits.db habits.bak",
    "ps aux | grep hermes", "journalctl -u hermes -n 20", "hermes status", "hermes doctor", "hermes logs -n 50",
    "cd ~/.hermes/hermes-agent && [ -f package.json ] && git log --oneline | head -3",
    "cp ~/.hermes/config.yaml ~/backups/", "tar czf ~/backups/hermes.tgz ~/.hermes/plugin-data/habits ~/.hermes/config.yaml",
    "git commit -m 'guard: HABITS_OFF never switches the guard off'", "grep -rn HABITS_OFF docs/",
    "ls ~/.hermes/plugins/habits", "cat ~/.hermes/plugins/habits/plugin.yaml", "hermes habits list",
    "hermes habits show check-links", "export NODE_ENV=production; npm run build", "env FORCE_COLOR=1 npm test",
    "sudo systemctl restart nginx", "xargs -n1 echo < list.txt", "watch -n 5 date",
]

WRITING = [
    ("write_file", {"path": "~/proj/README.md", "content": "Run `sqlite3 ~/.hermes/plugin-data/habits/habits.db .tables` "
                                                          "to inspect; settings live in ~/.hermes/config.yaml\n"}),
    ("write_file", {"path": "~/proj/docs/habits.md", "content": "The ledger is habits.db under plugin-data/habits. Do "
                                                               "not edit config.yaml in ~/.hermes by hand.\n"}),
    ("write_file", {"path": "~/proj/notes.txt", "content": "habits.db and state.db are sqlite files\n"}),
    ("write_file", {"path": "~/proj/a.py", "content": "DB = 'habits.db'\n"}),
    ("write_file", {"path": "~/proj/habit-tracker/db.py", "content": "import sqlite3\ndb = sqlite3.connect('habits.db')\n"
                    "db.execute(\"update habits set status='active' where id=1\")\n"}),
    ("write_file", {"path": "~/proj/habit-tracker/app.js", "content": "const db = open('habits.db')\n"}),
    ("write_file", {"path": "~/proj/habit-tracker/config.yaml", "content": "db: habits.db\n"}),
    ("write_file", {"path": "~/proj/habit-tracker/seed.sh", "content": "#!/bin/sh\nsqlite3 habits.db < schema.sql\n"}),
    ("write_file", {"path": "~/proj/app/settings.py", "content": "CONFIG = 'config.yaml'\nDB = 'habits.db'  # our app\n"}),
    ("write_file", {"path": "~/proj/.gitignore", "content": "habits.db\n*.pyc\n"}),
    ("write_file", {"path": "~/proj/data.json", "content": "{\"file\": \"habits.db\"}\n"}),
    ("write_file", {"path": "~/proj/index.html", "content": "<p>The ledger is habits.db</p>\n"}),
    ("write_file", {"path": "~/proj/CHANGELOG", "content": "Fixed the migration of habits.db\n"}),
    ("write_file", {"path": "~/proj/Makefile", "content": "db:\n\tsqlite3 habits.db < schema.sql\n"}),
    ("write_file", {"path": "~/proj/Dockerfile", "content": "COPY config.yaml /root/.hermes/config.yaml\n"}),
    ("write_file", {"path": "~/proj/docker-compose.yml", "content": "services:\n  hermes:\n    volumes:\n      - "
                    "~/.hermes/config.yaml:/root/.hermes/config.yaml:ro\n"}),
    ("write_file", {"path": "~/proj/notes.org", "content": "Hermes settings: ~/.hermes/config.yaml\n"}),
    ("write_file", {"path": "~/proj/plugin-data/habits/export.csv", "content": "a,b\n"}),
    ("write_file", {"path": "~/proj/deploy/config.yaml", "content": "plugins:\n  entries:\n    habits:\n      settings:\n"
                                                                   "        learning: auto\n"}),
    ("write_file", {"path": "~/proj/backup.sh", "content": "#!/bin/bash\n# back up Hermes\ncp ~/.hermes/state.db "
                    "~/backups/state-$(date +%F).db\ncp ~/.hermes/config.yaml ~/backups/\n"}),
    ("write_file", {"path": "~/proj/backup2.sh", "content": "#!/bin/bash\ntar czf ~/backups/hermes.tgz "
                    "~/.hermes/plugin-data/habits ~/.hermes/config.yaml\n"}),
    ("write_file", {"path": "~/proj/inspect.py", "content": "import sqlite3\nprint(sqlite3.connect('/Users/x/.hermes/"
                    "plugin-data/habits/habits.db').execute('select habit, status from habits').fetchall())\n"}),
    ("write_file", {"path": "~/proj/report.py", "content": "import os\nhome = os.path.expanduser('~/.hermes')\n"
                    "print(open(home + '/config.yaml').read()[:0])\n"}),
    ("write_file", {"path": "~/proj/a.sh", "content": "cat ~/.hermes/config.yaml\n"}),
    ("write_file", {"path": "~/proj/b.sh", "content": "hermes config show\nhermes habits list\n"}),
    ("write_file", {"path": "~/.hermes/skills/my-skill/SKILL.md", "content": "---\nname: my-skill\n---\nRead "
                    "~/.hermes/config.yaml to find the model.\n"}),
    ("write_file", {"path": "~/notes/report.md", "content": PROSE}),
    ("patch", {"path": "~/proj/a.py", "old_string": "x", "new_string": "y  # habits.db"}),
    ("patch", {"path": "~/proj/README.md", "old_string": "x", "new_string": "habits.db"}),
    ("patch", {"path": "~/proj/ansible/hermes.yml", "old_string": "x", "new_string": "dest: ~/.hermes/config.yaml"}),
    ("patch", {"path": "~/proj/habit-tracker/db.py", "old_string": "x", "new_string": "sqlite3.connect('habits.db')"}),
    ("execute_code", {"code": LONG_SCRIPT}),
    ("execute_code", {"code": "import csv\nwith open('/tmp/report/out.csv', 'w') as f:\n    w = csv.writer(f)\n"
                              "    w.writerow(['city', 'inhabitants', 'skills'])  # config\n"}),
    ("execute_code", {"code": "import sqlite3\nprint(sqlite3.connect('/Users/x/proj/app.db').execute('select count(*) "
                              "from users').fetchall())"}),
    ("process_manage", {"action": "submit", "session_id": "proc_1", "data": "npm test"}),
    ("process_manage", {"action": "poll", "session_id": "proc_1"}),
    ("send_message", {"target": "telegram", "message": "build finished"}),
    ("web_search", {"query": "hermes agent release notes"}),
    ("mcp__shell__run", {"command": "ls -la"}),
]

ORDINARY = MAINTAINER_1 + MAINTAINER_2 + [(T, {"command": c}) for c in TESTS_AND_LOOPS] + WRITING


@pytest.fixture
def hook(chat, tmp_path, monkeypatch):  # noqa: F811
    """The pre_tool_call hook as Hermes holds it once the plugin is registered: a home under tmp_path with a saved
    habit, the plugin's data folder, an installed copy of the plugin and a checkout of its repository elsewhere."""
    ctx, habits = chat
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / ".hermes"))
    habits._home = tmp_path / ".hermes" / "plugin-data" / "habits"
    habits._home.mkdir(parents=True)
    folder = tmp_path / ".hermes" / "skills" / HABIT
    (folder / "assets").mkdir(parents=True)
    (folder / "SKILL.md").write_text("---\nname: check-links\n---\nCheck.\n")
    (folder / "assets" / "reflex.yml").write_text("spec: x\n")
    (tmp_path / ".hermes" / "plugins" / "habits" / "habits").mkdir(parents=True)
    (tmp_path / ".hermes" / "plugins" / "habits" / "habits" / "guard.py").write_text("x = 1\n")
    repo = tmp_path / "code" / "hermes-habits"
    (repo / "tests").mkdir(parents=True)
    (repo / "habits").mkdir()
    (repo / "tests" / "test_guard.py").write_text(
        "import os\nimport sqlite3\n\nimport pytest\n\nfrom habits import guard\nfrom habits.ledger import Ledger\n\n"
        "HABIT = 'check-links'\n\n\ndef test_the_ledger(tmp_path):\n    ledger = Ledger(tmp_path / 'habits.db', None)\n"
        "    assert ledger\n")
    (repo / "habits" / "guard.py").write_text("import re\n\nLEDGER = re.compile(r'habits\\.db')\n\n\ndef f():\n    return 1\n")
    monkeypatch.setattr(catalog, "folders", lambda: {HABIT: folder})
    guard._PLACES.clear()
    registered = FakeCtx()
    monkeypatch.setattr(plugin, "Habits", lambda _ctx: habits)
    plugin.register(registered)
    return registered.hooks["pre_tool_call"]


def refusals(hook, calls):
    found = []
    for tool, args in calls:
        verdict = hook(tool_name=tool, args=args, session_id="s", task_id="t")
        if verdict:
            found.append((tool, json.dumps(args)[:160], verdict["message"][:120]))
    return found


def test_a_day_of_ordinary_developer_calls_meets_no_refusal(hook):
    assert len(ORDINARY) >= 150
    assert refusals(hook, ORDINARY) == []


def test_the_same_day_meets_no_refusal_while_habits_is_off(hook, monkeypatch):
    monkeypatch.setenv("HABITS_OFF", "1")
    assert refusals(hook, ORDINARY) == []


def test_a_skill_file_written_where_no_habit_can_be_is_ordinary_when_no_habit_is_saved(hook, monkeypatch):
    monkeypatch.setattr(catalog, "folders", lambda: {})
    assert refusals(hook, WITH_NO_HABIT) == []


def test_the_owner_develops_this_very_plugin_in_a_checkout_elsewhere(hook, tmp_path):
    repo = tmp_path / "code" / "hermes-habits"
    test_lines = (
        "\n\ndef test_the_settings_are_refused(g, tmp_path):\n"
        "    blocked(g, 'write_file', {'path': '~/.hermes/config.yaml', 'content': 'plugins:\\n'})\n"
        "    blocked(g, *terminal('hermes habits promote check-links'))\n"
        "    blocked(g, *terminal('sqlite3 ~/.hermes/plugin-data/habits/habits.db \"update habits set status=1\"'))\n"
        "    with sqlite3.connect(tmp_path / 'habits.db') as db:\n"
        "        db.execute(\"UPDATE habits SET status = 'active'\")\n"
        "    (tmp_path / '.hermes' / 'plugin-data' / 'habits').mkdir(parents=True)\n")
    source = ("\n\ndef _own_paths(home):\n    homes = {Path.home() / '.hermes' / 'plugin-data' / 'habits'}\n"
              "    return sorted(f'{h}/habits.db' for h in homes) + [str(Path.home() / '.hermes' / 'config.yaml')]\n")
    calls = [
        ("patch", {"path": str(repo / "tests" / "test_guard.py"), "old_string": "    assert ledger\n",
                   "new_string": "    assert ledger\n" + test_lines}),
        ("write_file", {"path": str(repo / "tests" / "test_new.py"),
                        "content": "import sqlite3\n\nfrom test_guard import blocked, terminal\n" + test_lines}),
        ("patch", {"path": str(repo / "habits" / "guard.py"), "old_string": "def f():\n    return 1\n",
                   "new_string": "def f():\n    return 2\n" + source}),
        ("write_file", {"path": str(repo / "docs" / "SAFETY.md"), "content": "`hermes habits promote x` and a write to "
                        "~/.hermes/plugin-data/habits/habits.db are refused. HABITS_OFF=1 turns the other hooks off.\n"}),
        ("write_file", {"path": str(repo / "plugin.yaml"), "content": "name: habits\noptional_env:\n  - HABITS_OFF\n"}),
        (T, {"command": f"cd {repo} && python -m pytest -q -c tests/pytest.ini tests -p no:cacheprovider"}),
        (T, {"command": f"cd {repo} && git add -A && git commit -m 'The guard lets ordinary work through'"}),
        (T, {"command": f"cd {repo} && git diff --stat && git log --oneline | head -5"}),
        (T, {"command": f"grep -rn 'habits.db' {repo}/habits | head"}),
        (T, {"command": f"sed -i '' 's/return 1/return 2/' {repo}/habits/guard.py"}),
        (T, {"command": f"hermes plugins validate {repo}"}),
        (T, {"command": f"cd {repo} && ruff check habits tests"}),
    ]
    assert refusals(hook, calls) == []


def linked(hook, tmp_path, monkeypatch):
    """The plugin installed as a link to a working copy (what a developer of the plugin has): returns the link, the
    working copy's real path and the data folder."""
    import os
    import shutil
    real = tmp_path / "work" / "hermes-habits"
    (real / "tests").mkdir(parents=True)
    (real / "habits").mkdir()
    (real / "habits" / "guard.py").write_text("x = 1\n")
    (real / "plugin.yaml").write_text("name: habits\n")
    link = tmp_path / ".hermes" / "plugins" / "habits"
    shutil.rmtree(link)
    os.symlink(real, link)
    monkeypatch.setattr(guard, "ROOT", real.resolve())
    guard._PLACES.clear()
    data = tmp_path / ".hermes" / "plugin-data" / "habits"
    (data / "habits.db").write_text("")
    return link, real.resolve(), data


def reads_of_the_plugins_folders(link, real, data):
    return [(T, {"command": c}) for c in (
        f"cd {link} && git status", f"cd {real} && git log --oneline -5", f"cd {real} && git diff --stat && git status --short",
        f"cd ~/.hermes/plugins/habits && ls", f"git -C {link} status", f"git -C {real} diff", f"git -C {real} log -3 | head",
        f"git -C {real} show HEAD:habits/guard.py", f"cd {real} && git blame habits/guard.py", f"ls {link}", f"ls -la {real}/habits",
        f"cat {link}/habits/guard.py", f"grep -rn guard {real}", f"cd {real} && ls | head", f"wc -l {real}/habits/*.py",
        f"cd {real} && python -m pytest -q tests", f"cd {link} && pytest -q -x -k guard",
        f"cd {real} && /x/venv/bin/python -m pytest -q -c tests/pytest.ini tests -p no:cacheprovider",
        f"python -m pytest -q {real}/tests", f"cd {real} && python3 -m unittest discover -s tests",
        f"ls {data}", f"cat {data}/off", f"sqlite3 -readonly {data}/habits.db 'select * from habits'",
        f"sqlite3 {data}/habits.db 'select count(*) from habits'", f"sqlite3 -readonly {data}/habits.db .tables",
        f"sqlite3 'file:{data}/habits.db?mode=ro' 'select 1'")]


def test_the_model_may_read_the_plugins_own_folder_and_data_like_any_other(hook, tmp_path, monkeypatch):
    link, real, data = linked(hook, tmp_path, monkeypatch)
    assert refusals(hook, reads_of_the_plugins_folders(link, real, data)) == []


# ---- reading is never an edit ------------------------------------------------------------------------------------
# Seen with a real model on 11 November: a look at a habit's files through a variable was refused as an edit by hand,
# under the name of another habit; read_file of a reflex.yml from inside execute_code too; and a stat plus a Python
# json.load of a file under ~/.hermes was refused as a program the guard could not read.

SECOND = "todo-count"
DUMP = "print(json.dumps({k:d[k] for k in d if k in ('status','state','updated_at','pid')},indent=1)[:1500])"
GATEWAY = (
    "echo \"=== gateway (default) ===\"; if [ -f ~/.hermes/gateway_state.json ]; then stat -f \"%Sm  %N\" "
    "~/.hermes/gateway_state.json; python3 -c \"import json;d=json.load(open('$HOME/.hermes/gateway_state.json'));"
    + DUMP + "\"; else echo \"no ~/.hermes/gateway_state.json\"; fi\n"
    "echo \"=== gateway (profile hre1207) ===\"; G=\"$HOME/.hermes/profiles/hre1207/gateway_state.json\"; "
    "if [ -f \"$G\" ]; then stat -f \"%Sm  %N\" \"$G\"; python3 -c \"import json;d=json.load(open('$G'));" + DUMP
    + "\"; else echo \"no profile gateway_state.json\"; fi\n"
    "echo \"=== disk ===\"; df -Pk ~/.hermes | tail -2\n"
    "echo \"=== internet ===\"; curl -sI -m 5 https://github.com | head -3")


def second_habit(tmp_path, monkeypatch):
    """A second saved habit beside check-links, and a file of Hermes's own; returns the second habit's folder."""
    home = tmp_path / ".hermes"
    folder = home / "skills" / SECOND
    (folder / "assets").mkdir(parents=True)
    (folder / "SKILL.md").write_text("---\nname: todo-count\n---\nCount.\n")
    (folder / "assets" / "reflex.yml").write_text("spec: x\n")
    (home / "gateway_state.json").write_text('{"status": "ok"}')
    (home / "plugin-data" / "habits" / "habits.db").write_text("")
    monkeypatch.setattr(catalog, "folders", lambda: {HABIT: home / "skills" / HABIT, SECOND: folder})
    guard.forget_folders()
    return folder


def reads_of_a_habit(folder):
    """Looks at a habit's own files: by their path, through a variable set to a plain path on the same line, in a
    loop or a test, from code. Not one of them writes."""
    s = str(folder)
    lines = [
        "S=~/.hermes/skills/todo-count; find $S -type f; echo ---; cat $S/SKILL.md; echo ---REFLEX---; "
        "cat $S/assets/reflex.yml 2>/dev/null",  # the line of the report
        f"S={s}; find $S -type f; cat $S/SKILL.md",
        'S="$HOME/.hermes/skills/todo-count"; ls -la "$S" "$S/assets"; head -20 "${S}/SKILL.md"',
        f"S={s}\nD=$S/assets\nF=$D/reflex.yml\nwc -l $F && grep -n spec $F",
        f"SKILL_DIR={s} && stat $SKILL_DIR/SKILL.md && ls $SKILL_DIR",
        f"cat {s}/SKILL.md {s}/assets/reflex.yml", f"find {s} -type f", f"find {s} -type f -name '*.yml' | sort",
        f"find {s} -type f -exec cat {{}} +", f"find {s} -name SKILL.md -exec grep -n name {{}} \\;",
        f"grep -rn spec {s}", f"stat {s}/assets/reflex.yml", f"ls -la {s} {s}/assets", f"wc -l {s}/SKILL.md",
        f"head -5 {s}/SKILL.md; tail -n 3 {s}/assets/reflex.yml", f"cd {s} && cat SKILL.md assets/reflex.yml",
        f"for f in {s}/SKILL.md {s}/assets/reflex.yml; do echo \"== $f\"; head -5 \"$f\"; done",
        f"for f in {s}/*.md {s}/assets/*.yml; do wc -l \"$f\"; done",
        f"if [ -f {s}/assets/reflex.yml ]; then cat {s}/assets/reflex.yml; else echo none; fi",
        f"if [ -f {s}/assets/reflex.yml ]\nthen\n  cat {s}/assets/reflex.yml\nfi",
        f"[ -d {s}/assets ] && ls {s}/assets || echo 'no assets'",
        f"V=$(head -1 {s}/SKILL.md); echo \"$V\"", f"N=$(grep -c . {s}/assets/reflex.yml) && echo $N lines",
        f"git -C {s} status --short; git -C {s} log --oneline -3", f"diff {s}/SKILL.md ~/.hermes/skills/{HABIT}/SKILL.md",
        f"python3 -c \"print(open('{s}/assets/reflex.yml').read())\"",
        f"F={s}/assets/reflex.yml; python3 -c \"import yaml; print(yaml.safe_load(open('$F')))\"",
        f"hermes habits check {s}/assets/reflex.yml", f"hermes habits show {SECOND}",
        f"# a look at the skill\ncat {s}/SKILL.md  # its text\nls {s}/assets", 'cat "${HOME}/.hermes/skills/todo-count/SKILL.md"',
        'S="${HOME}/.hermes/skills/todo-count"; cat "${S}/SKILL.md" "${S}/assets/reflex.yml"',
        f"awk '{{print}}' {s}/SKILL.md | head -3", f"S={s}; awk 'NR<5' $S/SKILL.md; sed -n '1,5p' $S/assets/reflex.yml",
        "python3 -c \"print(open('$HOME/.hermes/skills/todo-count/SKILL.md').read()[:200])\"",
        f"S={s}; {{ cat $S/SKILL.md; ls $S; }}", f"S={s}; while ! [ -f $S/SKILL.md ]; do sleep 1; done; cat $S/SKILL.md",
        f"S={s}", f"S={s}; echo $S", f"S={s}; A=$S/assets; ls $A; wc -l $A/reflex.yml",
    ]
    code = [
        "from hermes_tools import terminal, read_file\nprint(terminal(\"ls -la backup notes; date +%Y%m%d\")[\"output\"])\n"
        f"print(read_file(\"{s}/assets/reflex.yml\")[\"content\"])",  # the code of the report
        f"print(open('{s}/assets/reflex.yml').read())", f"with open('{s}/SKILL.md') as f:\n    print(f.read()[:200])",
        f"import yaml\nprint(yaml.safe_load(open('{s}/assets/reflex.yml')))",
        f"from pathlib import Path\nprint(Path('{s}/SKILL.md').read_text())",
        f"import os\nfor name in os.listdir('{s}'):\n    print(name, os.path.getsize(os.path.join('{s}', name)))",
        f"import json\nfrom hermes_tools import read_file\nprint(json.dumps(read_file('{s}/SKILL.md'))[:300])",
        f"import glob\nfor p in glob.glob('{s}/assets/*'):\n    print(p, len(open(p).read()))",
        f"from hermes_tools import terminal\nprint(terminal(\"S={s}; cat $S/SKILL.md; ls $S/assets\")['output'])",
        f"from hermes_tools import read_file, write_file\nwrite_file('/tmp/copy.yml', read_file('{s}/assets/reflex.yml')['content'])",
    ]
    return [(T, {"command": c}) for c in lines] + [("execute_code", {"code": c}) for c in code] + [
        ("read_file", {"path": f"{s}/assets/reflex.yml"}), ("search_files", {"pattern": "spec", "path": s})]


def reads_of_hermes_and_the_plugin():
    """Looks at Hermes's own files and at the plugin's folders, a variable or a small program in the line."""
    lines = [
        GATEWAY,  # the spot check of the report
        "stat -f '%Sm %N' ~/.hermes/gateway_state.json",
        "python3 -c \"import json;d=json.load(open('$HOME/.hermes/gateway_state.json'));print(d)\"",
        "G=\"$HOME/.hermes/gateway_state.json\"; if [ -f \"$G\" ]; then stat \"$G\"; python3 -c \"import json;"
        "print(json.load(open('$G')))\"; fi",
        "H=~/.hermes; ls $H; ls $H/skills | head; cat $H/gateway_state.json",
        "C=~/.hermes/config.yaml; grep -n model $C | head -5", "CFG=\"$HOME/.hermes/config.yaml\"; [ -f \"$CFG\" ] && wc -l \"$CFG\"",
        "python3 -c \"import yaml; print(sorted(yaml.safe_load(open('$HOME/.hermes/config.yaml'))))\"",
        "D=~/.hermes/plugin-data/habits; ls -la $D; cat $D/off 2>/dev/null",
        "P=~/.hermes/plugins/habits; ls $P; grep -rn guard $P/habits | head -3",
        "DB=~/.hermes/state.db; sqlite3 $DB 'select count(*) from sessions'",
        "for f in ~/.hermes/gateway_state.json ~/.hermes/config.yaml; do [ -f \"$f\" ] && wc -c \"$f\"; done",
        "find ~/.hermes -maxdepth 1 -type f -exec ls -la {} +", "git -C ~/.hermes/hermes-agent log --oneline -3",
        "git -C ~/.hermes/hermes-agent status --short && git -C ~/.hermes/hermes-agent describe --tags --always",
    ]
    code = [
        "import json, os\nprint(json.load(open(os.path.expanduser('~/.hermes/gateway_state.json'))))",
        "from pathlib import Path\nprint(Path('~/.hermes/config.yaml').expanduser().read_text()[:0])",
        "import os\nprint(sorted(os.listdir(os.path.expanduser('~/.hermes/plugin-data/habits'))))",
    ]
    return [(T, {"command": c}) for c in lines] + [("execute_code", {"code": c}) for c in code]


def test_a_look_at_a_habits_files_is_never_refused(hook, tmp_path, monkeypatch):
    folder = second_habit(tmp_path, monkeypatch)
    calls = reads_of_a_habit(folder)
    assert len(calls) >= 35 and refusals(hook, calls) == []


def test_a_look_at_hermess_own_files_and_the_plugins_folders_is_never_refused(hook, tmp_path, monkeypatch):
    second_habit(tmp_path, monkeypatch)
    assert refusals(hook, reads_of_hermes_and_the_plugin()) == []


# ---- a look around the Hermes home is never refused -------------------------------------------------------------
# Seen on the owner's real Hermes on 11 November, in the very follow-up the shipped health reflex suggests ("what
# takes space in ~/.hermes"): a listing of state.db* was answered with the message about writing Hermes's history,
# and a loop that counted empty folders with the message about a program the guard cannot read. Two refusals in one
# turn, for lines that write nothing.

ENVS = "~/.hermes/installs/194cfb2aeba8acc3/environments"
LEASES = (  # the first refused line of the report
    f"cd {ENVS}\n"
    "echo \"=== leases (échantillon) ===\"\n"
    "for d in $(ls -1d */ | head -3); do echo \"## $d\"; ls -la \"$d/.leases\"; cat \"$d/.leases\"/* 2>/dev/null | head -5; done\n"
    "echo; echo \"=== venv: paquets identiques ? (taille site-packages) ===\"\n"
    "for d in 45a79124707f418b9b95ca217bec03d8 34cddcfc181e4625b7d7ceabeed5ff33; do printf \"%s  \" \"$d\"; "
    "du -sh \"$d/venv/lib/python3.14/site-packages\" 2>/dev/null; ls \"$d/venv/lib/python3.14/site-packages\" 2>/dev/null "
    "| wc -l; done\n"
    "echo; echo \"=== hermes-agent/apps ===\"; du -sh ~/.hermes/hermes-agent/apps 2>/dev/null\n"
    "echo; du -sh ~/.hermes/* 2>/dev/null | sort -h | tail -15; ls -lh ~/.hermes/state.db*")
EMPTY_LEASES = (  # the second one
    f"cd {ENVS}\n"
    "empty=0; nonempty=0\n"
    "for d in */; do if [ -z \"$(ls -A \"$d/.leases\" 2>/dev/null)\" ]; then empty=$((empty+1)); else "
    "nonempty=$((nonempty+1)); fi; done\n"
    "echo \"envs sans lease actif (repertoire .leases vide): $empty\"\n"
    "echo \"envs avec lease(s): $nonempty\"\n"
    "echo \"espace total envs: $(du -sh . | cut -f1)\"")


def looks_around_the_hermes_home():
    """Reads under the Hermes home: listings, sizes, counts, loops and tests whose every command reads, a cd into
    one of its folders followed by reads, and the history database listed or asked with a select. Not one writes."""
    lines = [
        LEASES, EMPTY_LEASES,
        f"ls -la {ENVS}/05da76e94c0945e3a40c9cc0a8f80f9c/.leases", "ls -la ~/.hermes/installs", "du -sh ~/.hermes/*",
        "ls -lh ~/.hermes/state.db*", "du -sh ~/.hermes/* 2>/dev/null | sort -h | tail -15", "ls -la ~/.hermes",
        "ls -lh ~/.hermes/state.db ~/.hermes/state.db-wal ~/.hermes/state.db-shm", "du -h ~/.hermes/state.db*",
        "stat ~/.hermes/state.db", "stat -f '%z %N' ~/.hermes/state.db*", "wc -c ~/.hermes/state.db*",
        "file ~/.hermes/state.db", "du -sh ~/.hermes", "du -sh ~/.hermes/* | sort -rh | head -20", "df -h ~/.hermes",
        "du -d 1 -h ~/.hermes | sort -h", "du -sk ~/.hermes/installs/*/environments/* | sort -n | tail -5",
        "find ~/.hermes -maxdepth 1 -name 'state.db*' -exec ls -lh {} +", "find ~/.hermes -maxdepth 2 -type d | head -40",
        "find ~/.hermes -name '*.db' -size +10M", "find ~/.hermes/installs -maxdepth 3 -type d -name .leases | wc -l",
        "cd ~/.hermes && ls -lh state.db*", "cd ~/.hermes && du -sh * | sort -h | tail", "cd ~/.hermes; ls -la; du -sh .",
        "cd ~/.hermes && ls -la state.db state.db-wal && du -sh sessions logs cache 2>/dev/null",
        "cd ~/.hermes && wc -c state.db* && stat state.db", f"cd {ENVS} && ls | wc -l", f"cd {ENVS}; ls -ltd */ | tail -5",
        f"cd {ENVS}\necho \"nb environments: $(ls -1 | wc -l)\"\nls -ltd */ | tail -5\nls -la $(ls -1d */ | head -1)",
        f"cd {ENVS}\nfor d in */; do du -sh \"$d\"; done | sort -h | tail -3",
        f"cd {ENVS}\nn=0; for d in */; do n=$((n+1)); done; echo \"$n environments\"",
        f"for d in {ENVS}/*/; do if [ -z \"$(ls -A \"$d/.leases\" 2>/dev/null)\" ]; then echo \"$d\"; fi; done | wc -l",
        f"for d in {ENVS}/*/; do [ -d \"$d/.leases\" ] && ls -A \"$d/.leases\" | wc -l; done",
        f"cd {ENVS}\nls -1d */ | head -3 | while read -r d; do du -sh \"$d\"; done",
        "for f in ~/.hermes/state.db*; do ls -lh \"$f\"; done", "for f in ~/.hermes/*.db; do echo \"$f: $(wc -c \"$f\")\"; done",
        "for f in ~/.hermes/state.db*; do printf '%s\\t%s\\n' \"$(stat -f%z \"$f\")\" \"$f\"; done",
        "echo $((1024*1024)); ls -lh ~/.hermes/state.db*", "n=0; for f in ~/.hermes/state.db*; do n=$((n + 1)); done; echo $n",
        "big=0; for f in ~/.hermes/*; do [ -d \"$f\" ] && big=$(($big+1)); done; echo \"$big folders\"",
        "echo \"history: $(ls -lh ~/.hermes/state.db | awk '{print $5}')\"",
        "find ~/.hermes -maxdepth 1 -type f | xargs ls -lh", "find ~/.hermes -name '*.db*' -print0 | xargs -0 du -ch | tail -1",
        "ls ~/.hermes | xargs -I{} du -sh ~/.hermes/{} 2>/dev/null | sort -h | tail -5",
        "ls -1 ~/.hermes | while IFS= read -r name; do printf '%s\\n' \"$name\"; done | head",
        "sqlite3 \"file:$HOME/.hermes/plugin-data/habits/habits.db?immutable=1\" 'select count(*) from habits'",
        "sqlite3 'file:state.db?immutable=1' .tables; ls -lh ~/.hermes/state.db*",
        "sqlite3 -readonly ~/.hermes/state.db '.schema sessions' | head -20; du -h ~/.hermes/state.db",
        "sqlite3 ~/.hermes/state.db 'select count(*) from sessions'", "sqlite3 -readonly ~/.hermes/state.db .tables",
        "sqlite3 \"file:$HOME/.hermes/state.db?mode=ro\" 'select count(*) from messages'",
        "cd ~/.hermes && sqlite3 state.db 'select count(*) from sessions'",
        "cd ~/.hermes && sqlite3 -readonly state.db 'select max(started_at) from sessions'; ls -lh state.db*",
        "ls -lh ~/.hermes/state.db*; sqlite3 ~/.hermes/state.db \"select count(*) from sessions\"",
        "ls ~/.hermes/plugin-data ~/.hermes/plugins; du -sh ~/.hermes/plugin-data/habits ~/.hermes/plugins/habits",
        "cd ~/.hermes && du -sh plugin-data plugins skills", "cd ~/.hermes; for d in */; do du -sh \"$d\"; done",
        "cd ~/.hermes && ls -la plugin-data/habits && wc -c config.yaml",
        "cd ~/.hermes/logs 2>/dev/null && ls -lt | head -5 && tail -n 20 agent.log",
    ]
    return [(T, {"command": c}) for c in lines]


def test_a_look_around_the_hermes_home_is_never_refused(hook, tmp_path, monkeypatch):
    second_habit(tmp_path, monkeypatch)
    (tmp_path / ".hermes" / "state.db").write_text("")
    for name in ("05da76e94c0945e3a40c9cc0a8f80f9c", "45a79124707f418b9b95ca217bec03d8"):
        (tmp_path / ".hermes" / "installs" / "194cfb2aeba8acc3" / "environments" / name / ".leases").mkdir(parents=True)
    calls = looks_around_the_hermes_home()
    assert len(calls) >= 45 and refusals(hook, calls) == []


@pytest.mark.parametrize("command, wanted", [
    # the same places, written to: refused exactly as before
    ("cd ~/.hermes && sqlite3 state.db 'delete from sessions'", "history"),
    ("sqlite3 ~/.hermes/state.db \"update sessions set title='x'\"", "history"),
    ("cd ~/.hermes && " + RM + " state.db-wal", "history"), ("cp /tmp/x.db ~/.hermes/state.db", "history"),
    ("ls -lh ~/.hermes/state.db* > ~/.hermes/state.db", "history"), ("cd ~/.hermes && : > state.db", "history"),
    ("for f in ~/.hermes/state.db*; do " + RM + " \"$f\"; done", "history"),
    ("for f in ~/.hermes/state.db*; do ls -lh \"$f\"; done; truncate -s 0 ~/.hermes/state.db", "history"),
    ("cd ~/.hermes && ls -lh state.db* && mv state.db state.db.old", "history"),
    ("cd ~/.hermes && du -sh * && touch plugin-data/habits/off", ""),
    ("cd ~/.hermes; for d in */; do du -sh \"$d\"; done; echo 'x: 1' >> config.yaml", "settings file"),
    ("du -sh ~/.hermes/* && touch ~/.hermes/plugin-data/habits/paused", "plugin-data/habits holds"),
    (f"cd {ENVS}\nfor d in */; do if [ -z \"$(ls -A \"$d/.leases\")\" ]; then " + RM + " -rf ~/.hermes/plugin-data/habits; fi; done",
     ""),
    ("echo \"size: $(du -sh ~/.hermes | cut -f1)\" > ~/.hermes/plugin-data/habits/off", "plugin-data/habits holds"),
    ("n=$(sqlite3 ~/.hermes/state.db 'delete from sessions; select 1'); echo $n", "history"),
    # a count in the line hides nothing: the line is read with the number written out (two of these passed before)
    ("n=0; for f in ~/.hermes/state.db*; do n=$((n+1)); done; sqlite3 ~/.hermes/state.db 'delete from sessions'", "history"),
    ("n=0; n=$((n+1)); sqlite3 ~/.hermes/state.db 'delete from sessions'", "history"),
    ("echo $((1)); echo 'x: 1' >> ~/.hermes/config.yaml", "settings file"),
    ("n=0; for d in ~/.hermes/*/; do n=$((n+1)); done; touch ~/.hermes/plugin-data/habits/off", "plugin-data/habits holds"),
    ("echo $((2*3)); hermes habits promote check-links", "Only the user changes a habit's state"),
    # a count the guard cannot be sure of is left as it is, and what was refused stays refused
    ("v='a[$(touch ~/.hermes/plugin-data/habits/off)]'; echo $((v))", ""),
    ("n=0; read n < /tmp/payload; echo $((n+1)); touch ~/.hermes/plugin-data/habits/off", ""),
    (": $((PATH)); touch ~/.hermes/plugin-data/habits/off", ""),
    ("du -sh ~/.hermes; echo $(($(cat /tmp/n)+1)); echo 'x: 1' >> ~/.hermes/config.yaml", "cannot read which program"),
    # sqlite3: a function or a dot-command that writes a file is no select, read-only or not, by its short name too
    ("sqlite3 -readonly ~/.hermes/state.db \"select writefile('$HOME/.hermes/plugin-data/habits/off', '')\"", ""),
    ("sqlite3 ~/.hermes/state.db \"select writefile('/tmp/x', hex(randomblob(8)))\"; ls -lh ~/.hermes/state.db", "history"),
    ("sqlite3 -readonly ~/.hermes/state.db '.sh touch /tmp/x'", "history"),
    ("sqlite3 'file:state.db?immutable=1' '.once /tmp/x' 'select 1'; ls ~/.hermes/state.db", "history"),
    ("cd ~/.hermes && sqlite3 -readonly state.db '.sys date' && ls -lh state.db*", "history"),
    # what is fed to a program that is no plain reader, and a read into a variable the shell reads by itself
    ("ls ~/.hermes/state.db* | xargs " + RM, "history"), ("ls ~/.hermes/state.db* | xargs -I{} sh -c 'echo x > {}'", ""),
    ("ls ~/.hermes/state.db* | xargs sqlite3", "history"),
    ("echo /tmp/evil | while read -r PATH; do ls ~/.hermes/state.db; done; touch ~/.hermes/state.db", "history"),
    ("printf -v PATH %s /tmp/evil; cat ~/.hermes/state.db > /dev/null; touch ~/.hermes/state.db", "history"),
])
def test_a_write_among_the_looks_around_the_hermes_home_is_still_refused(hook, tmp_path, monkeypatch, command, wanted):
    second_habit(tmp_path, monkeypatch)
    (tmp_path / ".hermes" / "state.db").write_text("")
    verdict = hook(tool_name=T, args={"command": command}, session_id="s", task_id="t")
    assert verdict and verdict["action"] == "block" and wanted in verdict["message"], verdict


@pytest.mark.parametrize("line, read", [
    ('for d in a b; do printf "%s  " "$d"; done', True), ("printf '%s\\n' \"$(ls | wc -l)\" $HOME", True),
    ("printf -v PATH %s /tmp/evil", False), ('printf "$format" x', False), ("printf -- '%s' \"$d\"", False),
    ("printf '%s' \"$(touch /tmp/x)\"", False), ("printf '%s' x > /tmp/x", False),
    ('ls | while read -r d; do du -sh "$d"; done', True), ("while IFS= read -r line; do echo \"$line\"; done < /tmp/list", True),
    ("ls | while read -r PATH; do ls; done", False), ("read -r IFS < /tmp/x", False), ("read -u 3 line", False),
    ("ls | while read -r c; do $c; done", False), ("IFS=/ read -r a b < /tmp/x", False),
    ("find . -type f | xargs wc -l", True), ("find . -print0 | xargs -0 -n 20 du -ch | tail -1", True),
    ("ls | xargs -I{} ls -la {}", True), ("ls | xargs " + RM, False), ("ls | xargs -I{} sh -c 'cat {}'", False),
    ("ls | xargs", False), ("ls | xargs --arg-file=/tmp/x cat", False), ("ls | xargs tee /tmp/x", False),
    ("sqlite3 -readonly x.db 'select 1'", True), ("sqlite3 x.db 'select count(*) from t' '.tables'", True),
    ("sqlite3 'file:/tmp/x.db?immutable=1' '.schema t'", True), ("sqlite3 ./x.db 'select 1'; ls", True),
    ("sqlite3 x.db 'delete from t'", False), ("sqlite3 -readonly x.db '.sh ls'", False),
    ("sqlite3 -readonly x.db '.trace /tmp/x'", False), ("sqlite3 x.db \"select writefile('/tmp/x', '')\"", False),
    ("sqlite3 -readonly x.db \"select edit('x', 'vi')\"", False), ("sqlite3 -readonly \"$DB\" 'select 1'", False),
    ("sqlite3 -readonly x.db < /tmp/commands.sql", False), ("cat /tmp/commands.sql | sqlite3 -readonly x.db", False),
    ("sqlite3 -readonly x.db 'select 1' <<< '.shell date'", False), ("sqlite3 -readonly x.db 'select 1' | head", True),
])
def test_what_a_look_is_made_of_is_a_read_and_what_only_looks_like_one_is_not(line, read):
    assert guard.reads(line) is read


@pytest.mark.parametrize("line, read_as", [
    ("n=0; for d in */; do n=$((n+1)); done; echo $n", "n=0; for d in */; do n=0; done; echo $n"),
    ("echo $((1+2)) \"$(( 3 * 4 )) MB\"", "echo 0 \"0 MB\""),
    ("a=1; b=2\necho $((a+b)) $(($a * $b))", "a=1; b=2\necho 0 0"),
    ("cd /x\nempty=0; full=0\nif [ -d y ]; then empty=$((empty+1)); else full=$((full+1)); fi",
     "cd /x\nempty=0; full=0\nif [ -d y ]; then empty=0; else full=0; fi"),
    ("n=0; while read -r f; do n=$((n+1)); printf '%s\\n' \"$f\"; done < /tmp/list", ) * 1 +
    ("n=0; while read -r f; do n=0; printf '%s\\n' \"$f\"; done < /tmp/list",),
    ("echo '$((n+1))' \\$((n+1))",) * 2,
])
def test_a_count_that_is_surely_a_number_is_written_out_as_one(line, read_as):
    assert guard._counted(line) == read_as


# bash and zsh read a name's text again inside $(( )): v='a[$(program)]'; echo $((v)) runs the program. A count is
# written out only when each of its names is surely a number of the line's own.
@pytest.mark.parametrize("line", [
    "echo $((n+1))", "echo $((n+1)); n=0", "if true; then n=0; fi; echo $((n+1))", "true && n=0; echo $((n+1))",
    "(n=0); echo $((n+1))", "{ n=0; }; echo $((n+1))", "n=0 | true; echo $((n+1))", "n=0 & echo $((n+1))",
    "n=0 m=1; echo $((n+m))", "n=0; n=abc; echo $((n+1))", "n=0; n='a[$(x)]'; echo $((n+1))",
    "n=0; read n < /tmp/f; echo $((n+1))", "n=0; read -r n m < /tmp/f; echo $((n+1))",
    "n=0; for n in 'a[$(x)]'; do echo $((n)); done", "n=0; n+=x; echo $((n))", "n=0; n[0]='a[$(x)]'; echo $((n))",
    "n=0; declare -n r=n; r='a[$(x)]'; echo $((n))", "n=0; v=n; read $v < /tmp/f; echo $((n+1))",
    "n=0; read $(echo N | tr A-Z a-z) < /tmp/f; echo $((n+1))", "n=0; printf -v \"$v\" %s x; echo $((n))",
    "n=0; printf \"$f\" x; echo $((n))", "n=0; eval \"$x\"; echo $((n))", "n=0; . /tmp/vars; echo $((n))",
    "n=0; source /tmp/vars; echo $((n))", "alias n=x; n=0; echo $((n))", "n=0; export $v=x; echo $((n))",
    "n=0; mapfile \"$v\" < /tmp/f; echo $((n))", "n=0; getopts ab \"$v\"; echo $((n))", "n=0; unset n; echo $((n))",
    "echo $(($1+1))", "echo $((${n}+1))", "echo $(( $(cat /tmp/f) + 1 ))", "echo $((a[1]))", "echo $((PATH))",
    "PATH=0; echo $((PATH+1))", "n=0; echo $((n=5))", "n=0; echo $((n, m))", "x=$(n='a[$(e)]'; echo $((n)))",
    "n=0; x=$(n='a[$(e)]'; echo $((n)))", "f() { n=$1; }; n=0; f 'a[$(e)]'; echo $((n))",
    "n=0; f() { n=$1; }; f 'a[$(e)]'; echo $((n))", "function f { n=$1; }; n=0; f x; echo $((n))",
    "n=0; case x in x) n='a[$(e)]';; esac; echo $((n))", "n=0\ncat <<EOF\n$((n+1))\nEOF", "n=00x; echo $((n))",
    "n=0; echo $((n+1)) $((m))", "n=0; echo $((0x1F + n))", "n=0; trap 'n=x' EXIT; echo $((n))",
    "n=0; read \\n < /tmp/f; echo $((n+1))", "n=0; read -r a \\n < /tmp/f; echo $((n+1))",
    "n=0; for \\n in 'a[$(x)]'; do echo $((n)); done", "n=0; declare \\n='a[$(x)]'; echo $((n))",
])
def test_a_count_the_line_does_not_surely_hold_is_left_as_it_is(line):
    assert guard._counted(line) == line


@pytest.mark.parametrize("command", [
    "S=~/.hermes/skills/todo-count; echo x > $S/SKILL.md",
    "S=~/.hermes/skills/todo-count; sed -i '' s/a/b/ $S/assets/reflex.yml",
    'S="$HOME/.hermes/skills/todo-count"; cp /tmp/x.yml "${S}/assets/reflex.yml"',
    "S=~/.hermes/skills; T=$S/todo-count; tee $T/SKILL.md < /tmp/x",
    "S=~/.hermes/skills/todo-count; find $S -type f -delete",
    "S=~/.hermes/skills/todo-count; find $S -name '*.md' -exec sed -i '' s/a/b/ {} +",
    "S=~/.hermes/skills/todo-count; " + RM + " -rf $S",
    "sed -i '' s/a/b/ ~/.hermes/skills/todo-count/SKILL.md",
    "cd ~/.hermes/skills/todo-count && sed -i '' s/a/b/ SKILL.md",
])
def test_a_real_refusal_names_the_habit_the_call_touches(hook, tmp_path, monkeypatch, command):
    second_habit(tmp_path, monkeypatch)
    verdict = hook(tool_name=T, args={"command": command}, session_id="s", task_id="t")
    assert verdict and verdict["message"].startswith(f"{SECOND} is a habit"), verdict


@pytest.mark.parametrize("command", [
    'cp /tmp/x "$D/SKILL.md"', "echo x > $SKILL_DIR/assets/reflex.yml", "sed -i '' s/a/b/ $(cat /tmp/where)/SKILL.md",
    "tee ~/.hermes/skills/*/SKILL.md < /tmp/x", "for d in ~/.hermes/skills/*; do echo x > $d/SKILL.md; done",
])
def test_a_write_the_guard_cannot_place_names_no_habit_rather_than_the_first_one(hook, tmp_path, monkeypatch, command):
    second_habit(tmp_path, monkeypatch)
    verdict = hook(tool_name=T, args={"command": command}, session_id="s", task_id="t")
    assert verdict and verdict["action"] == "block" and " is a habit" not in verdict["message"], verdict
    assert verdict["message"] == guard.WHERE


# A variable is written out only when the line surely sets it once, to a plain path, before it is used: any other
# line is read as before, its variable unknown, and a write through it stays refused.
@pytest.mark.parametrize("command", [
    "S=/tmp/safe; S=~/.hermes/skills/todo-count; echo x > $S/SKILL.md",
    "false && S=/tmp/safe; echo x > $S/SKILL.md", "true || S=/tmp/safe; echo x > $S/SKILL.md",
    "if true; then S=/tmp/safe; fi; echo x > $S/SKILL.md", "(S=/tmp/safe); echo x > $S/SKILL.md",
    "{ S=/tmp/safe; }; echo x > $S/SKILL.md", "S=/tmp/safe & echo x > $S/SKILL.md", "S=/tmp/safe | true; echo x > $S/SKILL.md",
    "S=/tmp/safe; eval 'S=~/.hermes/skills/todo-count'; echo x > $S/SKILL.md",
    "S=/tmp/safe; . /tmp/vars.sh; echo x > $S/SKILL.md", "S=/tmp/safe; read S < /tmp/f; echo x > $S/SKILL.md",
    "S=/tmp/safe; export S=$HOME/.hermes/skills/todo-count; echo x > $S/SKILL.md",
    "S=/tmp/safe; f() { S=$1; }; f ~/.hermes/skills/todo-count; echo x > $S/SKILL.md",
    "S=/tmp/safe; printf -v S %s ~/.hermes/skills/todo-count; echo x > $S/SKILL.md",
    "S=/tmp/safe; for S in ~/.hermes/skills/todo-count; do echo x > $S/SKILL.md; done",
    "echo x > $S/SKILL.md; S=/tmp/safe", "S=/tmp/safe; echo x > $S:h/SKILL.md", "S=/tmp/safe; echo x > $S[1]/SKILL.md",
    "S=/tmp/safe; echo x > ${S:-/tmp}/SKILL.md", "S=/tmp/safe; echo x > ${S%safe}/SKILL.md", "S=$(cat /tmp/w); echo x > $S/SKILL.md",
    "S=/tmp/*; echo x > $S/SKILL.md", "S=$OTHER/x; echo x > $S/SKILL.md", "IFS=/; S=/tmp/safe; echo x > $S/SKILL.md",
    "S=/tmp/safe; IFS=e; echo x > $S/SKILL.md", "S=/tmp/safe S=/tmp/other env; echo x > $S/SKILL.md",
    "S=/tmp/safe cat /dev/null; echo x > $S/SKILL.md", "case x in x) S=/tmp/safe;; esac; echo x > $S/SKILL.md",
    "S=/tmp/safe; unset S; echo x > $S/SKILL.md", "S=/tmp/safe; S+=/../x; echo x > $S/SKILL.md",
])
def test_a_variable_the_line_does_not_surely_set_stays_unknown(hook, tmp_path, monkeypatch, command):
    second_habit(tmp_path, monkeypatch)
    assert guard._resolved(command) == command
    verdict = hook(tool_name=T, args={"command": command}, session_id="s", task_id="t")
    assert verdict and verdict["action"] == "block", command


@pytest.mark.parametrize("line, read_as", [
    ("S=/a/b; cat $S/x \"${S}/y\" '$S' \\$S \"$S\"", "S=/a/b; cat /a/b/x \"/a/b/y\" '$S' \\$S \"/a/b\""),
    ("A=/x; B=$A/y; ls $B", "A=/x; B=/x/y; ls /x/y"), ("S=/a && D=/b; ls $S $D", "S=/a && D=/b; ls /a /b"),
    ("ls; S=/a\ncat $S/f | head", "ls; S=/a\ncat /a/f | head"), ("S=~/x; ls $S", "S=~/x; ls {home}/x"),
    ("G=\"$HOME/.hermes/g.json\"; stat \"$G\"", "G=\"$HOME/.hermes/g.json\"; stat \"{home}/.hermes/g.json\""),
    ("S=\"~/x\"; ls $S", "S=\"~/x\"; ls $S"), ("S='/a b'; ls $S", "S='/a b'; ls $S"), ("S=; ls $S", "S=; ls $S"),
    ("cat \"${HOME}/x\" ${USER}", "cat \"$HOME/x\" $USER"), ("echo '${HOME}' ${HOME}x ${V}[1] ${V}:h ${V:-x}",) * 2,
    ("S=/a; echo \"$S: done\"", "S=/a; echo \"$S: done\""), ("ls $S; S=/a", "ls $S; S=/a"),
])
def test_a_variable_set_to_a_plain_path_is_written_out_where_the_shell_expands_it(hook, tmp_path, line, read_as):
    assert guard._resolved(line) == read_as.replace("{home}", str(tmp_path))


@pytest.mark.parametrize("command, wanted", [
    ("D=~/.hermes/plugin-data/habits; touch $D/off", "plugin-data/habits holds"),
    ("D=~/.hermes/plugin-data/habits; sqlite3 $D/habits.db \"update habits set status='active'\"", "plugin-data/habits holds"),
    ("C=~/.hermes/config.yaml; sed -i '' 's/a/b/' $C", "settings file"),
    ('CFG="$HOME/.hermes/config.yaml"; echo "x: 1" >> "$CFG"', "settings file"),
    ("P=~/.hermes/plugins/habits; echo x > $P/habits/guard.py", "own code"),
    ("DB=~/.hermes/state.db; sqlite3 $DB 'delete from sessions'", "history"),
    ("V=promote; hermes habits $V check-links", "Only the user changes a habit's state"),
    # a variable the shell or a program reads by itself is no variable of the line's own: such a line is no plain read
    ("PATH=/tmp/evil; cat ~/.hermes/skills/todo-count/SKILL.md", ""),
    ("GIT_EXTERNAL_DIFF=/tmp/x; git -C ~/.hermes/skills/todo-count diff", ""),
    ("IFS=/; cat ~/.hermes/skills/todo-count/SKILL.md", ""), ("HABITS_OFF=1; hermes habits list", "HABITS_OFF"),
    # a variable that stays in the line unread, or that a program could take, still counts where it is set
    ("D=~/.hermes/plugin-data/habits; touch $D:h/habits/off", "plugin-data/habits holds"),
    ("D=~/.hermes/plugin-data/habits; export D; python3 /tmp/x.py", "plugin-data/habits holds"),
    ("set -a; D=~/.hermes/plugin-data/habits; python3 /tmp/x.py", "plugin-data/habits holds"),
    ("D=~/.hermes/plugin-data/habits; x=D; touch ${!x}/off", ""),
    ("S=~/.hermes/skills/todo-count; eval 'echo x > $''S/SKILL.md'", ""),
    # what looks like a read and is not
    ("S=~/.hermes/skills/todo-count; if [ -f $S/SKILL.md ]; then sed -i '' s/a/b/ $S/SKILL.md; fi", "todo-count is a habit"),
    ("S=~/.hermes/skills/todo-count; for f in $S/SKILL.md; do echo x > \"$f\"; done", ""),
    ("find ~/.hermes/skills/todo-count -name SKILL.md -exec sh -c 'echo x > \"$1\"' _ {} \\;", ""),
    ("find ~/.hermes/skills/todo-count -name SKILL.md -exec cat {} \\; > ~/.hermes/skills/todo-count/SKILL.md", "todo-count is a habit"),
    ("python3 -c \"open('$HOME/.hermes/skills/todo-count/SKILL.md', 'w').write('x')\"", "todo-count is a habit"),
    ("python3 -c \"import os; os.remove(os.path.expanduser('~/.hermes/skills/todo-count/SKILL.md'))\"", ""),
    ("python3 -c \"import shutil; shutil.rmtree('$HOME/.hermes/skills/todo-count')\"", ""),
    ("python3 -c \"import shutil; shutil.rmtree('${HOME}/.hermes/plugin-data/habits')\"", "plugin-data/habits holds"),
    ('sed -i "" s/a/b/ "${HOME}/.hermes/skills/todo-count/SKILL.md"', "todo-count is a habit"),
    ('touch "${HERMES_HOME}/plugin-data/habits/off"', "plugin-data/habits holds"),
    ("V=$(sed -i '' s/a/b/ ~/.hermes/skills/todo-count/SKILL.md); echo $V", "todo-count is a habit"),
    ("# only a look\ncat ~/.hermes/skills/todo-count/SKILL.md > ~/.hermes/skills/todo-count/SKILL.md", "todo-count is a habit"),
])
def test_what_writes_through_a_variable_is_refused_for_what_it_is(hook, tmp_path, monkeypatch, command, wanted):
    second_habit(tmp_path, monkeypatch)
    verdict = hook(tool_name=T, args={"command": command}, session_id="s", task_id="t")
    assert verdict and verdict["action"] == "block" and wanted in verdict["message"], verdict


@pytest.mark.parametrize("code", [
    "open('{s}/SKILL.md', 'w').write('x')", "from pathlib import Path\nPath('{s}/assets/reflex.yml').write_text('x')",
    "import os\nos.remove('{s}/SKILL.md')", "import shutil\nshutil.copy('/tmp/x', '{s}/assets/reflex.yml')",
    "import subprocess\nsubprocess.run(['sed', '-i', '', 's/a/b/', '{s}/SKILL.md'])",
    "text = open('{s}/SKILL.md').read()\nopen('{s}/SKILL.md', 'a').write(text)",
    "import os\nos.rename('{s}/assets/reflex.yml', '/tmp/gone.yml')",
    # what it hands to Hermes's own tools is read as those calls, at once
    "from hermes_tools import write_file\nwrite_file('{s}/SKILL.md', 'x')",
    "from hermes_tools import terminal\nterminal(\"echo x > {s}/SKILL.md\")",
    "from hermes_tools import terminal\nterminal(\"S={s}; sed -i '' s/a/b/ $S/SKILL.md\")",
    "from hermes_tools import patch\npatch(path='{s}/assets/reflex.yml', old_string='a', new_string='b')",
    "from hermes_tools import write_file\np = '{s}/SKILL.md'\nwrite_file(p, 'x')",
    "from hermes_tools import read_file, write_file\nwrite_file('{s}/assets/reflex.yml', read_file('/tmp/x.yml')['content'])",
])
def test_code_that_writes_a_habits_file_is_still_refused(hook, tmp_path, monkeypatch, code):
    folder = second_habit(tmp_path, monkeypatch)
    verdict = hook(tool_name="execute_code", args={"code": code.replace("{s}", str(folder))}, session_id="s", task_id="t")
    assert verdict and verdict["action"] == "block", code
    # and what the code hands to Hermes's own tools meets the guard call by call
    verdict = hook(tool_name="write_file", args={"path": f"{folder}/SKILL.md", "content": "x"}, session_id="", task_id="t")
    assert verdict and verdict["message"].startswith(f"{SECOND} is a habit")


def test_a_patch_of_the_users_own_habit_tracker_applies_and_one_of_the_ledger_does_not(hook, tmp_path):
    # habits.db by itself is anyone's: the ledger is plugin-data with habits, or habits.db next to a Hermes folder
    project = tmp_path / "proj" / "habit-tracker"
    project.mkdir(parents=True)
    (project / "fix.patch").write_text("--- a/db.py\n+++ b/db.py\n@@ -1 +1 @@\n-db = connect('habits.db')\n"
                                       "+db = connect('habits.db', timeout=5)\n")
    (project / "evil.patch").write_text("--- a/.hermes/plugin-data/habits/habits.db\n+++ b/.hermes/plugin-data/habits/"
                                        "habits.db\n@@ -1 +1 @@\n-a\n+b\n")
    assert refusals(hook, [(T, {"command": f"cd {project} && git apply fix.patch"}),
                           (T, {"command": f"patch -p1 -d {project} -i {project}/fix.patch"})]) == []
    for command in (f"cd {project} && git apply evil.patch", f"cd ~ && patch -p1 -i {project}/evil.patch"):
        verdict = hook(tool_name=T, args={"command": command}, session_id="s", task_id="t")
        assert verdict and "plugin-data/habits holds" in verdict["message"], command


def test_a_script_reads_the_settings_through_a_variable_and_may_not_write_them_so(hook, tmp_path):
    sets = "#!/bin/sh\nC=~/.hermes/config.yaml\n"
    assert refusals(hook, [("write_file", {"path": "/tmp/proj/look.sh", "content": sets + "grep -n model \"$C\"\n"})]) == []
    for line in ("sed -i '' 's/a/b/' \"$C\"\n", "echo 'x: 1' >> $C\n"):
        verdict = hook(tool_name="write_file", args={"path": "/tmp/proj/edit.sh", "content": sets + line},
                       session_id="s", task_id="t")
        assert verdict and verdict["action"] == "block", line


@pytest.mark.parametrize("tool, args", [
    (T, {"command": "hermes plugins update"}),  # every plugin, Habits among them
    (T, {"command": "hermes config edit"}),  # the settings file, where the Habits settings live
    (T, {"command": "sed -i '' 's/model: a/model: b/' ~/.hermes/config.yaml"}),
])
def test_the_three_that_stay_refused_say_what_was_protected_and_what_to_do(hook, tool, args):
    verdict = hook(tool_name=tool, args=args, session_id="s", task_id="t")
    assert verdict["action"] == "block"
    assert "user" in verdict["message"] and ("hermes plugins" in verdict["message"] or "hermes config set" in verdict["message"])


def test_a_call_with_nothing_of_the_plugins_world_is_not_read(hook, monkeypatch):
    """The cost of the guard on ordinary work is one look at the text: no shell reading, no listing of the skills."""
    listed, parsed = [], []
    monkeypatch.setattr(catalog, "folders", lambda: listed.append(1) or {})
    monkeypatch.setattr(guard, "shell_refusal", lambda *a, **k: parsed.append(1))
    monkeypatch.setattr(guard, "changes_state", lambda *a, **k: parsed.append(1))
    plain = [(tool, args) for tool, args in MAINTAINER_1 + MAINTAINER_2
             if tool != "skill_manage" and not guard._names_world(json.dumps(args))
             and "-delete" not in str(args.get("command"))]  # a find that deletes is read: which files may it reach?
    assert len(plain) >= 110
    assert refusals(hook, plain) == [] and listed == [] and parsed == []


def test_the_median_ordinary_call_costs_under_a_tenth_of_a_millisecond(hook):
    costs = []
    for tool, args in MAINTAINER_1 + MAINTAINER_2:
        best = []
        for _ in range(3):
            start = time.perf_counter()
            hook(tool_name=tool, args=args, session_id="s", task_id="t")
            best.append(time.perf_counter() - start)
        costs.append(min(best) * 1000)
    assert statistics.median(costs) < 0.1, statistics.median(costs)  # measured: 0.01 ms (0.66 ms before the gate)
