# Meme Roadmap

## High Priority

### 1. Test Coverage
- [ ] tests/test_config.py — config read/write, merge, type coercion
- [ ] tests/test_utils.py — frontmatter parse/render, file discovery
- [ ] tests/test_commands.py — CLI dry-run modes, edge cases
- [ ] tests/test_vault.py — encrypt/decrypt round-trip

### 2. Type Checking
- [ ] Add `[tool.mypy]` to pyproject.toml
- [ ] Run mypy in CI
- [ ] Fix existing type annotations

### 3. CI Enhancement
- [ ] `.github/workflows/ci.yaml` — ruff, black, mypy, pytest
- [ ] PR checks before merge

## Medium Priority

### 7. Config Schema Validation
- [ ] Define schema with dataclasses or pydantic
- [ ] Validate on load_config()
- [ ] Provide clear error messages for invalid values

## Low Priority

### 9. Index Performance
- [ ] Evaluate SQLite for meta/index.json and graph.json
- [ ] Benchmark with 1000+ memories

### 10. Internationalization
- [ ] CLI output supports LANG=zh_CN
- [ ] Error messages and help text i18n
