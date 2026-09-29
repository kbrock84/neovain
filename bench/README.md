# Benchmarks

Does editing through vim keystrokes (neovain) beat exact string replacement (Claude Code's
Edit tool) for AI agents? This harness measures it with real, headless Claude Code sessions.

## Method

Each run is a fresh `claude -p` session in its own directory with a copy of
[`fixture.py`](fixture.py) (70 lines) and [`TASKS.md`](TASKS.md). The session has to apply seven
edits: a rename across every use, a new parameter, deleting a function, introducing a constant,
wrapping a function body in try/except, deleting debug lines, and swapping two functions.
[`check.py`](check.py) then verifies the result by parsing it and checking every edit and the
blank-line layout.

| Arm          | How the agent may change the file                                      |
|--------------|------------------------------------------------------------------------|
| `edit`       | Claude Code's Edit tool only (exact string replacement)                 |
| `neovain`    | the neovain CLI only, via Bash                                          |
| `neovain-ex` | neovain with `NEOVAIN_EX_ONLY=1`: anchors and ex commands, no normal-mode keys |

Tool permissions are enforced with `--allowedTools` / `--disallowedTools`. Bash commands that
write the file some other way are counted as violations. `--context-kb` preloads real source
code (Neovim's Lua runtime) into the prompt to simulate a long session: 250 KB is about 80k
tokens, so every request carries roughly 160k tokens including Claude Code's own prompt.

Token counts, cost and model time come from Claude Code's `stream-json` result event. **API
requests** counts distinct model responses, which is the number of real round trips. It is not
the same as tool calls, because models issue several tool calls in parallel from one response.

```
python3 run.py --models opus,sonnet --arms edit,neovain,neovain-ex --context-kb 0,250 --reps 3
python3 run.py --summarize-only
```

## Results (v1, September 2026)

Claude Opus 5.5 and Sonnet 5.5, 3 runs per cell, 36 runs total, all passing. The numbers are
means. Raw data: [`results-v1.jsonl`](results-v1.jsonl).

| Model  | Context | Arm        | Pass | API requests | Tool calls | Output tokens | …thinking | Cost   | Wall time |
|--------|---------|------------|------|--------------|------------|---------------|-----------|--------|-----------|
| Opus   | small   | edit       | 3/3  | 4.3          | 8.0        | 1,797         | 21        | $0.18  | 18s       |
| Opus   | small   | neovain    | 3/3  | 4.0          | 4.0        | 1,789         | 1,039     | $0.21  | 24s       |
| Opus   | small   | neovain-ex | 3/3  | 3.7          | 4.0        | 1,934         | 1,013     | $0.21  | 24s       |
| Opus   | 250 KB  | edit       | 3/3  | 4.7          | 9.0        | 1,787         | 22        | $1.47  | 26s       |
| Opus   | 250 KB  | neovain    | 3/3  | 4.7          | 4.7        | 1,569         | 870       | $1.50  | 24s       |
| Opus   | 250 KB  | neovain-ex | 3/3  | 3.7          | 2.7        | 1,807         | 1,233     | $1.46  | 25s       |
| Sonnet | small   | edit       | 3/3  | 4.0          | 10.7       | 2,023         | 63        | $0.10  | 16s       |
| Sonnet | small   | neovain    | 3/3  | 4.0          | 4.7        | 2,006         | 1,312     | $0.11  | 23s       |
| Sonnet | small   | neovain-ex | 3/3  | 4.3          | 4.7        | 2,089         | 1,279     | $0.12  | 24s       |
| Sonnet | 250 KB  | edit       | 3/3  | 3.3          | 10.0       | 1,961         | 74        | $0.75  | 18s       |
| Sonnet | 250 KB  | neovain    | 3/3  | 5.3          | 5.3        | 2,516         | 1,660     | $0.85  | 31s       |
| Sonnet | 250 KB  | neovain-ex | 3/3  | 3.7          | 4.3        | 2,323         | 1,471     | $0.78  | 29s       |

### Findings

1. **Correctness was the same.** Every run in every arm passed the checker. neovain's
   transactional aborts were used and recovered from: 0.3–0.7 aborted calls per run, and no
   run ended with a wrong file.
2. **neovain halves tool calls, but not round trips.** The Edit arm sends its 8–11 edits as
   parallel tool calls, so every arm needs about 4 model requests. On this task, fewer tool
   calls does not mean fewer turns.
3. **The work moves into thinking.** neovain runs spend about 1,000 extra thinking tokens
   working out line offsets, blank-line counts and cursor positions. Edit-tool runs barely
   think, because writing the replacement text *is* the reasoning. Total output tokens come
   out about equal.
4. **Cost is the same; neovain is a little slower.** With prompt caching, a large context
   costs one cache write plus cheap re-reads, so turn count has little effect on cost.
   neovain is equal or slower in wall time, because thinking runs before any tool call.
5. **Ex-only mode is not better.** It uses slightly fewer tool calls, with similar thinking
   and cost.
6. **Smaller models struggle more.** In an earlier pilot (v0, Python prototype, Haiku 4.5), the
   neovain arm failed 1 of 3 runs and used about 2.7× the tokens and 3× the time of the Edit
   arm. See [`results-v0-python.jsonl`](results-v0-python.jsonl).

### Limitations

These tasks are small edits on a small file. That is the Edit tool's best case: the
replacement text is short, so writing it out costs little. The next task set tests large
structural edits (moving, deleting and re-indenting big blocks). With string replacement,
output grows with the size of the block; with neovain it doesn't (`:m`, `:d`, `>`). Three reps
per cell is enough to see large effects, not small ones. Both models have been trained
heavily on string-replacement editing and not at all on neovain.
