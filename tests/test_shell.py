import pytest

from habits import shell

# What a habit reads with on a user's machines: every command of the line a reader, used as a reader.
READS = [
    "ls -la ~/Downloads 2>&1 | head -60; ls -la ~/Documents 2>&1 | head -20",
    "find /home/user/Documents -maxdepth 1 -printf '%y %10s %p\\n' | sort -k3 | head -100",
    "ssh -o BatchMode=yes laptop 'hc ls'",
    "ssh -o BatchMode=yes -o ConnectTimeout=5 -q laptop hc ls --json",
    "hc ls", "hc --json read t7", "hc get {terminal}",
    "git -C ~/code/x rev-parse --short HEAD", "git branch -a", "git tag -l 'v*'", "git -c color.ui=never branch -v",
    "git config --get user.name", "git remote -v", "git --no-pager branch --show-current",
    "pgrep -fl hermes", "ps aux | grep -v grep | grep hermes", "uptime", "df -h ~", "du -sh ~/x",
    "sed -n '1,20p' ~/x.md", "sed 's/[0-9]//g' f", "sed -E 's/^# (.*)/\\1/;/^$/d' f", "sed -n '/^## /{p;q}' f",
    "awk '{print $1}' f", "awk -F: '$3 > 100' /etc/passwd", "awk 'NR > 1' f", "awk 'NR>1 {''print $2}' f",
    "jq -r '.items[].name' f", "curl -s https://x.test | jq .", "curl -s -o /dev/null -w '%{''http_code}' https://x.test",
    "curl -sI -X HEAD https://x.test", "curl -sfL {page}",
    "date +%F", "date -u +%H:%M", "date -v-1d +%F", "date -d yesterday +%F",
    "uniq -c f", "sort -u f | uniq", "echo $HOME", "cat $(ls ~/x | head -1)", "wc -l < ~/x.txt",
    "ls ~/{Downloads,Desktop} | wc -l", "grep -c x f >/dev/null 2>&1", "sleep 2 && ls", "env",
    "test -f ~/x && cat ~/x", "[ -d ~/x ] && echo yes || echo no", "hostname -s", "uname -a", "file ~/x",
    "grep x f |\n  wc -l", "stat -f %m ~/x", "ls '$(touch x)'",
    "printf '%s\n' {item}", 'grep -c "end$" f', "echo $",
]

WRITES = [
    # options and arguments that make a reader write
    "sort -o out f", "sort --out=x f", "sort -no x f", "uniq in out", "uniq - out", "tee f",
    "sed -i 's/a/b/' f", "sed -i '' 's/a/b/' f", "sed 's/a/b/w out' f", "sed 's/a/b/e' f", "sed -n 'w out' f",
    "sed 'e touch x' f", "sed -f s.sed f", "sed 'a text' f", "sed 's/[/]/x/w out' f", "sed -l 'w out' p",
    "awk '{system(\"touch x\")}' f", "awk '{print > \"out\"}' f", "awk '{print | \"sh\"}' f",
    "awk '\"cmd\" | getline x'", "awk -f p.awk f", "awk '{print $1; if ($2 > 3) x=1}' f", "awk '@load \"x\"'",
    "find . -exec touch {} +", "find . -delete", "find ~ {-delete,-print}", "find . -ex* touch {} +", "sort --out* f",
    "curl -o f https://x.test", "curl -d x=1 https://x.test", "curl -X POST https://x.test",
    "curl --json '{}' https://x.test", "curl -F a=@f https://x.test", "curl -s {-o,/x} https://x.test",
    "date -s 2020-01-01", "date 0101", "date --set=x", "date -I 0101", "sleep 30", "sleep infinity",
    "hostname newname", "file -C -m x", "env FOO=1 ls", "printf -v PATH %s /tmp; ls", "printf $X",
    # commands that run other commands
    "xargs ls", "eval ls", "exec ls", "source x", ". x", "bash -c ls", "sudo ls", "X=1 ls", "$X", "/tmp/ls",
    "~/.recall/memo wake",
    # redirections, heredocs, substitutions
    "ls > f", "ls >> f", "ls &> f", "ls 2> f", "ls >&f", "ls <> f", "cat <<EOF\nx\nEOF", "cat <(ls)",
    "ls $(touch x)", "ls `touch x`", "echo `sort -o x f`", 'ls "`sort \\"-o\\" x f`"', "cat $'\\x72m'",
    "ls $((1+1))", "ls ${X:-$(touch y)}", "sort $X f", "sort *.txt", "sort $=X f", "ls *(e:'touch x':)",
    # what the reader cannot read
    "ls 'open", "ls # comment", "ls \x01", "curl -s \x01-o", "(ls)", "{ ls; }", "ls |", "ls && ", "; ls", "if true; then ls; fi",
    # git, hc and ssh beyond their reads
    "git commit -m x", "git branch new", "git branch -d old", "git tag v1", "git config user.name x",
    "git -c core.pager=less log", "git -c diff.external=x diff", "git log --output=x", "git log --out=x", "git push",
    "hc run x", "hc send t1 hi", "hc", "hc kill t1", "ssh laptop", "ssh -t laptop 'hc ls'",
    "ssh laptop 'hc run x'", "ssh -o ProxyCommand=x laptop ls", "ssh laptop {item}", 'ssh laptop "$X"',
    "ssh laptop 'ls > f'",
    # templates where a value would become code
    "sed s/x/{item}/ f", "awk {item} f", "date {f}", "curl -s -H {hdr} https://x.test", "uniq {a} {b}",
]


