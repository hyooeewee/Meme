"""System commands."""
import json
import os
import re
import subprocess
import sys
from pathlib import Path

from meme import __version__ as CURRENT_VERSION
from meme.constants import MEME_HOME, META_DIR, VERSION_PATH, VERSION_CHECK_PATH
from meme.utils import load_memory
from meme.vault import load_vault_memory


# ========================================
# Command: heat
# ========================================

def cmd_heat(args):
    """Show current session heat map."""
    if not SESSION_HEAT_PATH.exists():
        print("No active session heat data.")
        return
    data = json.loads(SESSION_HEAT_PATH.read_text())
    heat_map = data.get("heat_map", {})
    if not heat_map:
        print("No memories heated this session.")
        return
    print(f"Session: {data.get('session_id', 'unknown')}")
    print(f"Started: {data.get('started', 'unknown')}\n")
    for mid, info in sorted(heat_map.items(), key=lambda x: -x[1].get("heat", 0)):
        print(f"  {mid}: heat={info.get('heat', 0):.2f}")

# ========================================
# Command: auth (biometric-gated secret access)
# ========================================

def cmd_auth(args):
    """Authenticate and export a vault secret as an environment variable."""
    mem_id = args.mem_id
    var_name = args.var or "MEM_SECRET"

    # Find the memory
    mem_path = find_memory_by_id(mem_id)
    if not mem_path:
        print(f"echo 'ERROR: Memory {mem_id} not found' >&2", file=sys.stderr)
        sys.exit(1)

    # Must be a vault memory
    if mem_path.suffix != ".enc":
        print(f"echo 'ERROR: {mem_id} is not a sensitive (vault) memory' >&2",
              file=sys.stderr)
        sys.exit(1)

    # Auth: retrieving the vault key triggers OS-level auth (Touch ID / Hello / password)
    try:
        key = _get_vault_key()
    except Exception as e:
        print(f"echo 'ERROR: Authentication failed — {e}' >&2", file=sys.stderr)
        sys.exit(1)

    # Decrypt
    try:
        meta, body = load_vault_memory(mem_id)
    except Exception as e:
        print(f"echo 'ERROR: Decryption failed — {e}' >&2", file=sys.stderr)
        sys.exit(1)

    if not body:
        print(f"echo 'ERROR: Memory {mem_id} is empty' >&2", file=sys.stderr)
        sys.exit(1)

    # Write secret to a secure temp file instead of stdout to keep it out of AI context
    import tempfile
    escaped = body.replace("'", "'\\''")
    fd, tmp_path = tempfile.mkstemp(prefix="memectl_secret_", suffix=".sh")
    os.chmod(tmp_path, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(f"export {var_name}='{escaped}'\n")
    # Output a command that sources the file and then removes it
    print(f"source '{tmp_path}' && rm -f '{tmp_path}'")

# ========================================
# Command: run (vault-gated command execution)
# ========================================

def cmd_run(args):
    """Decrypt a vault secret, inject it as an env var, and exec a command.

    The AI never sees the plaintext — it only references the variable name.
    Use single quotes around arguments that reference $VAR so the shell
    does not expand them before meme run sets the env var.

    Example:
        meme run mem_xxx --var API_TOKEN -- sh -c 'curl -H "Authorization: Bearer $API_TOKEN" https://api.example.com'
    """
    mem_id = args.mem_id
    var_name = args.var or "MEM_SECRET"
    cmd_list = args.cmd

    if not cmd_list:
        print("ERROR: No command provided after '--'", file=sys.stderr)
        sys.exit(1)

    # Find the memory
    mem_path = find_memory_by_id(mem_id)
    if not mem_path:
        print(f"ERROR: Memory {mem_id} not found", file=sys.stderr)
        sys.exit(1)

    if mem_path.suffix != ".enc":
        print(f"ERROR: {mem_id} is not a vault memory", file=sys.stderr)
        sys.exit(1)

    # Auth + decrypt (triggers OS-level Touch ID / password if needed)
    try:
        _get_vault_key()
    except Exception as e:
        print(f"ERROR: Authentication failed — {e}", file=sys.stderr)
        sys.exit(1)

    try:
        meta, body = load_vault_memory(mem_id)
    except Exception as e:
        print(f"ERROR: Decryption failed — {e}", file=sys.stderr)
        sys.exit(1)

    if not body:
        print(f"ERROR: Memory {mem_id} is empty", file=sys.stderr)
        sys.exit(1)

    # Inject into environment (AI never sees this value)
    os.environ[var_name] = body.strip()

    # Exec the target command — this replaces the current process
    # so the secret is never returned as output to the AI
    try:
        os.execvp(cmd_list[0], cmd_list)
    except FileNotFoundError:
        print(f"ERROR: Command not found: {cmd_list[0]}", file=sys.stderr)
        sys.exit(127)
    except Exception as e:
        print(f"ERROR: Failed to execute command — {e}", file=sys.stderr)
        sys.exit(1)


# ========================================
# Command: version / upgrade / changelog
# ========================================

def cmd_version(args):
    """Show version info."""
    print(f"Meme v{CURRENT_VERSION}")
    if VERSION_PATH.exists():
        data = json.loads(VERSION_PATH.read_text())
        print(f"  Installed: {data.get('installed_at', 'unknown')}")
        print(f"  Schema: v{data.get('schema_version', '?')}")

def _check_remote_version(timeout=5):
    """Check for the latest published version.

    Strategy: PyPI first, then GitHub tags fallback.
    Returns the latest version string if newer than CURRENT_VERSION, else None.
    """
    import urllib.request
    import urllib.error

    # --- Try PyPI ---
    try:
        url = "https://pypi.org/pypi/memectl/json"
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode())
            ver = data.get("info", {}).get("version", "")
            if ver and _version_tuple(ver) > _version_tuple(CURRENT_VERSION):
                return ver
    except (urllib.error.URLError, OSError, json.JSONDecodeError, KeyError):
        pass

    # --- Fallback: GitHub tags ---
    try:
        url = "https://api.github.com/repos/hyooeewee/Meme/tags?per_page=20"
        req = urllib.request.Request(url, headers={"Accept": "application/vnd.github.v3+json"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            tags = json.loads(resp.read().decode())
            best = _version_tuple(CURRENT_VERSION)
            best_str = None
            for t in tags:
                name = t.get("name", "").lstrip("v")
                if _version_tuple(name) > best:
                    best = _version_tuple(name)
                    best_str = name
            if best_str:
                return best_str
    except (urllib.error.URLError, OSError, json.JSONDecodeError, KeyError):
        pass

    return None


def _version_tuple(v):
    """Parse 'x.y.z' into (x, y, z) for comparison."""
    try:
        return tuple(int(x) for x in v.split("."))
    except (ValueError, AttributeError):
        return (0, 0, 0)


def cmd_upgrade(args):
    """Check for upgrades or perform upgrade."""
    if getattr(args, "check", False):
        latest = _check_remote_version()
        if latest:
            print(f"New version available: {CURRENT_VERSION} -> {latest}")
            # Cache the result
            VERSION_CHECK_PATH.write_text(json.dumps({
                "latest": latest,
                "current": CURRENT_VERSION,
                "checked_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            }, indent=2))
        else:
            print(f"Meme {CURRENT_VERSION} is up to date.")
        return

    # Full upgrade
    print(f"Meme v{CURRENT_VERSION}")
    force = getattr(args, "force", False)
    # Check for latest version first
    latest = _check_remote_version()
    if not latest and not force:
        print("Already up to date.")
        return
    if force and not latest:
        latest = CURRENT_VERSION  # force reinstall current version

    print(f"New version available: {CURRENT_VERSION} -> {latest}")
    print()

    venv_dir = MEME_HOME / "venv"
    pkg_dir = MEME_HOME / "pkg"

    if venv_dir.exists():
        # Installed via install.sh (venv + pip)
        venv_pip = venv_dir / "bin" / "pip"
        if venv_pip.exists():
            print("Upgrading via pip in venv...")
            result = subprocess.run(
                [str(venv_pip), "install", "--upgrade", "memectl"],
                capture_output=True,
                text=True,
            )
            if result.returncode == 0:
                print("Package updated. Refreshing hooks...")
                # Re-install hook scripts from package
                for hook_file in ["session_start.sh", "query.sh", "session_end.sh"]:
                    dst = BIN_DIR / f"meme-{hook_file.replace('_', '-')}"
                    if dst.is_symlink():
                        continue
                    src = _get_package_resource_path(f"hooks/{hook_file}")
                    if src:
                        shutil.copy2(src, dst)
                        dst.chmod(0o755)
                # Update version meta
                if VERSION_PATH.exists():
                    data = json.loads(VERSION_PATH.read_text())
                    data["installed_version"] = latest
                    data["last_upgrade"] = datetime.datetime.now().isoformat()
                    VERSION_PATH.write_text(json.dumps(data, indent=2))
                VERSION_CHECK_PATH.write_text(json.dumps({
                    "latest": latest,
                    "current": CURRENT_VERSION,
                    "checked_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                }, indent=2))
                git_commit(f"upgrade: v{latest}")
                print(f"Upgraded to {latest}.")
                print("  Run 'meme --version' to verify.")
            else:
                print("pip upgrade failed:")
                print(result.stderr or result.stdout)
                print("Try manually:")
                print(f"  {venv_pip} install --upgrade memectl")
        else:
            print(f"venv found but pip missing at {venv_pip}")
            print("To upgrade, re-run the installer:")
            print("  curl -sSL https://raw.githubusercontent.com/hyooeewee/Meme/main/install.sh | bash")
    elif (pkg_dir / ".git").exists():
        # Legacy: installed via old install.sh (git clone)
        print("Upgrading via git pull...")
        result = git_run("-C", str(pkg_dir), "pull", "--ff-only", check=False)
        if result.returncode == 0:
            print("Updated. Re-installing CLI...")
            cli_src = pkg_dir / "meme"
            cli_dst = BIN_DIR / "meme"
            if cli_src.exists():
                shutil.copy2(cli_src, cli_dst)
                cli_dst.chmod(0o755)
            for hook_file in ["session_start.sh", "query.sh", "session_end.sh"]:
                src = pkg_dir / "hooks" / hook_file
                dst = BIN_DIR / f"meme-{hook_file.replace('_', '-')}"
                if src.exists() and dst.is_symlink():
                    shutil.copy2(src, dst)
                    dst.chmod(0o755)
            if VERSION_PATH.exists():
                data = json.loads(VERSION_PATH.read_text())
                data["installed_version"] = latest
                data["last_upgrade"] = datetime.datetime.now().isoformat()
                VERSION_PATH.write_text(json.dumps(data, indent=2))
            VERSION_CHECK_PATH.write_text(json.dumps({
                "latest": latest,
                "current": CURRENT_VERSION,
                "checked_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            }, indent=2))
            git_commit(f"upgrade: v{latest}")
            print(f"Upgraded to {latest}.")
        else:
            print("git pull failed. Try manually:")
            print(f"  cd {pkg_dir} && git pull --ff-only")
    else:
        print("Unknown installation method.")
        print("To upgrade, re-run the installer:")
        print("  curl -sSL https://raw.githubusercontent.com/hyooeewee/Meme/main/install.sh | bash")

def cmd_changelog(args):
    """Show changelog."""
    print("Changelog: see git log for changes.")
    if (MEME_HOME / ".git").exists():
        result = git_run("log", "--oneline", "-20", check=False)
        if result.stdout:
            print(result.stdout)

