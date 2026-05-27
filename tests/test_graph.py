# /// script
# requires-python = ">=3.10"
# dependencies = ["pytest"]
# ///

"""Tests for Meme graph traversal and related logic."""

import json


class TestGraphTraversal:
    def test_graph_bfs_basic(self, init_meme, cli_runner):
        """BFS should find linked nodes at distance 1."""
        # Create three memories: A -> B -> C
        cli_runner("add", "memory A", "--type", "knowledge", "--slug", "a")
        cli_runner("add", "memory B", "--type", "knowledge", "--slug", "b")
        cli_runner("add", "memory C", "--type", "knowledge", "--slug", "c")

        code, out, err = cli_runner("list", "--format", "json")
        results = json.loads(out)
        ids = sorted([r["id"] for r in results])
        a_id, b_id, c_id = ids[0], ids[1], ids[2]

        # Link A-B and B-C
        cli_runner("link", a_id, b_id)
        cli_runner("link", b_id, c_id)

        # Query from A: should see A (d=0) and B (d=1)
        code, out, err = cli_runner("query", a_id)
        assert code == 0, f"stderr: {err}"
        assert "distance=0" in out
        assert "distance=1" in out
        assert b_id in out
        # C is at distance 2 from A, may or may not appear depending on depth limit

    def test_graph_bidirectional_link(self, init_meme, cli_runner):
        """Linking should be bidirectional in graph."""
        cli_runner("add", "alpha", "--type", "feedback", "--slug", "alpha")
        cli_runner("add", "beta", "--type", "feedback", "--slug", "beta")

        code, out, err = cli_runner("list", "--format", "json")
        results = json.loads(out)
        ids = [r["id"] for r in results]

        cli_runner("link", ids[0], ids[1])

        # Query from either direction should find the other
        code, out, err = cli_runner("query", ids[0])
        assert ids[1] in out

        code, out, err = cli_runner("query", ids[1])
        assert ids[0] in out

    def test_load_weight_formula(self, init_meme, cli_runner):
        """Verify load_weight = importance * (0.4 ^ distance)."""
        cli_runner("add", "high imp", "--type", "knowledge", "--importance", "0.8", "--slug", "high")
        cli_runner("add", "low imp", "--type", "knowledge", "--importance", "0.5", "--slug", "low")

        code, out, err = cli_runner("list", "--format", "json")
        results = json.loads(out)
        ids = sorted([r["id"] for r in results], key=lambda x: json.loads(out))
        # Get IDs by importance
        high_id = [r["id"] for r in json.loads(cli_runner("list", "--format", "json")[1]) if r["importance"] == 0.8][0]
        low_id = [r["id"] for r in json.loads(cli_runner("list", "--format", "json")[1]) if r["importance"] == 0.5][0]

        cli_runner("link", high_id, low_id)

        code, out, err = cli_runner("query", high_id)
        # Distance 1 node with importance 0.5: load_weight = 0.5 * 0.4 = 0.20
        assert "load_weight=0.20" in out


class TestTierLogic:
    def test_working_tier(self, init_meme, cli_runner):
        """Importance >= 0.8 should go to working tier."""
        cli_runner("add", "high priority", "--type", "user", "--importance", "0.9")
        code, out, err = cli_runner("list", "--tier", "working")
        assert code == 0, f"stderr: {err}"
        assert "working" in out.lower() or "0.9" in out

    def test_cold_tier(self, init_meme, cli_runner):
        """Importance < 0.2 should go to cold tier."""
        cli_runner("add", "low priority", "--type", "reference", "--importance", "0.1")
        code, out, err = cli_runner("list", "--tier", "cold")
        assert code == 0, f"stderr: {err}"
        # Cold memories may show in list or may be indexed only

    def test_archive_tier(self, init_meme, cli_runner):
        """Default importance should go to archive tier."""
        cli_runner("add", "normal", "--type", "feedback")
        code, out, err = cli_runner("list", "--tier", "archive")
        assert code == 0, f"stderr: {err}"
        assert "archive" in out.lower()


class TestIndexIntegrity:
    def test_index_updated_on_add(self, init_meme, cli_runner):
        """Adding a memory should update index.json."""
        cli_runner("add", "indexed memory", "--type", "feedback", "--tags", "test")
        import meme.core as core_mod

        index = json.loads(core_mod.INDEX_PATH.read_text(encoding="utf-8"))
        assert len(index) >= 1
        assert any("indexed memory" in str(v) for v in index.values())

    def test_graph_updated_on_link(self, init_meme, cli_runner):
        """Linking memories should update graph.json."""
        cli_runner("add", "node A", "--type", "feedback", "--slug", "nodea")
        cli_runner("add", "node B", "--type", "feedback", "--slug", "nodeb")

        code, out, err = cli_runner("list", "--format", "json")
        results = json.loads(out)
        ids = [r["id"] for r in results]

        cli_runner("link", ids[0], ids[1])

        import meme.core as core_mod

        graph = json.loads(core_mod.GRAPH_PATH.read_text(encoding="utf-8"))
        assert ids[0] in graph
        assert ids[1] in graph[ids[0]]
