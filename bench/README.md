# Benchmarks

Does editing through vim keystrokes (neovain) beat exact string replacement (Claude Code's
Edit tool) for AI agents? This harness measures it with real, headless Claude Code sessions.

## Method

Each run is a fresh `claude -p` session in its own directory with a copy of
[`fixture.py`](tasks/small/fixture.py) (70 lines) and [`TASKS.md`](tasks/small/TASKS.md). The session has to apply seven
edits: a rename across every use, a new parameter, deleting a function, introducing a constant,
wrapping a function body in try/except, deleting debug lines, and swapping two functions.
[`check.py`](tasks/small/check.py) then verifies the result by parsing it and checking every edit and the
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
python3 run.py --task large --models opus,sonnet --arms edit,neovain --reps 3
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
replacement text is short, so writing it out costs little. The large task set below tests
the opposite case. Three reps per cell is enough to see large effects, not small ones. Both
models have been trained heavily on string-replacement editing and not at all on neovain.

## Results: large structural edits (v2, September 2026)

[`tasks/large`](tasks/large) is a 2,350-line module generated by [`gen.py`](tasks/large/gen.py)
(deterministic, so anyone can reproduce it). There are six changes: delete a 398-line class,
move a 401-line class to the end, swap two 300+ line classes, wrap a 122-line method body in
`with self._lock:`, rename a method used 191 times, and delete three `debug_` functions. The
[checker](tasks/large/check.py) derives the expected file from the fixture's syntax tree and
requires an exact match, blank lines included. Claude Opus 5.5 and Sonnet 5.5, 3 runs per cell,
no preloaded context. Raw data: [`results-large.jsonl`](results-large.jsonl).

| Model  | Arm     | Pass | Output tokens | API requests | Edit calls | Wall time | Cost  |
|--------|---------|------|---------------|--------------|------------|-----------|-------|
| Opus   | edit    | 3/3  | 28,903        | 20.3         | 10.0       | 227s      | $1.33 |
| Opus   | neovain | 3/3  | 3,162         | 9.0          | 2.0        | 43s       | $0.32 |
| Sonnet | edit    | 3/3  | 31,339        | 14.7         | 8.7        | 159s      | $0.74 |
| Sonnet | neovain | 2/3  | 3,530         | 7.3          | 1.3        | 38s       | $0.17 |

### Findings

1. **neovain used 9× fewer output tokens, finished 4.7× faster and cost 4.3× less** (means of
   the Opus and Sonnet ratios). String replacement has to spell out every line it removes and
   every line it adds, so moving a 400-line class means writing it twice. neovain moves it with
   a range and `:m`.
2. **This time the round trips drop too.** These edits depend on each other, so the Edit arm
   can't send them in parallel. It needed 15–20 model requests; neovain needed 7–9.
3. **One neovain run in six failed; no Edit run did.** In Sonnet's failed run, the range for
   the `sync_all` indent ended at the end of the class, not the method, so every later method
   got indented too. The result was valid Python and valid vim, so nothing aborted, and the
   agent had piped neovain's output through `grep -v` rather than read the 2,000-line diff.
   Opus agents also discarded the diff (`>/dev/null`) and checked the result another way.
   Diffs this large don't get read, which points at a needed feature: a compact structural
   summary of what changed.
4. **Everything went into one or two calls.** The [demo on the site](demo-large.json) shows one
   pair of runs: the neovain agent sent 760 characters across a dry run and the real call.
   The Edit agent sent 65,379 characters across 8 calls.
