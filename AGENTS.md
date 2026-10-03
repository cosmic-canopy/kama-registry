# kama-registry — guide for AI assistants

This repository IS the official kama package registry: `registry/` is deployed verbatim to
https://registry.kama-lang.org. Read `README.md` first.

## Rules that are not negotiable

- **Never edit, rename or delete a package's files under `registry/` by hand.** Every change there is a
  release to the world. The only way in is `./ops publish <kama.json>`, which runs `kama publish` and the
  checks — including `catalog.json`, which it rebuilds. The host's own files — `404.html`, `_redirects` — are
  edited by hand and reviewed. The pages people read are NOT here: the deploy writes them beside a copy of
  `registry/` with the kama repository's `tools/site/registry.mjs` (pinned as `KAMA_SITE_REV` in `deploy.yml`).
  To change a page, change that file in the kama repository, then bump the pin; `./ops preview` shows the result.
- **Tarballs never go in git or in `registry/`.** They live in the R2 bucket `kama-registry-tarballs`
  (`dl.kama-lang.org`), and `./ops publish` uploads each one only after every check has passed. The bucket
  is LOCKED — nothing in it can be overwritten or deleted, ever — so **never upload a probe or test object
  to it**: it would be permanent, and a key once taken spends that version number. `.gitignore` hides every
  `registry/**/*.tar.gz` from `git add`, and `ops publish` refuses to run while one is lying in the tree —
  it uploads exactly the tarball it just built, by name, and commits only `registry/`.
- **`_redirects` is load-bearing and ordered.** Its first rule keeps every `index.json` on Pages; its second
  sends a package page's explicit `…/index.html` to the page; its third sends everything else below a package
  to the bucket. A redirect beats a static file, so dropping or reordering the first rule redirects every index
  (measured). The page at `/@kama/<pkg>/` needs no rule — a placeholder matches no empty segment (measured with
  `./ops preview`). `tools/check.py` holds all three lines down.
- **A published version is permanent.** Do not "fix" one — bump the package's version and publish again.
  `tools/check.py` refuses a changed or removed version, and CI will not deploy past it.
- **`@kama/*` only.** This is the curated official registry. `@std` is reserved forever.
- **Do not add a `_headers` file** without measuring. Cloudflare Pages merges every matching rule, so a
  rule meant to make tarballs immutable also matches `index.json` — and an index cached forever hides
  every later publish.
- **Keep `registry/404.html`.** Without it Pages answers unknown paths with `index.html` and status 200.
- **Uploading is permanent; pushing to `main` deploys the index.** Commit locally; the maintainer pushes.

## Verify, don't assume

`./ops check` before committing, `./ops verify` after a deploy. The infrastructure (Pages project, domain,
DNS) is declared in the kama repository's `provisioning/pages/main.tf`, not here.
