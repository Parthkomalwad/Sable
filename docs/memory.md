# Memory in Sable

Sable remembers what it learns about a server, shows where each fact came
from, and forgets on request. This is the memory palace.

## How facts get in

- **You:** `/remember deploys go out on Tuesdays` saves to the `user` room, or
  `--room server|incidents|repos/<name>` to another room.
- **Agents:** when a goal discovers something durable about this server, such
  as a path, a port or where a service logs, the agent saves it as a fact when
  it finishes. You see a `remembered: ...` line for each one, so nothing is
  saved silently.

Each fact is one markdown file under `~/.sable/palace/<room>/`, with a header
listing its sources: the session, the goal and the commands that ran. You can
read or edit the files by hand. The search index rebuilds from them with
`sable doctor --fix`. Secrets are redacted before a fact is written.

## How facts are used

At the start of a goal, the facts that match it are shown to the agent as
notes, marked as data that may be stale. A question they answer is answered
straight from memory, with no commands run. A note never grants permission:
policy decides every command as before.

A fact learned after the agent read untrusted content, such as a web page, is
stored as untrusted and shown with that label.

## Looking and forgetting

```
/palace                  rooms and how many facts each holds
/palace server           the facts in one room
/palace find nginx       search every room
/palace why f45a3ba393d78   the fact, its tier and every source
/forget f45a3ba393d78    delete it
```

## Nightly consolidation

The daemon tidies the palace once a day at `maintenance_time` (default
`02:30`), or on demand with `/palace consolidate`. It uses rules, not a model:

- near-duplicate facts in a room become one, keeping every source;
- a fact seen in two separate sessions is promoted from episodic to semantic;
- episodic facts older than 30 days, and facts past their `valid_to`, are removed.

## Checking and moving an install

```
sable doctor             check tools, database, config version, palace index
sable doctor --fix       migrate config (with a backup), fix permissions, reindex
sable export             skills, palace, your policy and hooks, as a tarball
sable import file.tar.gz shows what it adds and overwrites, backs up, then writes
sable sync <git-remote>  the same set through git
```

Exports never include the keyring, the database, logs or `config.json`, and
refuse to run if a file contains a secret. Imports reject absolute paths,
`..`, links and archives whose hashes do not match their manifest.
