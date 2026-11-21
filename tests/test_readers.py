import pytest

from reflexes.readers import why_not

READS = [
    "ls -la /tmp", "cat a b", "head -n 5 x", "wc -l x", "stat x", "df -Pk /", "du -sh x", "ps aux", "echo hi",
    "grep -rc a|b notes", "jq .a x.json", "find . -name *.py -maxdepth 2", "tail -n 5 x", "sort -k2,2n x", "uniq -c",
    "date +%s", "date -d yesterday +%F", "sleep 3", "hostname -s", "ping -c 2 example.com",
    "curl -sI -m 5 -o /dev/null -w %{http_code} https://github.com", "curl -sfL --max-time 10 https://a/b",
    "curl -X HEAD https://a", "curl example.com",
    "git status", "git -C /repo log -p -3", "git --no-pager diff --stat", "git branch --show-current", "git branch",
    "git tag -l", "git remote -v", "git config --get user.name", "git stash list", "git --version",
    "systemctl --user is-active x", "journalctl -u x -n 20", "docker ps", "docker logs x", "kubectl get pods",
    "npm ls", "pip list", "python3 --version", "node -v", "hermes logs agent --since 30m", "crontab -l",
    "vcgencmd measure_temp", "/usr/bin/ls -la", "rg --version",
]
NOT_READS = [
    ("rm -rf x", "rm is not known"), ("mv a b", "mv is not known"), ("sh -c ls", "sh is not known"),
    ("xargs rm", "xargs is not known"), ("env", "env is not known"), ("sed -n 1p f", "sed is not known"),
    ("tee out", "tee is not known"), ("ssh host uptime", "ssh is not known"),
    ("find . -delete", "find -delete"), ("find . -exec rm {} ;", "find -exec"), ("tail -f x", "tail -f"),
    ("tail -fn 5 x", "tail -fn"), ("tail --follow=name x", "tail --follow=name"), ("sort -o out x", "sort -o"),
    ("sort -uo out x", "sort -uo"), ("sort --output=out x", "sort --output=out"), ("uniq a b", "second file"),
    ("date 010112002026", "sets the clock"), ("date -s 12:00", "date -s"), ("sleep 30", "10 at most"),
    ("hostname new", "sets the name"), ("ping example.com", "never ends"), ("ping -c 50 x", "never ends"),
    ("curl -d x https://a", "curl -d"), ("curl -o file https://a", "writes a file"),
    ("curl -X POST https://a", "not a GET or a HEAD"), ("curl file:///etc/passwd", "http and https only"),
    ("curl -K cfg https://a", "curl -K"), ("curl -T f https://a", "curl -T"), ("curl", "no URL"),
    ("git push", "git push is not listed"), ("git commit -m x", "git commit"), ("git branch new", "creates a branch"),
    ("git tag v1", "creates a tag"), ("git -c alias.x=!rm log", "git -c"), ("git diff --output=x", "git --output=x"),
    ("git config user.name x", "sets a value"), ("git stash drop", "git stash drop"), ("git remote add o u", "not a read"),
    ("systemctl restart x", "systemctl restart"), ("journalctl -f", "journalctl -f"), ("journalctl -xef", "-xef"),
    ("journalctl --vacuum-time=1d", "--vacuum-time=1d"), ("docker rm x", "docker rm"), ("docker logs -f x", "follows"),
    ("kubectl delete pod x", "kubectl delete"), ("npm install", "npm install"), ("pip install x", "pip install"),
    ("python3 -c print(1)", "runs code"), ("python3 script.py", "runs code"), ("sysctl -w a=1", "sysctl -w"),
    ("sysctl kern.x=1", "sets a value"), ("file -C -m magic", "file -C"), ("tree -o out", "tree -o"),
    ("rg --pre cat x", "rg --pre"), ("crontab -r", "only crontab -l"), ("hermes config set a b", "hermes config"),
    ("unknown-tool --version", "is not known"),
]


@pytest.mark.parametrize("line", READS)
def test_reads(line):
    assert why_not(line.split()) == ""


@pytest.mark.parametrize("line, said", NOT_READS)
def test_not_reads(line, said):
    assert said in why_not(line.split())


def test_a_host_can_teach_its_own_readers():
    extra = {"notes": ["list", "show"], "mytool": True}
    assert why_not(["notes", "list"], extra) == ""
    assert why_not(["notes", "delete", "x"], extra) == "notes delete is not listed as a read"
    assert why_not(["mytool", "anything"], extra) == ""
    assert why_not(["notes", "list"]) == "notes is not known as a command that only reads"


@pytest.mark.parametrize("argv", [[], [1, 2], "ls"])
def test_only_a_list_of_words_is_a_command(argv):
    assert why_not(argv) == "a command is a list of words"
