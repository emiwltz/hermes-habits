---
name: hermes-health
description: "Is Hermes OK? Gateway, model, internet, disk"
---

# Hermes health

Run it by name: `habit_run {name: "hermes-health"}`, or from a terminal: `hermes habits run hermes-health`.
It only reads and calls no model, so it answers even when the model is down.

The slow way, the same steps as `assets/HABIT.yaml`:

1. Gateway: `~/.hermes/gateway_state.json`, and `$HERMES_HOME/gateway_state.json` in a profile. A problem is `startup_failed`, `degraded`, a platform `fatal` or
   `retrying`, or a file older than 3 minutes while it says `running`. No file: no gateway here.
2. Model: `hermes logs agent --since 30m | grep -E 'Non-retryable client error|API call failed after [0-9]+ retries'`.
   The first catches a call refused at once (401 key, 402 credits, 403), the second one that failed after its
   retries (a 5xx, a timeout). The last line names the error.
3. Internet: `curl -sI -m 5 https://github.com`.
4. Errors: `hermes logs errors --since 1h --level ERROR`, grouped by kind.
5. Disk: `df -Pk ~/.hermes`; 90 % or more is a problem.
6. Answer with one line of verdict, then the failing part. When all is well, say so, then list the most frequent
   errors of the last hour if there are any (or that there were none). Say what was read. Change nothing:
   restarting or switching models is the user's call (`hermes gateway restart`, `hermes model`).

It gives its verdict in the default home and in a profile alike, and its last line says what it read: the gateway
file and the disk of `~/.hermes` (one gateway serves every profile and writes its file there; the disk is the same
disk), plus the gateway file and the logs of the active home (`HERMES_HOME`; `hermes logs` reads that home). A
profile with a gateway of its own is seen through its own file; only the 3-minute heartbeat check looks at
`~/.hermes` alone.
A home folder whose path has a space is not supported.
