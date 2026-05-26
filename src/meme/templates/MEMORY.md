# Meme — Memory Index

## Working Memory (always loaded)
<!-- Memories with importance >= 0.8 are auto-loaded each session -->

## Archive Index (graph traversal retrieval)
<!-- Use `meme search "keyword"` or `meme query mem_id` to retrieve -->

## Query Guide
When you need to recall something not in Working Memory:
1. Search archive by keyword: `meme search "keyword"`
2. Load the hit file, then follow its `[[links]]` for context
3. 1st-degree links: full load. 2nd-degree: title only. 3rd+: ignore.

## Commands
- `meme add "content" --type feedback` — Add a new memory
- `meme search "keyword"` — Search all memories
- `meme query mem_id` — Graph traversal from a memory node
- `meme list` — List all memories
- `meme learn <url>` — Learn from a URL
- `meme doctor` — Health check
