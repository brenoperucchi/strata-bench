# Deploying the upload Worker

The Worker receives a `result.json` from the upload page, validates it with the same rules as CI, checks the Turnstile challenge and opens a pull request. A maintainer still reviews and merges every PR; CI re-validates with the Python validator.

## One-time setup

1. **Turnstile widget** (Cloudflare dashboard, Turnstile): hostname `brenoperucchi.github.io`, mode Managed. The public site key goes in `docs/config.js` (`turnstileSiteKey`). The secret key is only used in step 4.
2. **Cloudflare login**: `cd worker && npx wrangler login`.
3. **GitHub token**: fine-grained personal access token, repository access limited to `brenoperucchi/strata-bench`, permissions Contents and Pull requests set to read and write. Nothing else.
4. **Secrets** (you type or paste the value at the prompt; it is never stored in the repo):

   ```
   npx wrangler secret put GITHUB_TOKEN
   npx wrangler secret put TURNSTILE_SECRET
   ```

5. **Deploy**: `npx wrangler deploy`. It prints the Worker URL (`https://strata-bench-submit.<account>.workers.dev`).
6. Put that URL in `docs/config.js` (`workerUrl`), commit and push. GitHub Pages republishes the page with the one-click upload enabled.

## Checks

- `npm test` in `worker/` runs the Worker tests (Node 22 or newer).
- `GET <workerUrl>/` answers with a short status line.
- Upload a valid result on the page: a branch `submit/<date>-<label>-<hash>` and a PR appear in the repository.

## Rotating or revoking

- Token: create a new one on GitHub, run `npx wrangler secret put GITHUB_TOKEN` again, revoke the old one.
- To stop accepting uploads, set `workerUrl` back to `""` in `docs/config.js` (the page falls back to PR and issue instructions) or delete the Worker.
