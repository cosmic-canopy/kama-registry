#!/usr/bin/env python3
"""check.py [--base REF] [--write-catalog] — what this registry guarantees, enforced before anything deploys.

`kama publish` already refuses to overwrite a version. That is a promise made by ONE client, on ONE machine,
and this repo accepts commits from anywhere git does — so the host enforces the same rules itself:

  integrity   every version's tarball is in the bucket; a NEW one (not in --base) is downloaded and must hash
              to the sha256 its index records. Tarballs live in R2 behind dl.kama-lang.org, never in this
              tree: `ops publish` stages one outside registry/ and uploads it only after this check passes.
  write-once  against --base, no existing version (or package) is removed and no published version's entry
              changes — not its integrity, tarball, revision or dependencies. A lockfile pins the hash;
              changing it breaks every consumer. The bytes themselves are held by the bucket's lock, which
              refuses any overwrite or delete, forever.
  scope       this is the OFFICIAL registry: it serves `@kama/*` and nothing else, and never `@std/*`
  secrets     no tarball contains a secret-shaped file (.env, private keys, credential files). Before 0.9.452
              `kama publish` tarred the directory and ignored .gitignore, so a gitignored .env shipped;
              from 0.9.452 it ships only committed, git-tracked files and refuses these names itself. But
              this repo takes a tarball from any compiler, and a version here is permanent — it can never
              be withdrawn — so the host refuses them whatever the publisher ran.
  installable the tarball's own kama.json — the manifest a consumer's resolver reads — names no `path`
              dependency. A fetched package arrives without that directory, so no consumer could install the
              version. Before 0.9.472 `kama publish` let one through (and recorded it in the index as `{}`, as
              it does a git or url dependency, so the index cannot tell them apart); it refuses one now.
  catalog    registry/catalog.json — every package's highest version and what a registry shows about it, the
              file `kama pkg search` and the site's search read — says exactly what the indexes say. `kama publish`
              (0.9.523 on) rebuilds it on every publish; `--write-catalog` writes it from the indexes, in the same
              format, for a registry whose last publish predates it.
  hygiene    registry/ holds the indexes, the catalog and the host's own files and nothing else — no tarball, no
              stray file, and no index.html: the pages people read are generated at deploy time by the kama
              repository's tools/site/registry.mjs, so a hand-written one would be overwritten. 404.html exists:
              without it Cloudflare Pages serves index.html with status 200 for every unknown path. _redirects
              holds exactly its three rules, in order: a redirect beats a static file, so without the first every
              index.json would be sent to the bucket along with the tarballs, and without the second a page's
              explicit `…/index.html`.

REGISTRY_PENDING_DIR names where `ops publish` staged tarballs not yet uploaded (their bytes are read there);
REGISTRY_ARTIFACTS overrides the bucket's base URL, for a test against a local server.

With no --base (or an all-zero / unknown ref, as on a repo's first push) the history rules are skipped.
"""
import hashlib, io, json, os, re, subprocess, sys, tarfile, urllib.error, urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TREE = os.path.join(ROOT, 'registry')
PREFIX = 'registry/'
OFFICIAL, RESERVED = '@kama/', '@std/'
INTEGRITY = re.compile(r'^sha256-([0-9a-f]{64})$')
SITE_FILES = {'404.html', '_redirects'}
CATALOG = 'catalog.json'
ARTIFACT_HOST = 'https://dl.kama-lang.org/'
ARTIFACTS = os.environ.get('REGISTRY_ARTIFACTS', ARTIFACT_HOST)
PENDING = os.environ.get('REGISTRY_PENDING_DIR', '')
# Measured with `wrangler pages dev` (2026-09-27): with only the last rule, index.json answers 302. And (2026-10-02):
# a package page at `/@kama/<pkg>/` is served with no rule of its own — `:file` matches no empty segment — while its
# explicit `index.html` would reach the bucket without the second.
REDIRECTS = ['/@kama/:pkg/index.json /@kama/:pkg/index.json 200',
             '/@kama/:pkg/index.html /@kama/:pkg/ 301',
             f'/@kama/:pkg/:file {ARTIFACT_HOST}@kama/:pkg/:file 302']
