"""Setup and init commands."""
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

from meme.constants import MEME_HOME, BIN_DIR, META_DIR, ARCHIVE_DIR, WORKING_DIR, BACKUPS_DIR, MEMORY_MD_PATH
from meme.utils import ensure_symlink, _get_package_resource_path

# ========================================
# Command: setup
# ========================================

def cmd_setup(args):
    """Set up the Meme system."""
    is_dev = getattr(args, "dev", False)
    already_installed = MEME_HOME.exists() and (MEME_HOME / "meta" / "version.json").exists()

    if already_installed and not is_dev:
        print(f"Meme is already set up at {MEME_HOME}")
        print("Use 'meme upgrade' to update, or 'meme uninstall' first.")
        return

    if already_installed and is_dev:
        print("Re-syncing hooks in dev mode...")
    else:
        print("Setting up Meme memory system...")

    # Create directories
    for d in [WORKING_DIR, ARCHIVE_DIR, COLD_DIR, VAULT_DIR, BACKUPS_DIR, META_DIR, BIN_DIR]:
        d.mkdir(parents=True, exist_ok=True)
    for sub in ["projects", "feedback", "knowledge", "corrections"]:
        (ARCHIVE_DIR / sub).mkdir(parents=True, exist_ok=True)

    # Init git
    if not (MEME_HOME / ".git").exists():
        git_run("init")
        git_run("checkout", "-b", "main")

    # Write .gitignore
    gitignore = MEME_HOME / ".gitignore"
    gitignore.write_text(
        "vault/*.enc\nbackups/*.tar.gz\nmeta/session_heat.json\ncold/_index.json\n"
        ".upgrade-tmp/\n__pycache__/\n*.pyc\n.DS_Store\n",
        encoding="utf-8",
    )

    # Write initial meta files
    save_index({})
    save_graph({})

    is_dev = getattr(args, "dev", False)
    version_data = {
        "installed_version": CURRENT_VERSION,
        "installed_at": datetime.datetime.now().isoformat(),
        "schema_version": CURRENT_SCHEMA,
        "last_upgrade": None,
        "last_doctor": None,
        "dev": is_dev,
        "obsidian_path": None,
    }
    VERSION_PATH.write_text(json.dumps(version_data, indent=2))

    IMPORT_STATE_PATH.write_text("{}")
    CONFLICT_LOG_PATH.write_text("")
    DECAY_LOG_PATH.write_text("")

    # Write MEMORY.md
    rebuild_memory_md()

    # Install hook scripts (copy in prod, symlink in dev)
    for hook_file in ["session_start.sh", "query.sh", "session_end.sh"]:
        dst = BIN_DIR / f"meme-{hook_file.replace('_', '-')}"
        src = _get_package_resource_path(f"hooks/{hook_file}")
        if not src:
            continue
        if is_dev:
            if dst.exists() or dst.is_symlink():
                dst.unlink()
            dst.symlink_to(src)
            print(f"  Dev hook: {dst} -> {src}")
        else:
            if dst.is_symlink():
                dst.unlink()
            elif dst.exists():
                continue
            shutil.copy2(src, dst)
            dst.chmod(0o755)

    # In dev mode, symlink the CLI entry point so `meme` works from PATH
    if is_dev:
        cli_dst = BIN_DIR / "meme"
        repo_root = Path(__file__).resolve().parent.parent.parent
        venv_meme = repo_root / ".venv" / "bin" / "meme"
        launcher = repo_root / "meme"
        if venv_meme.exists():
            if cli_dst.exists() or cli_dst.is_symlink():
                cli_dst.unlink()
            cli_dst.symlink_to(venv_meme)
            print(f"  Dev CLI: {cli_dst} -> {venv_meme}")
        elif launcher.exists():
            if cli_dst.exists() or cli_dst.is_symlink():
                cli_dst.unlink()
            cli_dst.symlink_to(launcher)
            print(f"  Dev CLI: {cli_dst} -> {launcher}")

    # Create symlinks for Claude Code projects
    _setup_project_symlinks()

    # Register hooks in Claude Code settings
    _register_hooks()

    # Optional: migrate
    if getattr(args, "migrate", False):
        _do_import_claude()
        _do_import_claude_global()

    # Optional: Obsidian
    obsidian_path = getattr(args, "obsidian", None)
    if obsidian_path:
        obsidian_target = Path(obsidian_path).expanduser()
        if obsidian_target.exists():
            meme_link = obsidian_target / "Meme"
            ensure_symlink(meme_link, MEME_HOME)
            print(f"  Obsidian symlink: {meme_link} -> {MEME_HOME}")
            # Record obsidian path in version.json for uninstall
            try:
                vd = json.loads(VERSION_PATH.read_text())
                vd["obsidian_path"] = str(obsidian_target)
                VERSION_PATH.write_text(json.dumps(vd, indent=2))
            except Exception:
                pass

    # Initial commit
    git_commit("init: meme memory system installed")

    # Add to PATH if not already in shell rc file
    _setup_path(str(BIN_DIR))

    print(f"\nMeme set up successfully at {MEME_HOME}")
    print("  Run 'meme --help' to get started.")


