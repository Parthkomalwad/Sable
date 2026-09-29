# sabled: the background daemon

`sabled` does work while you are logged out: scheduled jobs, watchers, and
pushes to your phone. It is one process per user.

## Install

```
sable daemon install          # writes ~/.config/systemd/user/sabled.service
systemctl --user daemon-reload && systemctl --user enable --now sabled
sudo loginctl enable-linger $USER   # keep it running after you log out
sable daemon status | stop
```

`install` never runs `systemctl` itself; it prints the commands. On a host
without systemd, run `sable daemon run` under tmux or nohup. The playground
starts it for you (log: `~/.sable/sabled.log`). A second daemon refuses to
start.

## What runs unattended, and what does not

- **Schedules** (`/schedule "every night at 2am, back up postgres"`). The
  model is called once, now, to draft a cron expression and a plan. You see
  every step with its policy tier and approve once. At run time the daemon
  runs those exact commands; no model is called, so a job cannot drift or be
  steered by output it reads.
- **Policy still decides every step.** `allow` runs. `confirm` (for example
  `rm -rf`) is queued to `/inbox` and the run waits until you approve it; a
  rejection fails the run. `deny` fails the run.
- **Watchers** (`/watch add disk / 90`, `file`, `log`, `http` on localhost).
  Checked every 30 seconds, fired once per state change. Tier `notify` only
  reports; `run` runs an approved plan under policy; `approve` waits in
  `/inbox` before its first step.
- A run left `running` when the daemon died is marked `lost` at the next start.

## Phone: ntfy

```
/notify setup     # server (default https://ntfy.sh), topic, reply topic, access token
/notify test
```

You get a push when a job finishes, a watcher fires, or something waits in
the inbox. An approval push has Approve and Reject buttons. A button sends
`yes <id> <token>` to the reply topic, which the daemon polls (outbound only,
no port is opened). The token is random, single use and valid for one hour;
a replayed, expired, mismatched or malformed reply is refused and recorded
in `/audit` as `approval.remote`. Only `confirm`-tier items can be approved
this way.

An access token (stored in the keyring, never in config) protects reading
your push topic. It is never put in the buttons. The reply topic must accept
writes without it; that is safe because a reply only counts with its
single-use token, which only the matching push carries. The daemon checks the
reply topic every 30 seconds, so a tap lands within half a minute. A push that
fails to send is retried on the next tick.

## `/inbox`

Everything waiting on you, in one list: approvals (a scheduled job's step
shows as `scheduled job <name>`), breaker trips, skill proposals.
`/inbox approve a7`, `/inbox reject a7`, `/inbox show b2`. Keys are stable, so
a key typed from an older listing never lands on a different item.
