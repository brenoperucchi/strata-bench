# Protocol standard-v1

What the runner sends and records, so results can be compared and challenged.

## Prompts

Fixed public text in `strata_bench/prompts/` (excerpts of the Strata source, MIT). Their SHA-256 hashes are pinned in the code and written into every result; a result whose hashes differ is rejected by the validator. A checkout that changed line endings produces a different protocol and the runner refuses to start.

## Sampling and calls

Temperature 0, seed 42, a 256-token cap, and `cache_prompt` off for the size prompts. For each size there is 1 warm-up call and 3 measured runs. Each call starts with a constant first line per (size, run), so the server cannot answer from a previous call's cache by accident. A run that reuses more than a few prompt tokens is flagged as a warning.

## Measurements

- Sizes `short`, `medium`, `long`, `xlong`: prompt speed and decode speed in tokens per second, from the engine's own `timings`, median, minimum and maximum of the measured runs.
- `probe`: a fixed system prompt and one public code line per call, 1-token answers, 30 calls with the first 2 skipped. It measures the cached short-request path.
- `gprobe` (opt-in): agent-shaped requests of about 150, 300 and 450 tokens, 30 calls per size, a different excerpt each time, 1 token out.

## Configuration identity

`config.args` and `config.env` are stored with paths replaced by the role of the flag that carries them and secrets redacted. The hash of that scrubbed block is `config_id`. Two results with the same `config_id` ran the same server arguments. `config_origin` says whether the config is what the setup wrote (`setup-default`) or yours (`custom-N`).

## Health

A call that fails because the server failed is recorded as a `failed` result: the kind is one of `crash`, `oom`, `garbage`, `hang`, `server_error`, and the place is the call name (for example `medium run 2`). Degenerate output means at least 40 words of which under 5% are distinct, or a long text with no letter or digit.

## What this does not prove

The hardware description is declared by the submitter. The runner checks the model file name and context size against the server, not the file contents. One result is one machine on one day. Different engine versions, configs or prompt sets are different measurements.

## standard-v2 (in progress: first token and the agent block)

`standard-v2` is additive. A result is written in schema `strata-bench/v2` and keeps every field of v1; the validator accepts both, and a v1 result must not carry the new fields. Run it with `--protocol standard-v2`.

- **What changes:** the four text sizes are requested as a stream (SSE) and thinking is fixed off (`reasoning_effort` none, `enable_thinking` false), so the first piece of text is the answer and not the start of reasoning. The texts and their hashes are the v1 ones. The probes and `gprobe` stay non-streamed one-token calls.
- **Time to first token (`ttft_ms`):** from just before the request is sent to the first non-empty piece of `content` or `reasoning_content`. Keep-alives and empty deltas do not count. It includes the network, so a v2 result declares `runner.host_relation` (`same-host`, `lan`, `remote`). `--host-relation` sets it; a loopback `--base` defaults to `same-host`, anything else must say.
- **Per request:** `runs[].stream` has `ttft_ms`, `total_ms`, `pieces` and `gap_ms` (p50, p95 and max of the time between pieces, null with fewer than two pieces). Per size: `sizes.<size>.ttft_ms` with median, min and max.
- **Speeds are still the engine's own** `timings`, read from the last chunk of the stream. A server that sends none gives an `incomplete` result (`no_stream_timings`), not a guess.
- **Failures:** a connection that closes before the stream finishes is a `crash`; an error event in the stream is `oom` or `server_error`. The server's own words are never kept. `failure.where` names are the same as in v1.
- `protocol.suite` is `custom` for now (the sizes you picked with `--sizes`) and `protocol.blocks` is `["first-token"]`. The names `quick`, `standard`, `full` and the blocks `chat`, `agent`, `clients`, `decode-curve` are reserved for the next phases and are not produced yet.

### The agent block (`--sizes ...,agent`)

One conversation of five turns, repeated in one warm-up session and three measured ones. It measures what a coding agent does: a fixed system text and four tool definitions, a history that grows each turn, and a prompt cache that should keep every turn after the first from reading the past again.

- **Fixed texts, hashed:** `agent_system.txt`, `agent_tools.json` (four tools: `read_file`, `search`, `edit_file`, `run_command`), `agent_turns.json` (the turn template and five questions) and `agent_doc_1.txt` to `agent_doc_5.txt` (fixed slices of the public `xlong.txt`, 7,000 characters at most each). They are written for this project; `tools/make_agent_prompts.py --check` rebuilds them from `xlong.txt`.
- **Each turn** adds one document and one question as a new user message. The server's own answer (temperature 0, seed 42, at most 256 tokens) goes back into the history as the assistant message, so token counts differ between models and are recorded, not fixed. Requests are streamed, `cache_prompt` is true, and the tools are offered with `tool_choice: none` and never executed.
- **Sessions are separate:** the first line of the system text is `[strata-bench agent <warmup|run> <i>]`, so one session never reuses another's cache.
- **Per turn** (`runs[]` with `size: agent` and `turn: 1..5`): tokens read (`prompt_n`), tokens from the cache (`cache_n`), tokens generated, time to first token and the engine's own speeds. **Per session** (`agent.sessions[]`): `hit_pct`, `reread_tokens` and `new_tokens`. Before turn t the server has seen the previous turn plus its answer (`prior` tokens); `cache_n` of them should come back from the cache, so `hit_pct` is cache tokens over prior tokens across turns 2 to 5 (capped at 100) and `reread_tokens` is what was read again. **Per turn over the three sessions:** `agent.turns.<t>` with the median, min and max of each.
- **Warnings:** a session with under 50% of its past coming from the cache adds a warning (status `warnings`). It is slow, not invalid.
- **Skipped, not failed:** a server context under 16,384 tokens cannot hold the conversation, so the block is recorded in `protocol.skipped` with the reason `context_too_small` and no `agent` data. If nothing else was asked for, the run refuses (exit 2). The closed reasons are `context_too_small`, `no_parallel_slots` and `no_stream_timings`.
- **Failures:** the same kinds as everywhere, with `failure.where` such as `agent run 2 turn 3`.
- `protocol.blocks` lists `agent` when it was asked for, and `protocol.agent` repeats the fixed settings (5 turns, 3 sessions, 1 warm-up, 256 tokens, cache on, `tool_choice` none); the validator rejects any other values.
