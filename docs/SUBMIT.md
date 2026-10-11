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
- Add `--sizes probe,medium,long,xlong,gprobe` to include the short agent-style prompts (about 150, 300 and 450 tokens).

The result is saved after every call. Exit codes: `0` done, `2` refused before measuring (wrong model or context, a modified prompt file, an existing result), `3` stopped early with a partial result kept.

## 5. Check it

```
python3 -m strata_bench validate results/*.result.json
```

The validator is the same gate the dashboard uses. It rejects anything outside the public schema and any value that looks like a local path, a private network address or a secret. Read the file once before you share it.

## 6. Submit

Fork the repository, add your `*.result.json` under `results/`, and open a pull request. Only the result file is needed. After the merge the dashboard data is rebuilt automatically.

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