# Secret-shaped basenames. A template (.env.example) is the documented way to ship the NAMES without values.
SECRET = re.compile(r'''^(\.env(\..+)?|.*\.(pem|key|p12|pfx|jks|keystore)|id_(rsa|dsa|ecdsa|ed25519)|\.netrc|\.npmrc|\.pypirc|kama\.local\.json)$''')
SECRET_OK = re.compile(r'^\.env\.(example|sample|template|dist)$')

def secrets_in(data):
    try:
        with tarfile.open(fileobj=io.BytesIO(data), mode='r:*') as t:
            return [m.name for m in t.getmembers()
                    if m.isfile() and SECRET.match(os.path.basename(m.name)) and not SECRET_OK.match(os.path.basename(m.name))]
    except tarfile.TarError:
        return None                                 # not a tarball at all — the caller says so

def path_deps_in(data):
    """The `path` dependencies of the tarball's root kama.json — the shallowest one, since `kama publish` roots the
    archive at one directory. A nested example or test project may depend on its own package by path: nobody
    installs those. None when there is no readable root manifest."""
    try:
        with tarfile.open(fileobj=io.BytesIO(data), mode='r:*') as t:
            manifests = [m for m in t.getmembers() if m.isfile() and os.path.basename(m.name) == 'kama.json']
            if not manifests: return None
            root = min(manifests, key=lambda m: len([p for p in m.name.split('/') if p not in ('', '.')]))
            deps = json.load(t.extractfile(root)).get('dependencies') or {}
            return sorted(name for name, d in deps.items() if isinstance(d, dict) and 'path' in d)
    except (tarfile.TarError, ValueError, AttributeError):
        return None

errors = []
def err(msg): errors.append(msg)

def git(*args):
    r = subprocess.run(['git', '-C', ROOT, *args], capture_output=True)
    return r.stdout if r.returncode == 0 else None

def indexes_on_disk():
    out = {}
    for d, _, files in os.walk(TREE):
        if 'index.json' in files:
            rel = os.path.relpath(d, TREE).replace(os.sep, '/')
            out[rel] = os.path.join(d, 'index.json')
    return out

def fetch(url, head=False):
    """(status, bytes) for `url` — status 0 when it could not be reached, so an outage is never read as absent."""
    req = urllib.request.Request(url, method='HEAD' if head else 'GET', headers={'User-Agent': 'kama-registry-check'})
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return r.status, (b'' if head else r.read())
    except urllib.error.HTTPError as e:
        return e.code, b''
    except (urllib.error.URLError, OSError):
        return 0, b''

def tarball(tb, head=False):
    """A tarball's bytes: from the staging directory if `ops publish` put it there, else from the bucket."""
    if PENDING and os.path.isfile(os.path.join(PENDING, tb)):
        return 200, (b'' if head else open(os.path.join(PENDING, tb), 'rb').read())
    return fetch(ARTIFACTS + tb, head)

def history(base):
    """{pkg: {version: entry}} at `base`, or None when there is no usable base (a first push)."""
    if not base or set(base) == {'0'} or git('cat-file', '-e', base + '^{commit}') is None: return None
    old = {}
    for path in (git('ls-tree', '-r', '--name-only', base, '--', 'registry') or b'').decode().split('\n'):
        if not path.endswith('/index.json'): continue
        idx = load(git('show', f'{base}:{path}') or b'{}', f'{path} @ {base[:12]}') or {}
        old[path[len(PREFIX):-len('/index.json')]] = {v.get('version'): v for v in idx.get('versions') or []}
    return old

def load(path_or_bytes, label):
    try:
        raw = path_or_bytes if isinstance(path_or_bytes, bytes) else open(path_or_bytes, 'rb').read()
        return json.loads(raw)
    except Exception as e:
        err(f'{label}: not valid JSON ({e})'); return None

SEMVER = re.compile(r'^(\d+)\.(\d+)\.(\d+)$')

