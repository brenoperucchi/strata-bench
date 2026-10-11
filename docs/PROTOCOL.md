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
