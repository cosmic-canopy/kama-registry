#!/usr/bin/env python3
"""check.py [--base REF] — what this registry guarantees, enforced before anything deploys.

`kama publish` already refuses to overwrite a version. That is a promise made by ONE client, on ONE machine,
and this repo accepts commits from anywhere git does — so the host enforces the same rules itself:

  integrity   every version's tarball exists and hashes to the sha256 its index records
  write-once  against --base, no existing version (or package) is removed, and no published version's
              entry or tarball bytes change. A lockfile pins the hash; changing it breaks every consumer.
  scope       this is the OFFICIAL registry: it serves `@kama/*` and nothing else, and never `@std/*`
  secrets     no tarball contains a secret-shaped file (.env, private keys, credential files). Before 0.9.452
              `kama publish` tarred the directory and ignored .gitignore, so a gitignored .env shipped;
              from 0.9.452 it ships only committed, git-tracked files and refuses these names itself. But
              this repo takes a tarball from any compiler, and a version here is permanent — it can never
              be withdrawn — so the host refuses them whatever the publisher ran.
  hygiene     no stray file ships that no index names, and 404.html exists — without it Cloudflare Pages
              serves index.html with status 200 for every unknown path, so a lookup for a package that
              does not exist would get a page of HTML instead of a 404.

With no --base (or an all-zero / unknown ref, as on a repo's first push) the history rules are skipped.
"""
import hashlib, json, os, re, subprocess, sys, tarfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TREE = os.path.join(ROOT, 'registry')
PREFIX = 'registry/'
OFFICIAL, RESERVED = '@kama/', '@std/'
INTEGRITY = re.compile(r'^sha256-([0-9a-f]{64})$')
SITE_FILES = {'index.html', '404.html'}
# Secret-shaped basenames. A template (.env.example) is the documented way to ship the NAMES without values.
SECRET = re.compile(r'''^(\.env(\..+)?|.*\.(pem|key|p12|pfx|jks|keystore)|id_(rsa|dsa|ecdsa|ed25519)|\.netrc|\.npmrc|\.pypirc|kama\.local\.json)$''')
SECRET_OK = re.compile(r'^\.env\.(example|sample|template|dist)$')

def secrets_in(path):
    try:
        with tarfile.open(path, 'r:*') as t:
            return [m.name for m in t.getmembers()
                    if m.isfile() and SECRET.match(os.path.basename(m.name)) and not SECRET_OK.match(os.path.basename(m.name))]
    except tarfile.TarError as e:
        return [f'<unreadable tarball: {e}>']      # the tree's own pages, not package content

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

def load(path_or_bytes, label):
    try:
        raw = path_or_bytes if isinstance(path_or_bytes, bytes) else open(path_or_bytes, 'rb').read()
        return json.loads(raw)
    except Exception as e:
        err(f'{label}: not valid JSON ({e})'); return None

def main():
    base = ''
    if '--base' in sys.argv:
        i = sys.argv.index('--base'); base = sys.argv[i + 1] if i + 1 < len(sys.argv) else ''
    if not os.path.isdir(TREE): print('check: FAIL — no registry/ directory'); return 1
    for f in SITE_FILES:
        if not os.path.isfile(os.path.join(TREE, f)): err(f'registry/{f} is missing')

    referenced, now, nversions = set(), {}, 0
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
            if '://' in tb: continue                    # an absolute URL: lives elsewhere, checked by the client
            p = os.path.normpath(os.path.join(TREE, tb))
            if not p.startswith(TREE + os.sep): err(f'{where}: tarball {tb!r} points outside the tree'); continue
            referenced.add(os.path.relpath(p, TREE).replace(os.sep, '/'))
            if not os.path.isfile(p): err(f'{where}: tarball {tb!r} does not exist'); continue
            if hashlib.sha256(open(p, 'rb').read()).hexdigest() != m.group(1):
                err(f'{where}: tarball {tb!r} does not hash to its recorded integrity')
            for leak in secrets_in(p):
                err(f'{where}: tarball contains {leak!r}, which looks like a secret — a published version '
                    f'is permanent, so it could never be withdrawn. Remove it from the package and republish '
                    f'(a template such as .env.example is fine)')
        now[pkg] = seen

    for d, _, files in os.walk(TREE):
        for f in files:
            rel = os.path.relpath(os.path.join(d, f), TREE).replace(os.sep, '/')
            if rel in SITE_FILES or f == 'index.json' or rel in referenced: continue
            err(f'{PREFIX}{rel}: no index names it — it would be published anyway')

    history = 'skipped (no base)'
    if base and set(base) != {'0'}:
        if git('cat-file', '-e', base + '^{commit}') is None:
            history = f'skipped (base {base[:12]} is not a known commit)'
        else:
            listing = (git('ls-tree', '-r', '--name-only', base, '--', 'registry') or b'').decode().split('\n')
            old_pkgs = 0
            for path in listing:
                if not path.endswith('/index.json'): continue
                pkg = path[len(PREFIX):-len('/index.json')]
                old = load(git('show', f'{base}:{path}') or b'{}', f'{path} @ {base[:12]}') or {}
                old_pkgs += 1
                if pkg not in now: err(f'{pkg}: the whole package was removed — published packages are permanent'); continue
                for v in old.get('versions') or []:
                    where = f'{pkg}@{v.get("version")}'
                    cur = now[pkg].get(v.get('version'))
                    if cur is None: err(f'{where}: was published and is now gone — versions are permanent'); continue
                    for k in ('integrity', 'tarball'):
                        if cur.get(k) != v.get(k): err(f'{where}: "{k}" changed — a published version is write-once')
                    tb = v.get('tarball', '')
                    if tb and '://' not in tb:
                        oldb = git('show', f'{base}:{PREFIX}{tb}')
                        p = os.path.join(TREE, tb)
                        if oldb is not None and os.path.isfile(p) and open(p, 'rb').read() != oldb:
                            err(f'{where}: tarball bytes changed — a published version is write-once')
            history = f'against {base[:12]} ({old_pkgs} package(s) there)'

    if errors:
        print(f'check: FAIL — {len(errors)} problem(s):')
        for e in errors: print('  ' + e)
        return 1
    print(f'check: OK — {len(now)} package(s), {nversions} version(s); write-once {history}')
    return 0

if __name__ == '__main__':
    sys.exit(main())