def catalog_from(indexes):
    """What `kama publish` writes to catalog.json (writeRegistryCatalog in kama.driver.cpp): per package, sorted by
    path, its highest MAJOR.MINOR.PATCH version with `license`, `description`, `repository` and `keywords` when
    that version's entry has them."""
    out = []
    for pkg in sorted(indexes):
        idx = indexes[pkg] or {}
        best = None
        for v in idx.get('versions') or []:
            m = SEMVER.match(v.get('version') or '')
            if m and (best is None or tuple(map(int, m.groups())) > best[0]): best = (tuple(map(int, m.groups())), v)
        if best is None: continue
        v = best[1]
        entry = {'name': idx.get('name') or pkg, 'version': v['version']}
        for k in ('license', 'description', 'repository'):
            if v.get(k): entry[k] = v[k]
        if v.get('keywords'): entry['keywords'] = v['keywords']
        out.append(entry)
    return out

def catalog_text(entries):
    """catalog.json byte for byte as `kama publish` writes it: one package per line."""
    s = lambda x: json.dumps(x, ensure_ascii=False)
    lines = []
    for e in entries:
        parts = [f'"{k}": {s(e[k])}' if k != 'keywords' else '"keywords": [' + ', '.join(s(w) for w in e[k]) + ']'
                 for k in ('name', 'version', 'license', 'description', 'repository', 'keywords') if k in e]
        lines.append('{ ' + ', '.join(parts) + ' }')
    return '{\n  "packages": [' + (('\n    ' + ',\n    '.join(lines) + '\n  ]') if lines else ']') + '\n}\n'

