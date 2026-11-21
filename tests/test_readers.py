import pytest

from reflexes.readers import why_not

READS = """
ls -la /tmp | cat a b | head -n 5 x | wc -l x | stat -c %Y x | df -Pk / | du -sh x | ps aux | ps -p 12 -o pid=
echo hi | grep -rc --include=*.py TODO /p | jq .a x.json | find . -maxdepth 1 -type f -name *.tmp -mtime +7
tail -n 5 x | tail -20 x | tail -c 100 x | tail +5 x | sort -k2,2n -t, x | sort -rn x | uniq -c | uniq -c in
date +%s | date -d yesterday +%F | date -u | date -Iseconds | date -j -f %Y 2020 +%s | sleep 3 | hostname -s
ping -c 2 example.com | file -b x | tree -L 2 x | sysctl -n kern.ostype | lsof -i :8080 | rg -n TODO src
curl -sI -m 5 -o /dev/null -w %{http_code} https://github.com | curl -sfL --max-time 10 https://a/b
curl -X HEAD https://a | curl example.com | curl -sL -r 0-0 -o /dev/null -w %{http_code} -m 10 https://a
git status --porcelain | git -C /repo log -p -3 --stat | git -C /repo log -5 --format=%s | git --no-pager diff --stat
git branch --show-current | git branch | git branch -a | git branch --list feat* | git tag -l | git tag -n5
git remote -v | git remote get-url origin | git config --get user.name | git config get user.name | git stash list
git stash show | git reflog | git --version | git log --exclude=x --all | git diff --text
systemctl --user is-active x | systemctl status x --no-pager -n 20 | journalctl -u x -n 20 --no-pager
journalctl -b -1 -p err | docker ps -a --format {{.Names}} | docker logs --tail 50 x | kubectl get pods -n ns -o json
hermes logs agent --since 30m -n 5000 | hermes --version | crontab -l | pmset -g batt | vcgencmd measure_temp
python3 --version | node -v | java -version | /usr/bin/ls -la | /bin/cat x
journalctl -n20 -u x | journalctl --lines=20 | journalctl --boot=-1 -p err | date --iso-8601=seconds | date --iso-8601
git branch --color=always -a | git config user.email | git config --global user.name | tree -aL 2 x
lsof -nP -iTCP -sTCP:LISTEN | lsof -i | ss -tlnp | ss -tulpn state listening | netstat -an | printenv HOME
ping -c1 example.com | ip addr | ip -br a | ip route show | ip -4 addr show dev eth0 | ip route get 1.1.1.1
curl -s --max-redirs 3 https://a | curl -s localhost:8080/health | curl -sN --no-progress-meter https://a
"""
NOT_READS = [
    # programs that are not readers at all
    ("rm -rf x", "rm is not known"), ("mv a b", "mv is not known"), ("sh -c ls", "sh is not known"),
    ("xargs rm", "xargs is not known"), ("env", "env is not known"), ("sed -n 1p f", "sed is not known"),
    ("tee out", "tee is not known"), ("ssh host uptime", "ssh is not known"), ("unknown --version", "is not known"),
    # a reader named from somewhere else is another program
    ("./ls -la", "a program of its own"), ("/tmp/evil/cat x", "a program of its own"),
    # options that write, run something or never end; cut short, bundled, or with their value attached
    ("find . -delete", "find -delete"), ("find . -exec rm {} ;", "find -exec"), ("find . -fprint out", "find -fprint"),
    ("tail -f x", "tail -f"), ("tail -fn 5 x", "tail -f"), ("tail --follow=name x", "tail --follow"),
    ("tail --fo x", "tail --fo"), ("tail +1f x", "a number only"),
    ("sort -o out x", "sort -o"), ("sort -uo out x", "sort -o"), ("sort --output=out x", "sort --output"),
    ("sort --o out x", "sort --o"), ("sort --outp=out x", "sort --outp"), ("sort --compress-prog=evil x", "sort --compress-prog"),
    ("uniq a b", "takes 1 name"), ("uniq -- in out", "takes 1 name"),
    ("date 010112002026", "sets the clock"), ("date -s 12:00", "date -s"), ("date -s2001-01-01", "date -s"),
    ("date --se=2001-01-01", "date --se"), ("sleep 30", "10 at most"),
    ("hostname new", "takes 0 names"), ("hostname --file=F", "hostname --file"), ("hostname -F f", "hostname -F"),
    ("sysctl -w a=1", "sysctl -w"), ("sysctl kern.x=1", "sets a value"), ("sysctl -qp FILE", "sysctl -p"),
    ("ping example.com", "never ends"), ("ping -c 50 x", "never ends"), ("ping -f -c 2 x", "ping -f"),
    ("file -C -m magic", "file -C"), ("tree -o out", "tree -o"), ("rg --pre cat x", "rg --pre"),
    ("journalctl -f", "journalctl -f"), ("journalctl -xef", "journalctl -f"), ("journalctl --rot", "journalctl --rot"),
    ("journalctl --vacuum-time=1d", "journalctl --vacuum-time"),
    ("curl -d x https://a", "curl -d"), ("curl -o file https://a", "writes a file"), ("curl -so f https://a", "curl -so"),
    ("curl -X POST https://a", "not a GET or a HEAD"), ("curl file:///etc/passwd", "http and https only"),
    ("curl -K cfg https://a", "curl -K"), ("curl -T f https://a", "curl -T"), ("curl", "no URL"),
    ("curl -w %output{/tmp/x}hi https://a", "writes a file"), ("curl --url gopher://h:1/_x https://a", "http and https only"),
    ("curl -D headers https://a", "curl -D"), ("curl -c jar https://a", "curl -c"),
    # an option whose value is optional never hides the next word
    ("journalctl -n --vacuum-files=1", "journalctl --vacuum-files"), ("journalctl --boot --rotate", "journalctl --rotate"),
    ("journalctl --lines --vacuum-time=1s", "journalctl --vacuum-time"), ("lsof -i -Db", "lsof -D"),
    ("date --iso-8601 --set=2001-01-01", "date --set"), ("git branch --color --unset-upstream", "git branch --unset-upstream"),
    ("git branch --color --edit-description", "git branch --edit-description"),
    # BSD date sets the clock from a value unless -j; tree reads a bundle its own way
    ("date -f %s 0", "sets the clock"), ("date -v +1d 0101000070", "sets the clock"),
    ("tree -Lo 2 out.txt", "takes its value in the next word"),
    # a scheme with one slash is a scheme too, and curl guesses one from a host name
    ("curl gopher:/127.0.0.1:6379/_x", "http and https only"), ("curl dict:/h/info", "http and https only"),
    ("curl --url telnet:/h:1", "http and https only"), ("curl ftp.example.com/f", "http and https only"),
    ("curl FILE:/etc/passwd", "http and https only"),
    # programs that run or fetch another one behind an innocent word
    ("arch -arm64 /bin/rm x", "arch is not known"), ("go --version", "go is not known"),
    ("cargo --version", "cargo is not known"), ("git log --show-signature", "git --show-signature"),
    ("git log --format=%G?", "runs gpg"), ("ss -K dst 1.2.3.4", "ss -K"), ("ss -D out", "ss -D"),
    ("ip addr add 1.2.3.4 dev eth0", "ip reads only"), ("ip -b file", "ip reads only"),
    ("ip link set eth0 down", "ip reads only"), ("ip a a 1.2.3.4 dev x", "ip reads only"), ("ping -c50 x", "never ends"),
    # git: what changes something, and what can write a file or run a program, however it is spelt
    ("git push", "git push is not listed"), ("git commit -m x", "git commit"), ("git grep -n TODO", "git grep"),
    ("git grep --open=evil x", "git --open"), ("git log --outp=x", "git --outp"), ("git show --ext", "git --ext"),
    ("git cat-file --textconv a", "git --textconv"), ("git diff --output=x", "git --output"),
    ("git -c alias.x=!rm log", "git -c"), ("git --git-dir=x status", "git --git-dir=x"),
    ("git branch new", "creates a branch"), ("git branch -r -D origin/x", "creates a branch"), ("git branch -D --list x", "git branch -D"),
    ("git branch -M a b -a", "creates a branch"), ("git branch -a -M", "git branch -M"), ("git tag v1", "creates a tag"), ("git tag -d v1 -l", "git tag -d"),
    ("git config user.name x", "git config reads only"), ("git config --unset user.name", "git config reads only"),
    ("git config --get a --add b", "git config reads only"), ("git remote add o u", "git remote reads only"),
    ("git remote set-url origin x", "git remote reads only"), ("git stash", "git stash reads only"),
    ("git stash -u", "git stash reads only"), ("git stash drop", "git stash reads only"),
    ("git worktree add x", "git worktree reads only"), ("git reflog expire --all", "git reflog expire"),
    # a subcommand hidden behind an option, and subcommands that change something
    ("pip --log list uninstall x", "pip runs code"), ("docker -l info context create x", "the subcommand comes first"),
    ("kubectl -n get delete pod x", "the subcommand comes first"), ("npm --prefix ls install", "npm runs code"),
    ("go env -w GOFLAGS=x", "go is not known"), ("cargo metadata --config build.rustc=x", "cargo is not known"),
    ("systemctl restart x", "systemctl restart"), ("systemctl -H host status x", "the subcommand comes first"),
    ("systemctl status x -H host", "systemctl status -H"), ("docker rm x", "docker rm"),
    ("docker logs -f x", "never ends"), ("docker logs -tf x", "never ends"), ("kubectl get pods -w", "never ends"),
    ("kubectl delete pod x", "kubectl delete"), ("hermes config set a b", "hermes config"),
    ("crontab -r", "only `crontab -l`"), ("pmset sleepnow", "only `pmset -g`"),
    # an interpreter reads only when it says its version
    ("python3 -c print(1)", "runs code"), ("python3 script.py", "runs code"), ("python3 version", "runs code"),
    ("make version", "runs code"), ("npm install", "runs code"), ("pip install x", "runs code"),
]


@pytest.mark.parametrize("line", [line.strip() for chunk in READS.strip().splitlines() for line in chunk.split(" | ")])
def test_reads(line):
    assert why_not(line.split()) == ""


@pytest.mark.parametrize("line, said", NOT_READS)
def test_not_reads(line, said):
    assert said in why_not(line.split())


def test_a_host_can_teach_its_own_readers():
    extra = {"notes": ["list", "show"], "mytool": True}
    assert why_not(["notes", "list"], extra) == ""
    assert why_not(["notes", "delete", "x"], extra) == "notes delete is not listed as a read"
    assert why_not(["notes", "--all", "list"], extra) == "notes --all is not listed as a read"
    assert why_not(["mytool", "anything"], extra) == ""
    assert why_not(["notes", "list"]) == "notes is not known as a command that only reads"


@pytest.mark.parametrize("argv", [[], [1, 2], "ls"])
def test_only_a_list_of_words_is_a_command(argv):
    assert why_not(argv) == "a command is a list of words"
