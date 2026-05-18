# Sessions (v0.9-A)

TUI multi-session support. Each session has its own persistent history of asks / reports / commands. Use sessions to keep different research contexts separate (e.g. "tech-stocks" vs "consumer-stocks" vs "crypto-watch").

Storage: `~/.financial-analyst/sessions/<name>/{meta.json, log.jsonl}`.

## Commands

```
> /sessions               — list all sessions + which is active
> /sessions new <name>    — create + switch
> /sessions switch <name> — switch active session
> /sessions show [name]   — view history of session (default: active)
> /sessions delete <name> — delete (cannot delete 'default')
```

## Use cases

1. **Topic separation** — `tech-stocks`, `policy-driven`, `arbitrage-watch`
2. **Project tracking** — `2026-Q2-research`, `IPO-pipeline`
3. **Shared usage** — different team members each have their own session
4. **Auditing** — `/sessions show` shows what you've been doing in a project

## What gets logged

Every command in the TUI (ask, report, slash command, chat) writes one line to `log.jsonl`:
```json
{"ts": "2026-05-18T14:30:00", "kind": "ask", "input": "PE of 600519",
 "output_summary": "", "duration_s": 12.3, "refs": []}
```

This is local + private — never sent anywhere.