@pytest.mark.parametrize("command", READS)
def test_reads(command):
    assert shell.reads_only(command)


@pytest.mark.parametrize("command", WRITES)
def test_writes(command):
    assert not shell.reads_only(command)


@pytest.mark.parametrize("command, waits", [
    ("ssh laptop", True), ("ssh -o BatchMode=yes laptop", True), ("ls && ssh -A server", True),
    ("ssh laptop 'hc ls'", False), ("ssh -t laptop top", False), ("ls 'open", False),
])
def test_an_ssh_without_a_command_waits_for_a_person(command, waits):
    assert shell.interactive(command) is waits


@pytest.mark.parametrize("command, found", [
    ("curl -s https://x.test", True), ("ls; curl -s https://x.test", True), ("ls|curl -s https://x.test", True),
    ("echo $(curl -s https://x.test)", True), ("ssh -o BatchMode=yes server 'curl -s https://x.test'", True),
    ("ls 'open", True), ("ls ~/curl", False), ("grep curl f", False), ("ssh server 'hc ls'", False),
])
def test_uses_finds_a_command_wherever_it_runs(command, found):
    assert shell.uses(command, "curl") is found


# An agent's screen can hold any page it read: what hc read prints is untrusted, wherever hc runs.
@pytest.mark.parametrize("command, found", [
    ("hc read --lines 0 {item} | tail -n 30", True), ("hc --json read t1", True), ("~/.local/bin/hc read t1", True),
    ("ssh -o BatchMode=yes mac '~/.local/bin/hc read t1' | tail -n 5", True), ("echo $(hc read t1)", True),
    ("ls 'open", True), ("hc ls", False), ("hc get t1 | jq .status", False), ("grep read f | hc ls", False),
])
def test_uses_finds_a_command_s_verb(command, found):
    assert shell.uses(command, "hc", "read") is found


# git reads only where no repository config can name a program: a planted .git ran one for each of these
# (diff.external, a textconv driver, core.fsmonitor, gpg through log.showSignature).
@pytest.mark.parametrize("command", [
    "git diff", "git --no-pager diff --stat", "git status --short", "git log --oneline -3", "git log -p -1",
    "git show", "git blame f.txt", "git ls-files", "git describe --dirty", "git shortlog -s",
    "git -C ~/vault log --since='1 day ago' --name-only", "ssh server 'git -C ~/x status'",
])
def test_git_that_can_run_a_repository_s_own_programs_is_a_write(command):
    assert not shell.reads_only(command)
    assert "own .git/config" in shell.why(command)


# A reader written as its path in a standard bin folder is that reader: ssh's PATH on the Mac lacks ~/.local/bin.
@pytest.mark.parametrize("command, reads", [
    ("/bin/ls ~", True), ("/usr/bin/sort -u f", True), ("~/.local/bin/hc ls", True), ("$HOME/.local/bin/hc ls", True),
    ("ssh -o BatchMode=yes laptop '~/.local/bin/hc ls'", True), ("/opt/homebrew/bin/jq . f", True),
    ("/usr/bin/rm f", False), ("~/bin/ls", False), ("/usr/bin/../../tmp/ls", False), ("$X/.local/bin/hc ls", False),
    ("~/.local/bin/hc run x", False), ("'$HOME'/.local/bin/hc ls", False),
])
def test_a_reader_in_a_standard_bin_folder_is_that_reader(command, reads):
    assert shell.reads_only(command) is reads


def test_a_reader_by_its_path_is_still_found():
    assert shell.uses("~/.local/bin/hc ls", "hc") and shell.uses("/usr/bin/curl -s https://x.test", "curl")
    assert shell.uses("ssh server '/usr/bin/ssh -o BatchMode=yes mac ls'", "ssh")


