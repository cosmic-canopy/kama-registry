# kama-registry

The official package registry for [kama](https://kama-lang.org), served at
**https://registry.kama-lang.org**. It holds the `@kama/*` packages — the ones maintained alongside the
compiler.

There is no registry *service*. A kama registry is a static file tree: this repository holds its
**indexes**, deployed to Cloudflare Pages, and its **tarballs** live in an R2 bucket behind
`dl.kama-lang.org`:

```
registry/                        (this repository → Cloudflare Pages)
  @kama/sodium/index.json        the published versions, each pinned by the sha256 of its tarball
  index.html, 404.html           the host's own pages
  _redirects                     sends every tarball path to the bucket; keeps index.json here
bucket kama-registry-tarballs    (R2 → dl.kama-lang.org, locked: nothing in it can change or go)
  @kama/sodium/0.5.0.tar.gz      the sources of one version
```

The toolchain fetches `<base>/<name>/index.json`, picks the highest version satisfying the range, fetches
that tarball — `registry.kama-lang.org/@kama/sodium/0.5.0.tar.gz`, which redirects to the bucket — and
verifies its hash. A lockfile records the registry URL, never the bucket's, so the bucket's hostname can
change with one line of `_redirects`.

**Why the tarballs are not in git.** A published version is permanent, so a repository holding tarballs
grows by every byte ever published and every clone downloads all of it; Cloudflare Pages also refuses any
file over 25 MiB. R2 serves them with free egress, and its bucket lock makes write-once a property of the
storage itself: a published tarball cannot be overwritten or deleted by `ops`, a leaked token, or hand.
(The first version, `@kama/sodium@0.5.0`, was published into git and moved to the bucket the next day; git
still has it in its history.) The protocol is documented in
[kama-lang.org/docs/packages](https://kama-lang.org/docs/packages/) (§ *The registry is a static file tree*).
Clients never clone this repository — git is the publisher's source of truth and the public record of
every release, and the host serves plain files over HTTPS.

## Using it

```json
{
  "dependencies": { "@kama/sodium": { "version": "^0.5.0" } }
}
```

Since kama 0.9.457 this registry is the built-in default, so nothing else is needed — or run
`kama pkg add kama.json @kama/sodium --version ^0.5.0`. An older compiler names it with
`"registries": { "@kama": "https://registry.kama-lang.org" }`.

## Publishing

```sh
cp .env.example .env                           # the upload needs a Cloudflare token with R2 write
./ops publish ../kama-sodium/kama.json         # check, upload the tarball (PERMANENT), commit the index
git push                                       # CI checks, then deploys the index
```

`./ops publish` runs `kama check`, then `kama publish --registry file://…/registry`, and stages the tarball
out of the tree at once — `registry/` never holds one. It runs `tools/check.py` against the staged bytes,
and only when every check passes uploads the tarball to the bucket and reads it back to prove the bytes.
**That upload is the point of no return:** the bucket's lock keeps it forever, so the version number is
spent even if the index is never pushed. (Read `kama publish kama.json --dry-run` in the package first.) A
re-run after a failed commit is safe — the archive is deterministic, and identical bytes already in the
bucket are accepted; different ones are refused. Then it commits `publish <name>@<version>`, which holds
only the index. Pushing releases that index: the deploy workflow checks the tree again and deploys
`registry/` to Pages. After a deploy, `./ops verify` confirms the live host serves exactly the committed
indexes, that every tarball — fetched through the redirect, as a client does — hashes to its index entry,
and that a missing package gets a real 404.

## The rules — enforced, not just documented

`tools/check.py` runs before every deploy, locally and in CI:

- **Integrity** — every version's tarball is in the bucket, and a new one is downloaded and must hash to
  the sha256 its index records.
- **Write-once** — against the previous tip, no published version is removed and no version's entry
  changes; the bucket's lock refuses any overwrite or delete of the bytes. A consumer's lockfile pins the
  hash; changing it breaks every one of them. To fix a release, publish a new version.
- **Scope** — this is the official registry: `@kama/*` only. `@std` is reserved forever — the standard
  library ships inside the compiler and is never a package.
- **Secrets** — no tarball may contain a secret-shaped file: `.env` and `.env.*`, private keys (`*.pem`,
  `*.key`, `id_rsa` …), `.netrc`/`.npmrc`/`.pypirc`, `kama.local.json`. A template such as `.env.example`
  is fine. Before 0.9.452, `kama publish` tarred the project directory without reading `.gitignore`; from
  0.9.452 it ships only committed, git-tracked files and refuses these names itself. The host cannot know
  which compiler a publisher ran, so this is still the line that stops a project's `.env` becoming a
  permanent public file.
- **Hygiene** — `registry/` holds indexes and the site's own pages and nothing else: no tarball, no stray
  file. `404.html` exists — without it, Cloudflare Pages treats the site as a single-page app and answers
  every unknown path with `index.html` and status 200. `_redirects` holds exactly its two rules, in order:
  a redirect beats a static file, so without the first, every `index.json` would be sent to the bucket too.

`kama publish` already refuses to overwrite a version, but that is one client on one machine; git accepts
commits from anywhere. The host enforces the same rules itself.

## Infrastructure

The Pages project, the `registry.kama-lang.org` custom domain and its DNS record, and the R2 bucket with
its lock and its `dl.kama-lang.org` domain are declared in the kama repository's
[`provisioning/pages/main.tf`](https://github.com/cosmic-canopy/kama/blob/main/provisioning/pages/main.tf),
beside the site's — every record in the `kama-lang.org` zone lives in one OpenTofu state. The deploy
workflow needs `CLOUDFLARE_API_TOKEN` and `CLOUDFLARE_ACCOUNT_ID` as GitHub Actions secrets (repository or
organization level), the same values as the site's; it reads the bucket only through its public domain.
`./ops publish` uploads with the token in `.env`, which needs R2 write as well as Pages.

`packages.kama-lang.org` is reserved for a human-facing browse and search site. This host is the machine
endpoint — its URL is compiled into kama binaries, so it must never move.

## License

The tooling in this repository is MIT (see `LICENSE`). Each published package carries its own license
inside its tarball.