def main():
    base = ''
    if '--base' in sys.argv:
        i = sys.argv.index('--base'); base = sys.argv[i + 1] if i + 1 < len(sys.argv) else ''
    if not os.path.isdir(TREE): print('check: FAIL — no registry/ directory'); return 1
    for f in SITE_FILES:
        if not os.path.isfile(os.path.join(TREE, f)): err(f'registry/{f} is missing')
    rp = os.path.join(TREE, '_redirects')
    if os.path.isfile(rp):
        rules = [' '.join(l.split()) for l in open(rp) if l.strip() and not l.lstrip().startswith('#')]
        if rules != REDIRECTS:
            err('registry/_redirects must hold exactly these rules, in this order — the first keeps every '
                'index.json on Pages, the second keeps a package page, the third sends tarballs to the bucket:\n    '
                + '\n    '.join(REDIRECTS))
    old = history(base)

    loaded = {pkg: load(ipath, f'{PREFIX}{pkg}/index.json') for pkg, ipath in sorted(indexes_on_disk().items())}
    want = catalog_text(catalog_from(loaded))
    cpath = os.path.join(TREE, CATALOG)
    if '--write-catalog' in sys.argv:
        with open(cpath, 'w', encoding='utf-8', newline='\n') as f: f.write(want)
        print(f'check: wrote {PREFIX}{CATALOG} ({len(catalog_from(loaded))} package(s))')
    if not os.path.isfile(cpath):
        err(f'{PREFIX}{CATALOG} is missing — `kama publish` (0.9.523 on) writes it; for a registry whose last publish '
            f'predates that, `python3 tools/check.py --write-catalog`')
    elif open(cpath, encoding='utf-8').read() != want:
        err(f'{PREFIX}{CATALOG} does not say what the indexes say — it is rebuilt by every `kama publish`, and '
            f'`python3 tools/check.py --write-catalog` rewrites it; it must never be edited by hand')

    now, nversions = {}, 0
    for pkg, ipath in sorted(indexes_on_disk().items()):
        label = f'{PREFIX}{pkg}/index.json'
        idx = load(ipath, label)
        if idx is None: continue
        if pkg.startswith(RESERVED): err(f'{label}: `@std` is reserved — the standard library ships inside the compiler')
        elif not pkg.startswith(OFFICIAL): err(f'{label}: this is the official registry; it serves `@kama/*` only')
        if idx.get('name') != pkg: err(f'{label}: "name" is {idx.get("name")!r} but the path says {pkg!r}')
        seen = {}
        for v in idx.get('versions') or []:
            ver, integ, tb = v.get('version'), v.get('integrity', ''), v.get('tarball', '')
            where = f'{pkg}@{ver}'
            if ver in seen: err(f'{where}: listed twice'); continue
            seen[ver] = v; nversions += 1
            m = INTEGRITY.match(integ)
            if not m: err(f'{where}: integrity {integ!r} is not sha256-<64 hex>'); continue
            # The layout `kama publish` writes, and the one `_redirects` routes: one segment below the package.
            if tb != f'{pkg}/{ver}.tar.gz':
                err(f'{where}: tarball {tb!r} is not {pkg}/{ver}.tar.gz — the path _redirects sends to the bucket'); continue
            if old is not None and ver in old.get(pkg, {}):
                # Published before this push: its bytes are held by the bucket lock; confirm it is still served.
                code, _ = tarball(tb, head=True)
                if code != 200: err(f'{where}: the bucket answers {code or "nothing"} for {ARTIFACTS}{tb}')
                continue
            code, data = tarball(tb)
            if code != 200:
                err(f'{where}: the bucket answers {code or "nothing"} for {ARTIFACTS}{tb} — `ops publish` uploads '
                    f'a tarball before its index is committed'); continue
            if hashlib.sha256(data).hexdigest() != m.group(1):
                err(f'{where}: tarball {tb!r} does not hash to its recorded integrity'); continue
            leaks = secrets_in(data)
            if leaks is None: err(f'{where}: {tb!r} is not a readable tarball'); continue
            for leak in leaks:
                err(f'{where}: tarball contains {leak!r}, which looks like a secret — a published version '
                    f'is permanent, so it could never be withdrawn. Remove it from the package and republish '
                    f'(a template such as .env.example is fine)')
            deps = path_deps_in(data)
            if deps is None: err(f'{where}: {tb!r} has no readable kama.json at its root'); continue
            for dep in deps:
                err(f'{where}: its kama.json depends on {dep!r} by `path` — a fetched package arrives without that '
                    f'directory, so no consumer could install this version, and it would be permanent. Depend on a '
                    f'released version and develop against the local copy with `overrides` in kama.local.json '
                    f'(kama publish refuses this itself from 0.9.472)')
        now[pkg] = seen

    for d, _, files in os.walk(TREE):
        for f in files:
            rel = os.path.relpath(os.path.join(d, f), TREE).replace(os.sep, '/')
            if rel in SITE_FILES or rel == CATALOG or f == 'index.json': continue
            if rel == 'index.html':
                err(f'{PREFIX}index.html: the pages are generated at deploy time (the kama repository\'s '
                    f'tools/site/registry.mjs), which would overwrite this one — remove it'); continue
            if f.endswith('.tar.gz'):
                err(f'{PREFIX}{rel}: a tarball in the tree — tarballs live in the bucket, and one here would be '
                    f'deployed to Pages and committed to git; `ops publish` stages and uploads it'); continue
            err(f'{PREFIX}{rel}: no index names it — it would be published anyway')

    history_note = 'skipped (no base)'
    if old is not None:
        for pkg, versions in old.items():
            if pkg not in now: err(f'{pkg}: the whole package was removed — published packages are permanent'); continue
            for ver, v in versions.items():
                where = f'{pkg}@{ver}'
                cur = now[pkg].get(ver)
                if cur is None: err(f'{where}: was published and is now gone — versions are permanent'); continue
                # The whole entry: a consumer resolves the graph from its `dependencies`, and `revision` is the claim
                # that the bytes rebuild from that commit — neither may move once published, any more than the hash.
                for k in sorted(set(cur) | set(v)):
                    if cur.get(k) != v.get(k): err(f'{where}: "{k}" changed — a published version is write-once')
        history_note = f'against {base[:12]} ({len(old)} package(s) there)'
    elif base and set(base) != {'0'}:
        history_note = f'skipped (base {base[:12]} is not a known commit)'

    if errors:
        print(f'check: FAIL — {len(errors)} problem(s):')
        for e in errors: print('  ' + e)
        return 1
    print(f'check: OK — {len(now)} package(s), {nversions} version(s); write-once {history_note}')
    return 0

if __name__ == '__main__':
    sys.exit(main())
