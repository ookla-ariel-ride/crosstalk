"""`relay.py deploy`: put one commit of a checkout into a separate folder, the running install.

The server used to run straight from the git checkout, so an edit or a
half-done merge was live at once. A deployment is a plain folder with the same
layout as the checkout, the app data beside the code, and no `.git`. Deploy
exports the tracked files of one commit (`git archive`), checks the target's
logs with the new code, and only then copies the files in. It never writes or
removes a data path, and it removes only the files an earlier deploy put there:
`.deployed.json` in the target lists them.
"""
import filecmp
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.request
from pathlib import Path
from typing import List, Optional, Tuple

MANIFEST = ".deployed.json"
# The app data and the secrets of an install (.gitignore names the same paths).
# A name that ends in "/" is a folder. Deploy never writes or removes these.
DATA_PATHS = ("log/", "import/", "kits/", "local/", "participants.json", "config.json", "tokens.json",
              "owner-pin.json", "owner-pin.json.tmp")
DEFAULT_PORT = 8484
HEALTH_TIMEOUT = 30.0


class DeployError(Exception):
    """A refusal or a failure, with the message to print."""


def is_data_path(rel: str) -> bool:
    """True for a path deploy must leave alone: app data, secrets, the manifest, `.git`."""
    if rel == MANIFEST or rel == ".git" or rel.startswith(".git/"):
        return True
    for p in DATA_PATHS:
        if p.endswith("/"):
            if rel == p[:-1] or rel.startswith(p):
                return True
        elif rel == p:
            return True
    return False


def read_manifest(target) -> Optional[dict]:
    """The target's `.deployed.json`, or None when there is none or it is unreadable."""
    try:
        data = json.loads((Path(target) / MANIFEST).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) and data.get("sha") else None


def _git(root: Path, *args) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(["git", "-C", str(root)] + list(args), stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, universal_newlines=True, timeout=60)
    except (OSError, subprocess.SubprocessError) as e:
        raise DeployError("cannot run git: %s" % e)


def _git_value(root: Path, *args) -> Optional[str]:
    p = _git(root, *args)
    return p.stdout.strip() if p.returncode == 0 and p.stdout.strip() else None


def resolve_ref(root: Path, ref: str) -> str:
    sha = _git_value(root, "rev-parse", "--verify", "--quiet", "%s^{commit}" % ref)
    if not sha:
        raise DeployError("the ref %r does not name a commit in %s" % (ref, root))
    return sha


def default_target(root: Path) -> Optional[Path]:
    """`git config crosstalk.deploydir`, or None."""
    value = _git_value(root, "config", "crosstalk.deploydir") if (root / ".git").exists() else None
    return Path(value).expanduser() if value else None


def check_target(root: Path, target: Path) -> None:
    """Refuse a target that is a checkout, or that is the source checkout or inside it."""
    src, dst = root.resolve(), target.resolve()
    if dst == src or src in dst.parents:
        raise DeployError("the target %s is the source checkout or inside it; "
                          "a deployment is a separate folder" % target)
    if (target / ".git").exists():
        raise DeployError("the target %s holds .git: that is a checkout, not a deployment" % target)
    if target.exists() and not target.is_dir():
        raise DeployError("the target %s is not a folder" % target)


def stage(root: Path, sha: str, staging: Path) -> List[str]:
    """Export the tracked files of `sha` into `staging`; returns their paths, sorted."""
    staging.mkdir(parents=True, exist_ok=True)
    tar_path = staging.parent / "export.tar"
    p = _git(root, "archive", "--format=tar", "-o", str(tar_path), sha)
    if p.returncode != 0:
        raise DeployError("git archive failed: %s" % p.stderr.strip())
    with tarfile.open(str(tar_path)) as tar:
        tar.extractall(str(staging))
    tar_path.unlink()
    files = []
    for path in staging.rglob("*"):
        if path.is_symlink() or path.is_file():
            files.append(path.relative_to(staging).as_posix())
    return sorted(files)


def precheck(staging: Path, target: Path) -> Tuple[bool, str]:
    """Run the staged `relay.py check` over the target's data; the target is not written.

    `relay.py` finds its root from its own path, so the staged copy reads the
    staging folder. A link there to each data path the target holds makes that
    the target's data. A data path the target lacks gets no link, so a first
    run writes its new roster into the staging folder, not into the target.
    """
    relay = staging / "scripts" / "relay.py"
    if not relay.is_file():
        return False, "the commit holds no scripts/relay.py"
    for p in DATA_PATHS:
        name = p.rstrip("/")
        src, link = target / name, staging / name
        if src.exists() and not link.exists() and not link.is_symlink():
            os.symlink(str(src.resolve()), str(link))
    try:
        run = subprocess.run([sys.executable, str(relay), "check"], cwd=str(staging),
                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                             universal_newlines=True, timeout=300)
    except (OSError, subprocess.SubprocessError) as e:
        return False, "cannot run the staged check: %s" % e
    return run.returncode == 0, run.stdout.strip()


