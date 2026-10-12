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

## standard-v2 (in progress: first token, chat, agent and clients)

`standard-v2` is additive. A result is written in schema `strata-bench/v2` and keeps every field of v1; the validator accepts both, and a v1 result must not carry the new fields. Run it with `--protocol standard-v2`.

- **What changes:** the four text sizes are requested as a stream (SSE) and thinking is fixed off (`reasoning_effort` none, `enable_thinking` false), so the first piece of text is the answer and not the start of reasoning. The texts and their hashes are the v1 ones. The probes and `gprobe` stay non-streamed one-token calls.
- **Time to first token (`ttft_ms`):** from just before the request is sent to the first non-empty piece of `content` or `reasoning_content`. Keep-alives and empty deltas do not count. It includes the network, so a v2 result declares `runner.host_relation` (`same-host`, `lan`, `remote`). `--host-relation` sets it; a loopback `--base` defaults to `same-host`, anything else must say.
- **Per request:** `runs[].stream` has `ttft_ms`, `total_ms`, `pieces` and `gap_ms` (p50, p95 and max of the time between pieces, null with fewer than two pieces). Per size: `sizes.<size>.ttft_ms` with median, min and max.
- **Speeds are still the engine's own** `timings`, read from the last chunk of the stream. A server that sends none gives an `incomplete` result (`no_stream_timings`), not a guess.
- **Failures:** a connection that closes before the stream finishes is a `crash`; an error event in the stream is `oom` or `server_error`. The server's own words are never kept. `failure.where` names are the same as in v1.
- `protocol.suite` names the profile that was run (see Suites below) or `custom`, and `protocol.blocks` lists the blocks that were asked for, in the order `first-token`, `chat`, `agent`, `clients`. `decode-curve` is reserved: it is not accepted until a protocol for it exists.

### The agent block (`--sizes ...,agent`)

One conversation of five turns, repeated in one warm-up session and three measured ones. It measures what a coding agent does: a fixed system text and four tool definitions, a history that grows each turn, and a prompt cache that should keep every turn after the first from reading the past again.

- **Fixed texts, hashed:** `agent_system.txt`, `agent_tools.json` (four tools: `read_file`, `search`, `edit_file`, `run_command`), `agent_turns.json` (the turn template and five questions) and `agent_doc_1.txt` to `agent_doc_5.txt` (fixed slices of the public `xlong.txt`, 7,000 characters at most each). They are written for this project; `tools/make_agent_prompts.py --check` rebuilds them from `xlong.txt`.
- **Each turn** adds one document and one question as a new user message. The server's own answer (temperature 0, seed 42, at most 256 tokens) goes back into the history as the assistant message, so token counts differ between models and are recorded, not fixed. Requests are streamed, `cache_prompt` is true, and the tools are offered with `tool_choice: none` and never executed.
- **Sessions are separate:** the first line of the system text is `[strata-bench agent <warmup|run> <i>]`, so one session never reuses another's cache.
- **Per turn** (`runs[]` with `size: agent` and `turn: 1..5`): tokens read (`prompt_n`), tokens from the cache (`cache_n`), tokens generated, time to first token and the engine's own speeds. **Per session** (`agent.sessions[]`): `hit_pct`, `reread_tokens` and `new_tokens`. Before turn t the server has seen the previous turn plus its answer (`prior` tokens); `cache_n` of them should come back from the cache, so `hit_pct` is cache tokens over prior tokens across turns 2 to 5 (capped at 100) and `reread_tokens` is what was read again. **Per turn over the three sessions:** `agent.turns.<t>` with the median, min and max of each.
- **Warnings:** a session with under 50% of its past coming from the cache adds a warning (status `warnings`). It is slow, not invalid.
- **Skipped, not failed:** a server context under 16,384 tokens cannot hold the conversation, so the block is recorded in `protocol.skipped` with the reason `context_too_small` and no `agent` data. If nothing else was asked for, the run refuses (exit 2). The closed pairs are `agent` with `context_too_small` and `clients` with `no_parallel_slots`; the validator accepts no other block or reason.
- **Failures:** the same kinds as everywhere, with `failure.where` such as `agent run 2 turn 3`.
- `protocol.blocks` lists `agent` when it was asked for, and `protocol.agent` repeats the fixed settings (5 turns, 3 sessions, 1 warm-up, 256 tokens, cache on, `tool_choice` none); the validator rejects any other values.

### The chat block (`--sizes ...,chat`)

The fixed one-sentence prompt `short.txt`, streamed, with the answer capped at 100 tokens: 1 warm-up and 9 measured requests, each with its own first line (`[strata-bench chat run <i>]`) and `cache_prompt` false. It reports the first-token time and the decode speed of a short answer. Results go to `sizes.chat` (prompt and decode speed, prompt time, `ttft_ms`) and `runs[]` with `size: chat`. `protocol.chat` repeats the fixed settings and the validator rejects any other values.

