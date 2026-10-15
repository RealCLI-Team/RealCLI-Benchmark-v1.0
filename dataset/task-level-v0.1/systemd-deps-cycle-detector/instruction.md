My on-call shift is about to end. I just exported the startup dependencies of the systemd units from a host into `inputs/deps.txt`. It looks like a few services are looping — troubleshooting when the machine won't boot is painful, so I want to find the cycles quickly, and also draw a DOT graph for my colleague to look at in the morning.

Here's what the data looks like on site:

```
multi-user.target: After=basic.target Requires=basic.target
network.service: After=network-pre.service Requires=dbus.service
app-c.service: After=app-a.service
local-fs.target:
```

One unit per line like this; after the colon there may be `After=` and `Requires=`, or neither (like the `local-fs.target` line); field values are comma-separated. Just take After and Requires together as this unit's dependency predecessors.

Please produce two files for me:

- `outputs/deps.dot`: a valid digraph whose nodes are **all** units that appear in deps.txt (including those that only show up in someone else's dependencies), with edge direction `dep -> unit`. Don't worry about aesthetics — as long as dot can parse it.
- `outputs/cycles.json`: a top-level object where `cycles` is an array. If there are no cycles, give `[]`; if there are cycles, write each cycle as a member list, rotated so the lexicographically smallest member is the starting point, with the order following the edges.

The specific format details are in `inputs/docs/format.md` — just follow that spec.

This offline terminal has no network; don't install extra packages either — the standard library is enough. Don't touch anything under `inputs/`.
