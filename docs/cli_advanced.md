# CLI Advanced Usage (v0.9-B)

## stdin / file input

### Ask from file

```bash
financial-analyst ask -f question.txt
```

### Ask from stdin (pipe)

```bash
echo "PE of SH600519" | financial-analyst ask
cat question.txt | financial-analyst ask
```

### Batch reports from file

```bash
# codes.txt: one stock code per line, # for comments
cat > codes.txt <<EOF
SH600519
SZ000858
# skip this comment
SH601318
EOF

financial-analyst report -f codes.txt
```

Runs deep-dive sequentially on each. Ctrl+C between any cancels the loop.

## Cancellation (Ctrl+C)

Long-running reports can be cancelled with Ctrl+C:

```
> /report SH600519
[Running stock-deep-dive for SH600519...]
[wave: tier 2 analysts running]
<Ctrl+C>
Cancelled by user.
```

Sub-agents already completed are NOT lost — but the run won't continue.

## Trace mode (--trace)

See per-agent timing + output-size breakdown:

```bash
financial-analyst report SH600519 --trace
```

Output after run completes:

```
Trace · SH600519 · 2026-05-15
┃ Agent              ┃ Status ┃ Elapsed ┃ Output bytes ┃
│ quote-fetcher      │   ✓    │  1.2s   │    482       │
│ factor-computer    │   ✓    │  3.1s   │   1024       │
│ ...                                                  │
│ report-writer      │   ✓    │ 12.4s   │   8201       │
│ TOTAL                       │ 78.3s   │              │
```

Use this to find slow sub-agents (often a sign of LLM response timeouts) or oversized outputs (token cost driver).
