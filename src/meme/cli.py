"""CLI argument parser and main entry point."""
import argparse
import sys

from meme import __version__ as CURRENT_VERSION
from meme.commands.setup import cmd_setup, cmd_init, cmd_uninstall
from meme.commands.memory import (
    cmd_add, cmd_list, cmd_show, cmd_search, cmd_query,
    cmd_edit, cmd_delete, cmd_forget,
)
from meme.commands.ingest import cmd_learn, cmd_import
from meme.commands.lifecycle import cmd_decay, cmd_promote, cmd_demote, cmd_warm
from meme.commands.links import cmd_link, cmd_suggest_links, cmd_daydream, cmd_config, cmd_dream
from meme.commands.maintenance import (
    cmd_doctor, cmd_backup, cmd_gc, cmd_reindex,
    cmd_stats, cmd_export,
)
from meme.commands.system import cmd_version, cmd_upgrade, cmd_changelog, cmd_auth, cmd_run, cmd_heat


_EPILOG = """\n\
examples:
  meme add "Use uv for Python deps" --type feedback --importance 0.8 --tags python,uv
  meme search "docker permission"
  meme query mem_xxx
  meme config --set dream.enabled=false
  meme daydream --dry-run
  meme doctor --fix
  meme upgrade --check

command groups:
  Memory Management    add, list, show, search, query, edit, delete, forget
  Ingestion            learn, import
  Lifecycle            decay, promote, demote, warm, link, suggest-links, daydream, dream
  Maintenance          doctor, backup, gc, reindex, stats, export, heat
  Setup                setup, init, uninstall
  System               version, upgrade, changelog, auth, run, config

Use 'meme <command> --help' for detailed usage of a command."""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="meme",
        description="Meme — A centralized, tiered memory system with knowledge graph.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=_EPILOG,
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {CURRENT_VERSION}",
    )
    sub = parser.add_subparsers(dest="command", title="commands", metavar="COMMAND")

    # setup
    p = sub.add_parser("setup", help="Set up the Meme system")
    p.add_argument("--migrate", action="store_true", help="Also import from Claude Code")
    p.add_argument("--obsidian", type=str, help="Path to Obsidian vault for symlink")
    p.add_argument("--dev", action="store_true", help="Symlink hook scripts instead of copying (for local development)")
    p.add_argument("--dream", action="store_true", help="Install launchd plist for nightly dream consolidation (macOS)")
    p.add_argument("--dream-reload", action="store_true", help="Reload launchd job after config changes")
    p.set_defaults(func=cmd_setup)

    # init
    p = sub.add_parser("init", help="Init Meme in the current project (CLAUDE.md + .claude/)")
    p.set_defaults(func=cmd_init)

    # uninstall
    p = sub.add_parser("uninstall", help="Uninstall Meme")
    p.add_argument("--keep-data", action="store_true", help="Keep ~/.meme/ data")
    p.set_defaults(func=cmd_uninstall)

    # add
    p = sub.add_parser("add", help="Add a new memory")
    p.add_argument("content", help="Memory content")
    p.add_argument("--type", "-t", default="feedback",
                   choices=["feedback", "project", "user", "reference", "knowledge", "correction"],
                   help="Memory type (default: feedback)")
    p.add_argument("--importance", "-i", type=float, default=0.6,
                   help="Importance 0.0~1.0 (default: 0.6)")
    p.add_argument("--tags", default="", help="Comma-separated tags")
    p.add_argument("--links", default="", help="Comma-linked memory IDs")
    p.add_argument("--slug", default="", help="URL-friendly slug for the ID")
    p.add_argument("--sensitive", action="store_true", help="Encrypt and store in vault")
    p.add_argument("--source-url", default=None, help="Source URL")
    p.add_argument("--source-file", default=None, help="Source file path")
    p.add_argument("--corrects", default=None, help="ID of memory this corrects")
    p.add_argument("--scope", default=None, help="Correction scope (e.g. project name)")
    p.add_argument("--wrong-pattern", default=None, help="Pattern that was wrong")
    p.add_argument("--correct-pattern", default=None, help="Pattern that is correct")
    p.set_defaults(func=cmd_add)

    # list
    p = sub.add_parser("list", help="List memories")
    p.add_argument("--tier", choices=["working", "archive", "cold"],
                   help="Filter by tier")
    p.add_argument("--tag", default=None, help="Filter by tag")
    p.add_argument("--sort", default="importance", choices=["importance", "recent", "heat"],
                   help="Sort order (default: importance)")
    p.add_argument("--forgotten", action="store_true", help="Include forgotten memories")
    p.add_argument("--format", default="text", choices=["text", "json"],
                   help="Output format (default: text)")
    p.set_defaults(func=cmd_list)

    # show
    p = sub.add_parser("show", help="Show a memory's full content")
    p.add_argument("id", help="Memory ID")
    p.set_defaults(func=cmd_show)

    # search
    p = sub.add_parser("search", help="Search memories by keyword")
    p.add_argument("query", help="Search query")
    p.add_argument("--format", default="text", choices=["text", "json"],
                   help="Output format (default: text)")
    p.set_defaults(func=cmd_search)

    # query
    p = sub.add_parser("query", help="Graph traversal retrieval")
    p.add_argument("id", help="Memory ID to start traversal from")
    p.set_defaults(func=cmd_query)

    # edit
    p = sub.add_parser("edit", help="Edit a memory")
    p.add_argument("id", help="Memory ID")
    p.add_argument("--content", default=None, help="New content")
    p.add_argument("--importance", type=float, default=None, help="New importance")
    p.add_argument("--type", default=None, help="New type")
    p.add_argument("--tags", default=None, help="Replace tags (comma-separated)")
    p.add_argument("--add-link", default=None, help="Add a link to another memory ID")
    p.set_defaults(func=cmd_edit)

    # delete
    p = sub.add_parser("delete", help="Delete a memory")
    p.add_argument("id", help="Memory ID")
    p.add_argument("--force", "-f", action="store_true", help="Skip confirmation")
    p.set_defaults(func=cmd_delete)

    # forget
    p = sub.add_parser("forget", help="Forget a memory")
    p.add_argument("id", help="Memory ID")
    p.add_argument("--hard", action="store_true", help="Hard delete from filesystem")
    p.add_argument("--purge", action="store_true", help="Purge from git history")
    p.add_argument("--reason", default=None, help="Reason for forgetting")
    p.set_defaults(func=cmd_forget)

    # learn
    p = sub.add_parser("learn", help="Learn from URL or file")
    p.add_argument("url", nargs="?", default=None, help="URL to learn from")
    p.add_argument("--url", dest="url_flag", default=None, help="URL to learn from (alternative)")
    p.add_argument("--file", default=None, help="Local file to learn from")
    p.add_argument("--slug", default="", help="URL-friendly slug")
    p.add_argument("--importance", type=float, default=0.5, help="Importance (default: 0.5)")
    p.add_argument("--tags", default="", help="Comma-separated tags")
    p.set_defaults(func=cmd_learn)

    # import
    p = sub.add_parser("import", help="Import memories from external sources")
    p.add_argument("source", nargs="+", choices=["claude", "claude-global", "codex"],
                   help="Source to import from")
    p.add_argument("--path", default=None, help="Codex workspace path")
    p.set_defaults(func=cmd_import)

    # decay
    p = sub.add_parser("decay", help="Run importance decay scan")
    p.add_argument("--dry-run", action="store_true", help="Preview without applying changes")
    p.set_defaults(func=cmd_decay)

    # promote
    p = sub.add_parser("promote", help="Promote a memory to working tier")
    p.add_argument("id", help="Memory ID")
    p.set_defaults(func=cmd_promote)

    # demote
    p = sub.add_parser("demote", help="Demote a memory")
    p.add_argument("id", help="Memory ID")
    p.add_argument("--importance", type=float, default=None, help="Target importance")
    p.set_defaults(func=cmd_demote)

    # warm
    p = sub.add_parser("warm", help="Warm a cold memory to archive")
    p.add_argument("id", help="Memory ID")
    p.set_defaults(func=cmd_warm)

    # link
    p = sub.add_parser("link", help="Link two memories")
    p.add_argument("id_a", help="First memory ID")
    p.add_argument("id_b", help="Second memory ID")
    p.set_defaults(func=cmd_link)

    # suggest-links
    p = sub.add_parser("suggest-links", help="Suggest new links")
    p.set_defaults(func=cmd_suggest_links)

    # daydream
    p = sub.add_parser("daydream", help="Semantic clustering and link consolidation")
    p.add_argument("--dry-run", action="store_true", help="Preview without applying changes")
    p.add_argument("--mode", choices=["all", "cluster", "link"], default="all",
                   help="Run mode (default: all)")
    p.add_argument("--threshold", type=float, default=0.4,
                   help="Similarity threshold for clustering (default: 0.4)")
    p.add_argument("--apply", action="store_true",
                   help="Apply suggested links automatically")
    p.set_defaults(func=cmd_daydream)

    # config
    p = sub.add_parser("config", help="View or modify configuration")
    p.add_argument("--get", default=None, help="Get a config value by dot path (e.g. dream.enabled)")
    p.add_argument("--set", default=None, help="Set a config value (e.g. dream.enabled=false)")
    p.add_argument("--edit", action="store_true", help="Open config in $EDITOR")
    p.set_defaults(func=cmd_config)

    # dream
    p = sub.add_parser("dream", help="Run automated memory consolidation (night mode)")
    p.set_defaults(func=cmd_dream)

    # doctor
    p = sub.add_parser("doctor", help="Health check")
    p.add_argument("--fix", action="store_true", help="Auto-fix issues")
    p.add_argument("--ask", action="store_true", help="Confirm each fix")
    p.set_defaults(func=cmd_doctor)

    # backup
    p = sub.add_parser("backup", help="Create a backup")
    p.set_defaults(func=cmd_backup)

    # gc
    p = sub.add_parser("gc", help="Clean old backups")
    p.set_defaults(func=cmd_gc)

    # reindex
    p = sub.add_parser("reindex", help="Rebuild index and graph")
    p.set_defaults(func=cmd_reindex)

    # stats
    p = sub.add_parser("stats", help="Show statistics")
    p.set_defaults(func=cmd_stats)

    # export
    p = sub.add_parser("export", help="Export all memories")
    p.add_argument("--format", default="json", choices=["json", "md"],
                   help="Export format (default: json)")
    p.add_argument("--output", "-o", default=None, help="Output file path")
    p.set_defaults(func=cmd_export)

    # heat
    p = sub.add_parser("heat", help="Show session heat map")
    p.set_defaults(func=cmd_heat)

    # auth
    p = sub.add_parser("auth", help="Authenticate and export a vault secret")
    p.add_argument("mem_id", help="Vault memory ID to authenticate")
    p.add_argument("--var", default=None,
                   help="Environment variable name (default: MEM_SECRET)")
    p.set_defaults(func=cmd_auth)

    # run
    p = sub.add_parser("run", help="Run a command with a vault secret as env var")
    p.add_argument("mem_id", help="Vault memory ID")
    p.add_argument("--var", default=None,
                   help="Environment variable name (default: MEM_SECRET)")
    p.add_argument("cmd", nargs="*",
                   help="Command to execute (after --)")
    p.set_defaults(func=cmd_run)

    # version
    p = sub.add_parser("version", help="Show version")
    p.set_defaults(func=cmd_version)

    # upgrade
    p = sub.add_parser("upgrade", help="Upgrade Meme")
    p.add_argument("--check", action="store_true", help="Check for updates only")
    p.add_argument("--force", action="store_true", help="Force reinstall current version")
    p.set_defaults(func=cmd_upgrade)

    # changelog
    p = sub.add_parser("changelog", help="Show changelog")
    p.set_defaults(func=cmd_changelog)

    return parser

# ========================================
# Main
# ========================================

def main():
    parser = build_parser()
    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return

    if hasattr(args, "func"):
        args.func(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