### The clients block (`--sizes ...,clients`)

Several clients at once, as when a team or a few agents share one server. For N = 2, 4 and 8, N clients each send the public `medium.txt` (own first line `[strata-bench clients <N> <warmup|run> <round> <client>]`, `cache_prompt` false, streamed, at most 128 tokens). All N start together on a barrier. Each N gets 1 warm-up round and 2 measured rounds.

- **Per N** (`clients[]`): `requests` (measured requests sent), `errors`, `total_tps` (tokens all clients generated divided by the time from the common start to the last finish, median over the measured rounds; the common start is taken just before the barrier releases the clients, and a round in which nothing was generated counts as 0), and the p50 and p95 (nearest rank, over all measured requests of that N) of the first-token time and of the whole request's latency.
- **A refusal with 429 is counted, not failed:** a client the server turns away with HTTP 429 (too many requests) adds to `errors`, its `runs[]` entry carries `error_status` and no timings, and the result gets a warning, because that round served fewer than N clients. Any other failure of a request is a failure of the server: a closed connection, a stream that ends early, a timeout, degenerate output, any 5xx, and in particular 503, which Strata answers while its engine restarts or after it died. The run stops with status `failed` and `failure.where` such as `clients 4 round 2` or `clients 4 warmup 1`. When several clients fail in one round the failure recorded is the first of crash, oom, garbage, hang, server_error, whatever order they finished in. A 4xx other than 429 is the client's own mistake and leaves the result `incomplete`.
- **Every request turned away** for an N leaves that row's numbers empty (null) and adds a warning.
- **Skipped, not failed:** a server that reports `total_slots` under 2 in `/props` cannot serve clients at once, so the block is recorded in `protocol.skipped` with `no_parallel_slots`. A server that does not report slots is run anyway. More clients than slots simply queue, which is part of what the block measures.
- `protocol.clients` repeats the fixed settings (counts `2,4,8`, 2 rounds, 1 warm-up round, 128 tokens) and the validator rejects any other values.

### Suites (`--suite`)

`--protocol standard-v2` with no `--sizes` and no `--suite` runs the `standard` suite. A suite is its own fixed list of blocks, so `--suite` and `--sizes` cannot be combined (`--sizes` still gives a `custom` result, and may add `probe` and `gprobe`).

| Suite | Runs | Blocks listed |
|---|---|---|
| `quick` | chat, medium, long | `first-token`, `chat` |
| `standard` | chat, medium, long, xlong, agent | `first-token`, `chat`, `agent` |
| `full` | standard plus clients | `first-token`, `chat`, `agent`, `clients` |

A block the server cannot run is skipped with its closed reason and still listed in `protocol.blocks`; the suite keeps its name. The validator ties each suite name to exactly its list of blocks. Before anything is measured the run prints how many requests it will send, because how long that takes depends on the machine (the `standard` suite can take 30 to 60 minutes on a slow GPU). Ctrl+C keeps what was measured as an `incomplete` result.

### What the validator asks of a v2 result

- **Numbers are numbers.** Every measured value (timings, token counts, summaries, first-token and clients numbers, `http_status`) must be a finite number or null; a string or a boolean there is refused, so no text can ride in a metric.
- **A block listed in `protocol.blocks` must bring its content** unless it was skipped or the result is `failed`: `first-token` needs `ttft_ms` in at least one text size and the streamed runs; `chat` needs `sizes.chat` with `ttft_ms` and its 9 measured runs; `agent` needs its 3 sessions and 15 measured turns; `clients` needs the rows for 2, 4 and 8 clients, each with `requests` equal to N times 2. A result cannot claim a block it did not measure.
- **The server's own words stay out.** `finish_reason` is recorded as `stop`, `length`, `tool_calls`, `content_filter` or `function_call`, and anything else as `other`. The text of an error event is never kept.
- **Python and JavaScript agree.** Both validators read the same `docs/schema.json`, look only at a result's own keys (not names every object inherits), match patterns as ASCII, scan the same text for paths and secrets, and are tested against each other on hundreds of edited results.

### Notes on measuring

- **The stream reader** follows SSE framing: a blank line ends an event and an event may span several `data:` lines. A connection that ends inside an event counts as a crash unless the stream already finished with its closing `timings`; an event that is complete but not JSON, or a 200 answer that is not an event stream at all, leaves the result `incomplete`.
- **`cache_prompt`** is sent as documented, but a server may ignore it (Strata does). What keeps a request from reading another's cache is its own first line (`[strata-bench ...]`), which differs per request, per session and per client.
- **`reread_tokens` and `hit_pct`** compare each turn with the tokens the server had already seen. If the server's chat template renders an old answer differently from how it generated it (for example without an empty thinking block), that difference is counted as read again even when the cache worked. Compare it with the previous turn's `predicted_n` before blaming the cache.
- **Percentiles** are nearest rank over all measured requests of that N: with N = 2 and 2 rounds there are 4 samples, so p95 is the largest.