# hc 0.1.18: status, list and who do not exist; server status, machine ls and templates read. Each read verb
# takes only the options of its --help: an option a later hc adds could do anything.
@pytest.mark.parametrize("command, reads", [
    ("hc server status", True), ("hc machine ls --json", True), ("hc templates", True), ("hc read server/t3 --lines 20", True),
    ("hc ls --all --json --label where=x", True), ("hc ls --label=where=x", True), ("hc get --json t7", True),
    ("hc read --lines 0 {item}", True), ("hc read t7", True), ("hc --json ls", True), ("hc server --json status", True),
    ("hc get {item} | jq -rn 'input | .status.state'", True),
    ("hc status", False), ("hc list", False), ("hc who", False), ("hc server stop", False), ("hc machine rm server", False),
    ("hc ls --follow", False), ("hc get t1 t2", False), ("hc read --lines", False), ("hc read --on server t1", False),
    ("hc templates reviewer", False), ("hc ls t1", False), ("hc machine ls server", False), ("hc events", False),
    ("hc wait t1", False), ("hc label t1 a=b", False), ("hc keys t1 enter", False), ("hc exec ls", False),
    ("hc file read x", False), ("hc interrupt t1", False), ("hc keep t1", False), ("hc put t1 f", False),
])
def test_hc_reads_are_those_of_the_real_hc(command, reads):
    assert shell.reads_only(command) is reads


# One pipeline, whose commands bash's PIPESTATUS gives one status each; a list or a single command is not one.
@pytest.mark.parametrize("command, names", [
    ("ls ~/x | head -n 1", ["ls", "head"]), ("find . -name '*.md' |& grep -v x | wc -l", ["find", "grep", "wc"]),
    ("grep x f |\n  wc -l", ["grep", "wc"]), ("hc get {item} | jq -rn 'input'\n", ["hc", "jq"]),
    ("cat $(ls | head -1) | wc -c", ["cat", "wc"]), ("ssh server 'ls | wc -l' | cat", ["ssh", "cat"]),
    ("ls", None), ("ls | wc -l; ls", None), ("ls && ls | wc -l", None), ("ls | wc -l &", None), ("ls |\n", None),
    ("ls | wc -l\nls", None), ("ls 'open | wc", None), ("ls || wc", None),
])
def test_pipeline_gives_the_commands_of_one_pipe(command, names):
    found = shell.pipeline(command)
    assert (None if found is None else [name for name, _ in found]) == names


def test_pipeline_gives_each_command_s_words():
    assert shell.pipeline("curl -s -w '%{''http_code}' https://x.test | grep -c 000") == [
        ("curl", ["-s", "-w", "%{http_code}", "https://x.test"]), ("grep", ["-c", "000"])]


