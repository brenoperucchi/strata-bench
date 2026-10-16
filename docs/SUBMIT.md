# Run the benchmark and submit a result

About ten minutes plus the measurement time (a few minutes on a fast GPU). You need Python 3.10 or newer and a running Strata server. The runner uses only the standard library.

## 1. Get the code

```
git clone https://github.com/brenoperucchi/strata-bench
cd strata-bench
```

## 2. Start your Strata server the way you normally do

The benchmark attaches to a server that is already running; it does not start or stop anything. Use the config you actually run. `--config` is the JSON run config of that server (the `strata-*.json` the setup wrote, or your own). The runner checks that the server really serves the model file and context size named in that config, and refuses to measure otherwise.

## 3. Describe your machine (optional but useful)

Copy `examples/identity.example.json`, edit it to match your hardware, and pass it with `--identity`. These facts are declared by you, so they are shown as declared. Keys outside the public schema are dropped with a warning.

Two optional names let the dashboard filter and group results. Leave them out and nobody is listed.

- `submission.by`: a public name to list the result under, for example your GitHub name (or `--by your-name` on the command line). The dashboard gets a "Submitted by" filter, and `https://brenoperucchi.github.io/strata-bench/?by=your-name` links to your results. It is declared, not verified: anyone could write someone else's name, so treat it as a label.
- `machine.machine_id`: any short name for your machine, to group runs of the same rig (before and after calibrating, for example). The dashboard gets a "Machine" filter.

Both must be short names: letters, digits, `.`, `_` and `-`, up to 39 characters. The validator refuses anything else, so an email, a path or a sentence cannot slip in.

## 4. Run

```
python3 -m strata_bench run \
  --base http://127.0.0.1:18299 \
  --config /path/to/strata-yourmodel.json \
  --origin setup-default \
  --label myrig-0142-default \
  --out results/ \
  --identity my-identity.json
```

- `--base` is the server's address. If it needs an API key, set `STRATA_API_KEY` or pass `--api-key`; the key is never written anywhere.
- `--origin` is `setup-default` when the config is exactly what the release's setup wrote, otherwise `custom-1`, `custom-2`, and so on.
- `--label` names this run. It becomes the file name, and an existing result is never overwritten.
- You do not pick what is measured. A `standard-v1` run always measures the four prompt sizes `probe`, `medium`, `long` and `xlong`, and a result with other sizes is rejected by `validate`, by CI and by the upload page: results are only comparable when everybody measured the same things.

**Start with this default run (`standard-v1`).** The dashboard puts two machines side by side only when they ran the same protocol, and v1 is the one everybody can run, so a v1 result is what lets your machine be compared with the others. If you also run v2 (next section), keep the v1 result: v2 is an addition, not a replacement.

### Protocol v2: first token, agent session and several clients (optional)

`standard-v2` measures more than speeds: the time to the first token, a five-turn agent conversation with the prompt cache on, and several clients at once. Run it in addition to v1, on the same server and config, so the two results describe the same setup. Your v1 results stay valid and v2 results sit beside them on the dashboard, but a v2 result is compared only with other v2 results.

```
python3 -m strata_bench run --protocol standard-v2 --base http://127.0.0.1:18299 --config /path/to/strata-yourmodel.json \
  --origin setup-default --label myrig-0142-default-v2 --out results/ --identity my-identity.json
```

- With no `--suite`, v2 runs the **standard** suite. `--suite quick` is a few minutes (chat, medium and long prompts); `--suite standard` adds the xlong prompt and the agent session; `--suite full` adds 2, 4 and 8 clients at once and the short agent-style `gprobe` prompts (about 150, 300 and 450 tokens). The run prints how many requests it will send before it starts. The standard suite can take 30 to 60 minutes on a slow GPU.
- The agent session needs a server context of at least 16,384 tokens; with less it is skipped and recorded as skipped, and the rest still runs. The clients block is skipped if the server says it has fewer than 2 slots.
- First-token times include the network. A loopback `--base` is recorded as `same-host`; if the client runs on another machine pass `--host-relation lan` or `--host-relation remote`.
- Thinking is switched off for v2 requests, so the first token is the start of the answer and not of the reasoning.
- v1 is still the default when you do not pass `--protocol`, and it is the one to run first. Everything about what v2 sends and records is in [PROTOCOL.md](PROTOCOL.md).

The result is saved after every call. Exit codes: `0` done, `2` refused before measuring (wrong model or context, a modified prompt file, an existing result), `3` stopped early with a partial result kept.

## 5. Check it

```
python3 -m strata_bench validate results/*.result.json
```

The validator is the same gate the dashboard uses. It rejects anything outside the public schema and any value that looks like a local path, a private network address or hostname, or a secret, and any string over 200 characters. It is a filter, not a proof that a free-text field is harmless: read the file once before you share it, and keep the label and the identity file to plain names.

## 6. Submit

Only the `*.result.json` file is needed. There are three ways to send it:

1. **Upload page** (no GitHub account needed): open [the upload page](https://brenoperucchi.github.io/strata-bench/submit.html), pick the file, and it is checked in your browser with the same rules as CI. Once the page shows the one-click button, sending opens a pull request for you.
2. **Pull request**: fork the repository, add the file under `results/`, and open a pull request. A template lists what to check.
3. **Issue**: open a "Submit a result" issue and attach the file, for people who cannot open a pull request.

In every case a maintainer reviews it, CI validates it again, and after the merge the dashboard data is rebuilt automatically.

## What the status means

| status | meaning | published |
|---|---|---|
| `ok` | every measurement completed and no warning was raised | performance view |
| `warnings` | completed, with something to read (for example a prompt reused from the cache) | performance view, with the warning count |
| `failed` | the server crashed, ran out of memory, returned degenerate output, hung, or answered with a 5xx error. The runner keeps what it measured and records the kind and the call it failed in | health view |
| `incomplete` | the run stopped for another reason (you pressed Ctrl+C, a client-side error, a missing field) | not published |

A `failed` result is welcome: the health view exists to show which configurations break and on which versions. The server's own error text is never copied into the file.

## Privacy

The result holds your declared hardware, the engine and model identity, the server arguments with every path replaced by its role (`<pack>`, `<native>`, ...), and the measured numbers. The API key, host names, addresses and paths are not recorded, and the validator refuses a file that contains one.