def _setup_path(bin_str: str):
    """Auto-detect shell and add bin dir to PATH."""
    import platform
    if platform.system() == "Windows":
        print(f"\n  [!] Windows detected. Please add to PATH manually:")
        print(f"      setx PATH \"%PATH%;{bin_str}\"")
        print(f"  Or run inside WSL for full support.")
        return

    shell = os.environ.get("SHELL", "")
    export_line = f'export PATH="{bin_str}:$PATH"'
    marker = "# meme-memory-system"

    # Determine rc file
    rc_file = None
    if "zsh" in shell:
        rc_file = Path.home() / ".zshrc"
    elif "bash" in shell:
        # macOS ships zsh by default; bash users go to .bash_profile
        rc_file = Path.home() / ".bash_profile"
    elif "fish" in shell:
        # Fish uses a different syntax
        export_line = f'set -gx PATH "{bin_str}" $PATH'
        rc_file = Path.home() / ".config" / "fish" / "config.fish"
    else:
        rc_file = Path.home() / ".profile"

    # Check if already configured
    if rc_file.exists():
        content = rc_file.read_text(encoding="utf-8")
        if bin_str in content:
            print(f"  PATH already configured in {rc_file.name}")
            return

    # Append to rc file
    try:
        rc_file.parent.mkdir(parents=True, exist_ok=True)
        with open(rc_file, "a", encoding="utf-8") as f:
            f.write(f"\n{marker}\n{export_line}\n")
        print(f"  Added to PATH in {rc_file}")
        print(f"  Run 'source {rc_file}' or restart your shell.")
    except Exception as e:
        print(f"\n  [!] Could not write to {rc_file}: {e}")
        print(f"  Please add manually: {export_line}")


def _setup_project_symlinks():
    """Set up symlinks in Claude Code project memory directories."""
    claude_projects = Path.home() / ".claude" / "projects"
    if not claude_projects.exists():
        return
    for proj_dir in claude_projects.iterdir():
        if not proj_dir.is_dir():
            continue
        memory_dir = proj_dir / "memory"
        memory_dir.mkdir(parents=True, exist_ok=True)
        # Symlink MEMORY.md
        ensure_symlink(memory_dir / "MEMORY.md", MEMORY_MD_PATH)
        # Symlink working/
        ensure_symlink(memory_dir / "working", WORKING_DIR)