# The reads the real tasks use (the real tasks): Hermes's logs, the vault's sync settings, the Mac's idle time,
# disks, dates, a timed curl and the find tests of notes changed since.
@pytest.mark.parametrize("command, reads", [
    ("hermes logs", True), ("hermes logs errors --since 1h", True), ("hermes logs gateway -n 100", True),
    ("hermes logs -n50 --level WARNING --session abc --component tools", True), ("hermes logs list", True),
    ("~/.local/bin/hermes logs errors --lines=20", True),
    ("hermes logs -f", False), ("hermes logs --follow", False), ("hermes logs --fol", False), ("hermes logs -fn 5", False),
    ("hermes logs errors gateway", False), ("hermes gateway restart", False), ("hermes send -t telegram x", False),
    ("hermes", False), ("hermes -p x logs", False),
    ("hermes habits check /tmp/x.yaml", True), ("hermes habits show tidy --json", True), ("hermes habits list", True),
    ("hermes habits check", False), ("hermes habits check a b", False), ("hermes habits check -x f", False),
    ("hermes habits run tidy", False), ("hermes habits view", False), ("hermes habits reset tidy", False),
    ("hermes habits list x", False), ("hermes habits show tidy --live", False),
    ("ob sync-status", True), ("ob sync-status --path ~/Documents/vault --json", True), ("ob sync-status --path=~/v", True),
    ("ob sync", False), ("ob sync --path ~/v", False), ("ob sync-status x", False), ("ob sync-status --continuous", False),
    ("ob sync-unlink --path ~/v", False), ("ob login", False),
    ("ioreg -c IOHIDSystem -d 4 | grep HIDIdleTime", True), ("ioreg -n Root -d1 -a | grep -A1 ScreenIsLocked", True),
    ("df -Pk ~/.hermes | tail -n 1", True), ("df -h", True), ("df --output=pcent /", True),
    ("date -v-1d +%d_%m_%Y", True), ("date -v -1H '+%Y-%m-%d %H:%M'", True), ("date -d '1 hour ago' +%s", True),
    ("date --date=yesterday +%F", True), ("date -j -f %Y-%m-%d 2026-11-09 +%s", True), ("date -u -r 1700000000", True),
    ("date -d {when} +%F", True), ("date -Iseconds", True),
    ("date -s '2026-01-01'", False), ("date -v-1d 0101", False), ("date --se=x", False), ("date -u {f}", False),
    ("curl -sfL --max-time 10 'https://publish.twitter.com/oembed?url='{url}", True),
    ("curl -s -S -f -L --connect-timeout 3 -m 10 https://x.test", True), ("curl --connect-timeout=3 -sS https://x.test", True),
    ("curl -s -I -m 4 -w '%{''http_code} %{''url_effective}\\n' https://api.github.com https://api.telegram.org", True),
    ("curl -s -o /dev/null -w '%{''http_code}' --max-time 5 https://x.test", True),
    ("curl -s -w '%output{''f}' https://x.test", False), ("curl -sS --data x https://x.test", False),
    ("find . -name '*.md' -cmin -62 -not -path '*/.*' -not -empty", True),
    ("find ~/v -newerct '1 hour ago' -type f", True), ("find ~/v -maxdepth 1 -printf '%T@ %p\\n'", True),
    ("find ~/v -fprintf out '%p'", False), ("stat -f '%m %N' ~/x", True), ("stat -c '%Y %n' ~/x", True),
])
def test_the_real_tasks_reads(command, reads):
    assert shell.reads_only(command) is reads


# The Hermes health check on the Pi: systemd tells whether the gateway runs, the journal what it logged.
@pytest.mark.parametrize("command, reads", [
    ("systemctl --user is-active hermes-gateway", True), ("systemctl is-enabled ssh", True),
    ("systemctl --user status hermes-gateway --no-pager -n 20", True),
    ("journalctl --user -u hermes-gateway --since '1 hour ago' --no-pager | grep -ci error", True),
    ("journalctl -n 50 -p err -o cat", True),
    ("systemctl --user restart hermes-gateway", False), ("systemctl stop x", False), ("systemctl -H server status x", False),
    ("journalctl --vacuum-time=1d", False), ("journalctl --rotate", False), ("journalctl -f", False),
    ("journalctl --flush", False),
])
def test_systemctl_and_journalctl_read_with_their_reading_verbs_only(command, reads):
    assert shell.reads_only(command) is reads


# Why a line is a write, first cause first, so the model knows what to change.
@pytest.mark.parametrize("command, cause", [
    ("find ~/vault -name '*.md' -mtime -1 -exec stat -f '%Sm %N' {} +", "find -exec acts on what it finds"),
    ("cd ~/vault && ls", "cd is not a reader"),
    ("~/.recall/memo wake", "~/.recall/memo is a path used as the command"),
    ("find ~/vault -iname *{query}*.md | head -5", "an unquoted glob next to a template"),
    ("ssh -o BatchMode=yes laptop {item} ls", "in the ssh remote command: a template as the command"),
    ("find ~/notes -newer ~/.stamp -name *.md", "an unquoted glob at the start of an argument of find (*.md)"),
    ("ls # comment", "a comment (#)"), ("ls > f", "a redirection to a file"), ("sort -o x y", "sort -o writes"),
    ("find \"$DIR\" -type f", "$DIR in an argument of find"), ("ssh server 'ls > f'", "in the ssh remote command: a redirection"),
    ("ls $(sort -o x y)", "inside $( ): sort -o"), ("curl -d x=1 https://x.test", "curl beyond a plain GET"),
    ("curl -s https://api.telegram.org/bot$TELEGRAM_BOT_TOKEN/getMe", "$TELEGRAM_BOT_TOKEN is a secret"),
    ("ls", ""),
])
def test_why_names_the_first_cause(command, cause):
    found = shell.why(command)
    assert found.startswith(cause) if cause else found == ""


@pytest.mark.parametrize("command, waits", [
    ("ssh server < ~/cmds.sh", False), ("cat cmds.sh | ssh -T -o BatchMode=yes server", False), ("ssh -fN -L 8080:x:80 server", False),
    ("ssh -O check server", False), ("ssh -G laptop", False), ("ssh -T git@github.com < /dev/null", False),
    ("ssh -T git@github.com", True), ("ssh -o ForwardAgent=yes server", True), ("ls | grep x; ssh server", True),
])
def test_only_an_ssh_fed_by_the_terminal_waits(command, waits):
    assert shell.interactive(command) is waits