def plan_changes(staging: Path, target: Path, files: List[str], old: Optional[dict]):
    """(files to copy, files whose bytes differ or are new, old files to remove, data paths skipped)."""
    skipped = [f for f in files if is_data_path(f)]
    copy = [f for f in files if not is_data_path(f)]
    changed = []
    for f in copy:
        dest = target / f
        src = staging / f
        if src.is_symlink() or dest.is_symlink() or not dest.is_file() or not filecmp.cmp(str(src), str(dest), shallow=False):
            changed.append(f)
    keep = set(copy)
    old_files = old.get("files") if old else None
    remove = sorted(f for f in (old_files if isinstance(old_files, list) else [])
                    if isinstance(f, str) and f not in keep and not is_data_path(f)
                    and not os.path.isabs(f) and ".." not in Path(f).parts
                    and ((target / f).is_file() or (target / f).is_symlink()))
    return copy, changed, remove, skipped


def _copy_file(src: Path, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".deploy-tmp")
    if tmp.exists() or tmp.is_symlink():
        tmp.unlink()
    if src.is_symlink():
        os.symlink(os.readlink(str(src)), str(tmp))
    else:
        shutil.copy2(str(src), str(tmp))
    os.replace(str(tmp), str(dest))


def apply_changes(staging: Path, target: Path, copy: List[str], remove: List[str], manifest: dict) -> None:
    """Copy the files in, remove the old deploy's leftovers, then write the manifest."""
    target.mkdir(parents=True, exist_ok=True)
    for f in copy:
        _copy_file(staging / f, target / f)
    for f in remove:
        path = target / f
        path.unlink()
        parent = path.parent
        while parent != target:  # drop the folders the removal left empty
            try:
                parent.rmdir()
            except OSError:
                break
            parent = parent.parent
    tmp = target / (MANIFEST + ".tmp")
    tmp.write_text(json.dumps(manifest, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(str(tmp), str(target / MANIFEST))


def build_manifest(root: Path, ref: str, sha: str, files: List[str]) -> dict:
    epoch = _git_value(root, "log", "-1", "--format=%ct", sha)
    count = _git_value(root, "rev-list", "--count", sha)
    return {
        "ref": ref,
        "sha": sha,
        "short": _git_value(root, "rev-parse", "--short", sha) or sha[:7],
        "commit_date": (time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(int(epoch)))
                        if epoch and epoch.isdigit() else None),
        "build_number": int(count) if count and count.isdigit() else None,
        "time": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "source": str(root.resolve()),
        "files": files,
    }


def install(root: Path, target: Path, ref: str, sha: str, out, dry_run: bool = False) -> None:
    """Stage `sha`, check it against the target's data, then copy it in. Raises DeployError."""
    old = read_manifest(target)
    tmp = Path(tempfile.mkdtemp(prefix="crosstalk-deploy-"))
    try:
        staging = tmp / "staging"
        files = stage(root, sha, staging)
        ok, text = precheck(staging, target)
        if not ok:
            raise DeployError("the check failed with the new code over the data in %s; "
                              "nothing was changed there\n%s" % (target, text))
        out("check: %s" % (text.splitlines()[-1] if text else "ok"))
        copy, changed, remove, skipped = plan_changes(staging, target, files, old)
        for f in skipped:
            out("warning: the commit tracks the data path %s; not deployed" % f)
        word = "would deploy" if dry_run else "deployed"
        if dry_run:
            for f in changed:
                out("would write %s" % f)
            for f in remove:
                out("would remove %s" % f)
        else:
            apply_changes(staging, target, copy, remove, build_manifest(root, ref, sha, copy))
        out("%s %s (%s) to %s: %d files, %d new or changed, %d removed"
            % (word, sha[:12], ref, target, len(copy), len(changed), len(remove)))
    finally:
        shutil.rmtree(str(tmp), ignore_errors=True)


def service_port(target: Path) -> int:
    """The port in the target's server plist template."""
    try:
        text = (target / "scripts" / "launchd" / "crosstalk.plist").read_text(encoding="utf-8")
    except OSError:
        return DEFAULT_PORT
    m = re.search(r"--port</string>\s*<string>(\d+)</string>", text)
    return int(m.group(1)) if m else DEFAULT_PORT


def restart_service(target: Path) -> Tuple[bool, str]:
    """Load the LaunchAgents from the target's own installer. Tests replace this function."""
    script = target / "scripts" / "install-service.sh"
    try:
        run = subprocess.run(["/bin/sh", str(script), "install"], stdout=subprocess.PIPE,
                             stderr=subprocess.STDOUT, universal_newlines=True, timeout=120)
    except (OSError, subprocess.SubprocessError) as e:
        return False, str(e)
    return run.returncode == 0, run.stdout.strip()


def wait_healthy(port: int, sha: str, timeout: float = HEALTH_TIMEOUT) -> Tuple[bool, str]:
    """Poll `/api/health` until it is ok and reports `sha` (a server too old to report one passes on ok)."""
    url = "http://127.0.0.1:%d/api/health" % port
    deadline = time.time() + timeout
    last = "no answer"
    while True:
        try:
            with urllib.request.urlopen(url, timeout=5) as r:
                body = json.loads(r.read().decode("utf-8"))
            if body.get("ok") and body.get("sha") in (None, sha):
                return True, "healthy at %s" % url
            last = ("the server runs %s" % body.get("sha") if body.get("ok")
                    else "not ok: %s" % "; ".join(body.get("fatal") or [])[:300])
        except (OSError, ValueError) as e:
            last = str(e)
        if time.time() >= deadline:
            return False, "%s after %d s: %s" % (url, timeout, last)
        time.sleep(0.5)


def _restart(target: Path, sha: str, out) -> bool:
    ok, text = restart_service(target)
    if not ok:
        out("restart failed: %s" % text)
        return False
    ok, text = wait_healthy(service_port(target), sha)
    out(("restart: %s" if ok else "health check failed: %s") % text)
    return ok


def deploy(root, target, ref: str = "HEAD", dry_run: bool = False, restart: bool = False, out=print) -> int:
    """Deploy `ref` of the checkout at `root` to `target`. Returns the exit status."""
    root, target = Path(root), Path(target).expanduser()
    if not (root / ".git").exists():
        raise DeployError("%s is not a git checkout; run deploy from the checkout" % root)
    check_target(root, target)
    sha = resolve_ref(root, ref)
    if _git_value(root, "status", "--porcelain"):
        out("warning: the working tree has uncommitted changes; only the commit %s ships" % sha[:12])
    previous = read_manifest(target)
    install(root, target, ref, sha, out, dry_run=dry_run)
    if dry_run or not restart:
        return 0
    if _restart(target, sha, out):
        return 0
    if not previous:
        out("no earlier deployment to go back to; %s stays in %s and the service is not healthy"
            % (sha[:12], target))
        return 1
    old_sha = previous["sha"]
    try:
        resolve_ref(root, old_sha)
        install(root, target, previous.get("ref") or old_sha, old_sha, out)
    except DeployError as e:
        out("rollback to %s failed: %s" % (old_sha[:12], e))
        return 1
    back = _restart(target, old_sha, out)
    out("deploy of %s failed; rolled back to %s; the service is %s"
        % (sha[:12], old_sha[:12], "healthy" if back else "NOT healthy"))
    return 1


def status(root, target, out=print) -> int:
    """Print what the target runs, and how far the checkout is ahead of it."""
    root, target = Path(root), Path(target).expanduser()
    m = read_manifest(target)
    if not m:
        out("no deployment at %s (no %s)" % (target, MANIFEST))
        return 1
    out("deployed: %s (%s)" % (m.get("short") or m["sha"][:7], m["sha"]))
    out("ref: %s" % m.get("ref"))
    out("time: %s" % m.get("time"))
    out("target: %s" % target)
    out("source: %s" % m.get("source"))
    if (root / ".git").exists():
        ahead = _git_value(root, "rev-list", "--count", "%s..HEAD" % m["sha"])
        if ahead is None:
            out("checkout: %s does not have the deployed commit" % root)
        else:
            behind = _git_value(root, "rev-list", "--count", "HEAD..%s" % m["sha"]) or "0"
            out("checkout: HEAD is %s commit(s) ahead of the deployment%s"
                % (ahead, "" if behind == "0" else ", and lacks %s of its commits" % behind))
    return 0


def run(root, args, out=print) -> int:
    """The `deploy` command: `args` holds to, ref, restart, dry_run and status."""
    root = Path(root)
    target = Path(args.to).expanduser() if args.to else default_target(root)
    try:
        if args.status:
            if target is None and read_manifest(root):
                target = root  # run inside a deployment: report on it
            if target is None:
                raise DeployError("no target: pass --to <dir> or set git config crosstalk.deploydir")
            return status(root, target, out)
        if target is None:
            raise DeployError("no target: pass --to <dir> or set git config crosstalk.deploydir")
        return deploy(root, target, ref=args.ref, dry_run=args.dry_run, restart=args.restart, out=out)
    except DeployError as e:
        print("deploy: %s" % e, file=sys.stderr)
        return 1