def _register_hooks():
    """Register Meme hooks in Claude Code settings.json."""
    settings_path = Path.home() / ".claude" / "settings.json"
    if not settings_path.exists():
        return

    try:
        settings = json.loads(settings_path.read_text())
    except json.JSONDecodeError:
        return

    hooks = settings.get("hooks", {})

    session_start_hook = {
        "matcher": "startup|resume|clear",
        "hooks": [{
            "type": "command",
            "command": str(BIN_DIR / "meme-session-start.sh"),
            "timeout": 10,
            "statusMessage": "Loading Meme working memory...",
        }],
    }

    query_hook = {
        "matcher": "*",
        "hooks": [{
            "type": "command",
            "command": str(BIN_DIR / "meme-query.sh"),
            "timeout": 15,
            "statusMessage": "Querying Meme...",
        }],
    }

    session_end_hook = {
        "matcher": "clear|logout|prompt_input_exit",
        "hooks": [{
            "type": "command",
            "command": str(BIN_DIR / "meme-session-end.sh"),
            "timeout": 30,
            "statusMessage": "Saving Meme session state...",
        }],
    }

    # Merge hooks (don't overwrite existing ones)
    for event, hook_config in [
        ("SessionStart", session_start_hook),
        ("UserPromptSubmit", query_hook),
        ("SessionEnd", session_end_hook),
    ]:
        existing = hooks.get(event, [])
        # Check if meme hook already registered
        meme_registered = any(
            any("meme" in h.get("command", "") for h in cfg.get("hooks", []))
            for cfg in existing
        )
        if not meme_registered:
            existing.append(hook_config)
        hooks[event] = existing

    settings["hooks"] = hooks
    settings_path.write_text(json.dumps(settings, indent=2, ensure_ascii=False))

    # --- Dream (launchd) setup ---
    dream_install = getattr(args, "dream", False)
    dream_reload = getattr(args, "dream_reload", False)
    if dream_install or dream_reload:
        import platform
        if platform.system() != "Darwin":
            print("Dream launchd setup is only supported on macOS.")
            print("Use cron on Linux: add '0 3 * * * meme dream' to your crontab")
        else:
            config = load_config()
            schedule = config.get("dream", {}).get("schedule", "0 3 * * *")
            plist_content = _generate_launchd_plist(schedule)
            launch_agents = Path.home() / "Library" / "LaunchAgents"
            launch_agents.mkdir(parents=True, exist_ok=True)
            plist_path = launch_agents / "com.meme.dream.plist"

            # Write plist
            plist_path.write_text(plist_content, encoding="utf-8")

            # Load/unload
            subprocess.run(["launchctl", "unload", str(plist_path)], capture_output=True)
            result = subprocess.run(["launchctl", "load", str(plist_path)], capture_output=True, text=True)
            if result.returncode == 0:
                print(f"Dream launchd job installed: {plist_path}")
                print(f"  Schedule: {schedule}")
                print(f"  Logs: {MEME_HOME}/dreams/dream.log")
            else:
                print(f"Failed to load launchd job: {result.stderr}")

    if not dream_install and not dream_reload and not already_installed:
        print("\nTip: Enable nightly dream consolidation with:")
        print("  meme setup --dream")

# ========================================
# Command: init
# ========================================

CLAUDE_MD_TEMPLATE = """# Meme — Project Memory System

This project uses [Meme](https://github.com/hyooeewee/Meme) for centralized memory management.

## Quick Reference

| Command | Purpose |
|---------|---------|
| `meme add "content"` | Add a new memory |
| `meme search "keyword"` | Search memories |
| `meme list` | List all memories |
| `meme edit <id>` | Edit a memory |
| `meme link <id_a> <id_b>` | Link two memories |

## Project Memory

- Project memory file: `~/.meme/archive/projects/{project_safe_name}.md`
- Working memories: `~/.meme/working/` (always loaded)
- Archive memories: `~/.meme/archive/` (graph traversal)

## For AI Assistants

When working in this project:
1. **SessionStart**: Working memories are auto-loaded via hook
2. **UserPromptSubmit**: Relevant archive memories are auto-injected via keyword search
3. **SessionEnd**: Access counts and heat are persisted

Use `[[mem_id]]` syntax to reference memories. Create links between related memories to build the knowledge graph.
"""


