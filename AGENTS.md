# kama-registry — guide for AI assistants

This repository IS the official kama package registry: `registry/` is deployed verbatim to
https://registry.kama-lang.org. Read `README.md` first.

## Rules that are not negotiable

- **Never edit, rename or delete anything under `registry/` by hand.** Every change there is a release to
  the world. The only way in is `./ops publish <kama.json>`, which runs `kama publish` and the checks.
- **A published version is permanent.** Do not "fix" one — bump the package's version and publish again.
  `tools/check.py` refuses a changed or removed version, and CI will not deploy past it.
- **`@kama/*` only.** This is the curated official registry. `@std` is reserved forever.
- **Do not add a `_headers` file** without measuring. Cloudflare Pages merges every matching rule, so a
  rule meant to make tarballs immutable also matches `index.json` — and an index cached forever hides
  every later publish.
- **Keep `registry/404.html`.** Without it Pages answers unknown paths with `index.html` and status 200.
- **Pushing to `main` deploys.** Commit locally; the maintainer pushes.

## Verify, don't assume

`./ops check` before committing, `./ops verify` after a deploy. The infrastructure (Pages project, domain,
DNS) is declared in the kama repository's `provisioning/pages/main.tf`, not here.