@pytest.mark.parametrize("command, found", [
    ("grep -c ERROR ~/x.log", "grep"), ("cat f | grep -c x", "grep"), ("ls; diff a b", "diff"),
    ("grep x f | head -1", "head"), ("grep x f && ls", None), ("grep x f || true", None), ("ls 'open", None),
])
def test_last_names_the_command_whose_status_the_line_has(command, found):
    assert shell.last(command) == found


# Over ssh the remote shell reads the command again, so a value filled there is checked when the runner fills it (one
# plain word: letters, digits, / _ . -). The remote shell starts in $HOME: .local/bin/hc right after the host is hc.
@pytest.mark.parametrize("command, reads", [
    ("ssh -o BatchMode=yes -o ConnectTimeout=5 laptop .local/bin/hc get --json {item} | jq -rn 'input | .status.state'",
     True),
    ("ssh -o BatchMode=yes -o ConnectTimeout=5 laptop .local/bin/hc ls --json | jq -r '.terminals[].address'", True),
    ("ssh -o BatchMode=yes laptop 'hc read --lines 0 {item}' | tail -n 30", True),
    ("ssh -o BatchMode=yes laptop hc get --json server/{item}", True),
    (".local/bin/hc ls", False), ("ssh laptop 'ls; .local/bin/hc ls'", False), ("ssh laptop .local/bin/rm x", False),
    ("ssh laptop {item}", False), ("ssh laptop 'hc get $X'", False), ("ssh laptop hc send {item} hi", False),
    ("ssh laptop 'hc get {item}' > f", False),
])
def test_an_ssh_read_takes_a_value_in_its_remote_command(command, reads):
    assert shell.reads_only(command) is reads


@pytest.mark.parametrize("command, remote", [
    ("ssh -o BatchMode=yes mac .local/bin/hc get --json {item} | jq -r {key}", {0}),
    ("ssh mac 'hc read {a}' && ls {b}", {0}), ("ls {a} $(ssh server \"cat {b}\")", {1}), ("ssh {host} ls {x}", {0, 1}),
    ("ls {a} | grep {b}", set()), ("ssh mac \"ls $(echo {a})\"", {0}),  # a local $( ) whose output goes over
    ("ssh mac ls {a} (", None),
])
def test_the_templates_an_ssh_remote_shell_reads_again(command, remote):
    assert shell.remote(command) == remote


# C-voix's watch read an age with test $(date +%s) -lt $(jq … get-{item}.json), and was refused
# on "control characters": the reader saw its own template mark inside the $( ). A template there is one quoted word,
# the same as outside it; a write inside it is still a write.
@pytest.mark.parametrize("command, reads", [
    ("test $(date +%s) -lt $(jq -r '.status_ms/1000|floor+1200' ~/hc/get-{item}.json) && echo recent || echo old", True),
    ("cat $(ls ~/notes/{item})", True), ("echo `jq -r .cwd ~/hc/get-{item}.json`", False),
    ("ls $(cp ~/a ~/{item})", False), ("echo $({item})", False), ("echo $(awk {item} f)", False),
    ("echo $(ls \x01)", False),  # the reader's own mark, written in the line itself: still refused
])
def test_a_template_inside_a_substitution_is_one_quoted_word(command, reads):
    assert shell.reads_only(command) is reads


def test_a_template_inside_a_substitution_is_judged_by_its_command():
    assert shell.why("ls $(cp ~/a ~/{item})") == "inside $( ): cp is not a known reader"
    assert shell.why("echo $({item})").startswith("inside $( ): a template as the command")


# Not inside backticks. The shell finds the closing backtick before it reads any quote, so
# a value holding one (a downloaded file's name) leaves the quotes the runner puts around it there; $( ) reads
# its quotes as written.
def test_a_template_inside_backticks_is_refused_with_what_to_write():
    for command in ("echo `basename {item}`", "wc -c \"`echo {item}`\"", "ls `echo ~/{a}` {b}"):
        assert shell.why(command) == "a template inside backticks: write $( ) instead"
    assert shell.why("echo `date +%s` {item}") == ""  # a template beside them is one quoted word


def test_arithmetic_is_refused_with_what_to_use_instead():
    found = shell.why("echo $(( $(date +%s) - 60 ))")
    assert "$(( ))" in found and "compute in jq or awk" in found
    assert shell.why("cat $'\\x72m'") == "$'…' or $\"…\""
