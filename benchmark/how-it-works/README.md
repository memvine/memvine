# The How it works example

[memvine.dev/how-it-works](https://memvine.dev/how-it-works) follows one lesson through a real run.
`run.py` makes that run again: real Claude Code sessions with the Memvine plugin, on a local server
and console, in a small sample repository (Northwind's billing API).

1. **Conversation.** You ask your agent to review `export.py`, then tell it the 90-second timeout is
   deliberate and why.
2. **Memory saved.** Memvine keeps that one lesson, quotes your words as its source, and ties it to
   `export.py`.
3. **Memory recalled.** A teammate's fresh agent, in its own copy of the repository, is asked to lower the
   timeout to 10 seconds.
4. **Memory rechecked.** A commit changes `export.py`; at the teammate's next session start the memory turns
   stale ("may be outdated").

```sh
export MEMVINE_DEMO_KEY=mv_mk_...          # the local server's operator key
python3 benchmark/how-it-works/run.py all  # about five minutes; writes to benchmark/how-it-works/out/
python3 benchmark/how-it-works/run.py publish   # copies the two screenshots over site/how-*.png
```

It needs a local server started with `MEMVINE_SIGNUP=open` and a real model, the console running
against it with the operator key, Claude Code and Google Chrome. `MEMVINE_DEMO_API` and
`MEMVINE_DEMO_CONSOLE` change the addresses (defaults `127.0.0.1:8419` and `127.0.0.1:3119`).
Running `setup` again deletes the previous run's project and starts over.

The model's words change from run to run, so the page quotes one run (17 Nov 2026) and marks where
replies are shortened. The outcome held on each of three runs: one memory kept, the teammate's agent
declines and gives the reason, and the memory turns stale after the commit. `shot.mjs` takes the
console screenshots; its usage is at the top of the file.

`readme-image.html` lays the same run out as one picture for the repository's README; render it
with headless Chrome (`--window-size=1600,800 --force-device-scale-factor=1.5 --screenshot`) and save
it as `docs/images/memvine-run.png`.

## The team example (the homepage's screens)

`team.py` makes the run behind the four screens on [memvine.dev](https://memvine.dev): two people,
each with a project only they can open, beside one team space. You teach your agent a deploy rule
and a preference of your own; a teammate teaches theirs why a timeout is long; each shares one
memory with the team. Real Claude Code sessions, the real plugin and a real model.

```sh
python3 benchmark/how-it-works/team.py all       # setup, you, teammate, share, shots
python3 benchmark/how-it-works/team.py publish   # copies the screenshots to site/home-*.png
```

It needs a server on an empty database, started with `MEMVINE_SIGNUP=open` and a real model, and
the console running against it **without** an operator key, so people sign in and a personal
project can be opened (defaults `127.0.0.1:8421` and `127.0.0.1:3121`). The page shows the run of
20 Nov 2026: your project kept one rule and one preference, the teammate's one gotcha, and the
team space holds the two that were shared.
