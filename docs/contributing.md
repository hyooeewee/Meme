# Contributing to Meme

## Adding a New Command

1. **Create the command function** in the appropriate `commands/*.py` module:
   ```python
   def cmd_mycmd(args):
       """Brief description for help text."""
       ...
   ```

2. **Register in `cli.py`**:
   ```python
   from meme.commands.memory import cmd_mycmd  # or appropriate module
   
   # In build_parser():
   p = sub.add_parser("mycmd", help="Brief description")
   p.add_argument("--flag", action="store_true", help="Flag help")
   p.set_defaults(func=cmd_mycmd)
   ```

3. **Export from `core.py`** (for backward compatibility):
   ```python
   from meme.commands.memory import cmd_mycmd
   ```

4. **Add tests** in `tests/test_commands.py`

## Module Responsibilities

| Module | What goes here |
|--------|---------------|
| `constants.py` | Paths, thresholds, enums — no logic |
| `config.py` | TOML read/write, schema defaults |
| `utils.py` | Reusable utilities: frontmatter, git, file ops |
| `vault.py` | Encryption/decryption only |
| `log.py` | Logging configuration |
| `commands/*.py` | CLI command implementations |
| `cli.py` | Parser registration + main() only |
| `core.py` | Re-export layer (do not add logic here) |

## Code Style

- Type annotations on public functions
- English comments and docstrings
- 40-char section dividers:
  ```python
  # ========================================
  # Section Name
  # ========================================
  ```

## Testing

```bash
uv run pytest tests/ -v
```

Run a single test:
```bash
uv run pytest tests/test_config.py -v
```
