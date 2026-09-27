# kama-registry

The official package registry for [kama](https://kama-lang.org), served at
**https://registry.kama-lang.org**. It holds the `@kama/*` packages — the ones maintained alongside the
compiler.

There is no registry *service*. A kama registry is a static file tree, and this repository is that tree:

```
registry/
  @kama/sodium/index.json        the published versions, each pinned by the sha256 of its tarball
  @kama/sodium/0.4.1.tar.gz      the sources of one version
  index.html, 404.html           the host's own pages
```

The toolchain fetches `<base>/<name>/index.json`, picks the highest version satisfying the range, fetches
that tarball and verifies its hash. The protocol is documented in
[kama-lang.org/docs/packages](https://kama-lang.org/docs/packages/) (§ *The registry is a static file tree*).
Clients never clone this repository — git is the publisher's source of truth and the public record of
every release, and the host serves plain files over HTTPS.

## Using it

```json
{
  "registries": { "@kama": "https://registry.kama-lang.org" },
  "dependencies": { "@kama/sodium": { "version": "^0.4.0" } }
}
```

A future compiler release will make this registry the built-in default, and the `registries` line becomes
unnecessary.

## Publishing

```sh
cp .env.example .env                           # only needed for ./ops deploy
./ops publish ../kama-sodium/kama.json         # publish into registry/, check, commit
git push                                       # CI checks, then deploys
```

`./ops publish` runs `kama publish --registry file://…/registry`, runs `tools/check.py`, and commits
`publish <name>@<version>`. Pushing is the release: the deploy workflow checks the tree again and deploys
`registry/` to Cloudflare Pages. After a deploy, `./ops verify` confirms the live host serves exactly the
committed indexes, and that a missing package gets a real 404.

## The rules — enforced, not just documented

`tools/check.py` runs before every deploy, locally and in CI:

- **Integrity** — every tarball exists and hashes to the sha256 its index records.
- **Write-once** — against the previous tip, no published version is removed and no version's entry or
  tarball bytes change. A consumer's lockfile pins the hash; changing it breaks every one of them. To fix
  a release, publish a new version.
- **Scope** — this is the official registry: `@kama/*` only. `@std` is reserved forever — the standard
  library ships inside the compiler and is never a package.
- **Secrets** — no tarball may contain a secret-shaped file: `.env` and `.env.*`, private keys (`*.pem`,
  `*.key`, `id_rsa` …), `.netrc`/`.npmrc`/`.pypirc`, `kama.local.json`. A template such as `.env.example`
  is fine. Before 0.9.452, `kama publish` tarred the project directory without reading `.gitignore`; from
  0.9.452 it ships only committed, git-tracked files and refuses these names itself. The host cannot know
  which compiler a publisher ran, so this is still the line that stops a project's `.env` becoming a
  permanent public file.
- **Hygiene** — no file ships that no index names, and `404.html` exists. Without it, Cloudflare Pages
  treats the site as a single-page app and answers every unknown path with `index.html` and status 200.

`kama publish` already refuses to overwrite a version, but that is one client on one machine; git accepts
commits from anywhere. The host enforces the same rules itself.

## Infrastructure

The Pages project, the `registry.kama-lang.org` custom domain and its DNS record are declared in the kama
repository's [`provisioning/pages/main.tf`](https://github.com/cosmic-canopy/kama/blob/main/provisioning/pages/main.tf),
beside the site's — every record in the `kama-lang.org` zone lives in one OpenTofu state. The deploy
workflow needs `CLOUDFLARE_API_TOKEN` and `CLOUDFLARE_ACCOUNT_ID` as GitHub Actions secrets (repository or
organization level), the same values as the site's.

`packages.kama-lang.org` is reserved for a human-facing browse and search site. This host is the machine
endpoint — its URL is compiled into kama binaries, so it must never move.

## License

The tooling in this repository is MIT (see `LICENSE`). Each published package carries its own license
inside its tarball.
