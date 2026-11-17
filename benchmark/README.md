# Benchmarks and checks

What is measured, and how to run it. Each needs the server set up as in `CONTRIBUTING.md`.

| Folder | What it is | Needs a real model |
| --- | --- | --- |
| [`memory/`](memory) | The memory check: the gate for a change to extraction, reconciliation or recall in coding mode. `check.py` runs scripted team conversations through a real server and checks what is kept and what recall says; `--agent-e2e` adds real Claude Code sessions with and without memory. | Yes |
| [`knowledge/`](knowledge) | The cases behind the keep-or-drop rules: what must become a memory and what must not. | Used by the memory check |
| [`beam/`](beam) | The [BEAM](https://arxiv.org/abs/2510.27246) long-conversation benchmark for conversation mode, against plain search and full context on the same harness. See its README. | Yes |
| [`smoke/`](smoke) | `hosted.py` checks a deployed server and console end to end in a throwaway project. | Optional (`--no-synthesis`) |
| [`sources/`](sources), [`capture/`](capture) | Tools that turn pull requests and Claude Code transcripts into conversations to try memory on. | No |

```bash
cd server && .venv/bin/python ../benchmark/memory/check.py              # about 5 minutes
cd server && .venv/bin/python ../benchmark/memory/check.py --agent-e2e  # about 30 minutes
```

Results are quoted for Claude models. What has and has not been measured is in
[docs/what-memvine-does.md](../docs/what-memvine-does.md).