def cmd_init(args):
    """Initialize Meme integration in the current project directory."""
    if not MEME_HOME.exists():
        print("Meme is not set up yet. Run 'meme setup' first.")
        return

    project_name = Path.cwd().name
    safe_name = re.sub(r"[^\w-]", "_", project_name).lower()

    # 1. Create .claude/ directory
    claude_dir = Path.cwd() / ".claude"
    claude_dir.mkdir(exist_ok=True)

    # Ensure .claude/.gitignore exists (ignore all except memory/)
    gitignore = claude_dir / ".gitignore"
    if not gitignore.exists():
        gitignore.write_text("*\n", encoding="utf-8")

    # 2. Create .claude/memory/ and symlinks
    memory_dir = claude_dir / "memory"
    memory_dir.mkdir(exist_ok=True)
    ensure_symlink(memory_dir / "MEMORY.md", MEMORY_MD_PATH)
    ensure_symlink(memory_dir / "working", WORKING_DIR)

    # 3. Create/update CLAUDE.md
    claude_md = Path.cwd() / "CLAUDE.md"
    meme_section = CLAUDE_MD_TEMPLATE.format(project_safe_name=safe_name)

    if claude_md.exists():
        content = claude_md.read_text(encoding="utf-8")
        if "# Meme — Project Memory System" in content:
            print("  CLAUDE.md already has Meme section. Skipping.")
        else:
            content = content.rstrip() + "\n\n" + meme_section
            claude_md.write_text(content, encoding="utf-8")
            print("  Updated CLAUDE.md with Meme section.")
    else:
        claude_md.write_text(meme_section, encoding="utf-8")
        print("  Created CLAUDE.md with Meme guide.")

    # 4. Create project memory file
    project_mem_path = ARCHIVE_DIR / "projects" / f"{safe_name}.md"
    project_mem_path.parent.mkdir(parents=True, exist_ok=True)
    if not project_mem_path.exists():
        now = datetime.datetime.now().strftime("%Y-%m-%d")
        meta = {
            "id": f"mem_{datetime.datetime.now():%Y%m%d}_{safe_name}",
            "type": "project",
            "importance": 0.7,
            "created": now,
            "last_accessed": now,
            "access_count": 0,
            "tags": [safe_name],
            "links": [],
        }
        body = f"# {project_name}\n\nProject memory for {project_name}.\n\n## Overview\n\n## Notes\n\n## Related\n"
        save_memory(project_mem_path, meta, body)
        print(f"  Created project memory: {project_mem_path}")
    else:
        print(f"  Project memory already exists: {project_mem_path}")

    # 5. Register hooks (idempotent — safe to call even if already registered)
    _register_hooks()

    # 6. Rebuild MEMORY.md index
    rebuild_memory_md()

    print(f"\nMeme initialized for project '{project_name}'")
    print("  Run 'meme --help' for available commands.")


# ========================================
# Command: uninstall
# ========================================

def cmd_uninstall(args):
    """Uninstall the Meme system."""
    if not MEME_HOME.exists():
        print("Meme is not installed.")
        return

    # Remove hooks from settings.json
    settings_path = Path.home() / ".claude" / "settings.json"
    if settings_path.exists():
        try:
            settings = json.loads(settings_path.read_text())
            hooks = settings.get("hooks", {})
            for event in ["SessionStart", "UserPromptSubmit", "SessionEnd"]:
                if event in hooks:
                    hooks[event] = [
                        cfg for cfg in hooks[event]
                        if not any("meme" in h.get("command", "") for h in cfg.get("hooks", []))
                    ]
            settings["hooks"] = hooks
            settings_path.write_text(json.dumps(settings, indent=2, ensure_ascii=False))
        except Exception:
            pass

    # Remove project symlinks
    claude_projects = Path.home() / ".claude" / "projects"
    if claude_projects.exists():
        for proj_dir in claude_projects.iterdir():
            memory_dir = proj_dir / "memory"
            for link_name in ["MEMORY.md", "working"]:
                link = memory_dir / link_name
                if link.is_symlink():
                    link.unlink()

    # Remove Obsidian symlink if recorded
    try:
        vd = json.loads(VERSION_PATH.read_text())
        obsidian_path = vd.get("obsidian_path")
        if obsidian_path:
            meme_link = Path(obsidian_path) / "Meme"
            if meme_link.is_symlink():
                meme_link.unlink()
                print(f"  Removed Obsidian symlink: {meme_link}")
    except Exception:
        pass

    # Remove PATH entry from shell rc files
    _remove_path_entry()

    keep_data = getattr(args, "keep_data", False)
    if keep_data:
        print(f"Hooks and symlinks removed. Data preserved at {MEME_HOME}")
    else:
        shutil.rmtree(MEME_HOME)
        print(f"Meme completely removed from {MEME_HOME}")


def _remove_path_entry():
    """Remove meme PATH entry from shell rc files."""
    # Support both old (# Meme CLI) and new (# meme-memory-system) markers
    markers = ["# meme-memory-system", "# Meme CLI"]
    for rc_name in [".zshrc", ".bash_profile", ".profile"]:
        rc_file = Path.home() / rc_name
        if not rc_file.exists():
            continue
        try:
            lines = rc_file.read_text(encoding="utf-8").splitlines()
            new_lines = []
            skip_next = False
            for line in lines:
                if any(m in line for m in markers):
                    skip_next = True
                    continue
                if skip_next and line.strip().startswith("export PATH") and ".meme" in line:
                    skip_next = False
                    continue
                if skip_next and line.strip().startswith("set -gx PATH") and ".meme" in line:
                    skip_next = False
                    continue
                skip_next = False
                new_lines.append(line)
            rc_file.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
        except Exception:
            pass

