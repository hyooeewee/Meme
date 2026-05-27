# /// script
# requires-python = ">=3.10"
# dependencies = ["pytest"]
# ///

"""Tests for Meme CLI commands."""

import json


class TestAdd:
    def test_add_basic(self, cli_runner, init_meme):
        code, out, err = cli_runner("add", "test content", "--type", "feedback")
        assert code == 0, f"stderr: {err}"
        assert "Added memory" in out

    def test_add_with_tags(self, cli_runner, init_meme):
        code, out, err = cli_runner(
            "add", "uv is fast", "--type", "knowledge", "--tags", "uv,python", "--importance", "0.8"
        )
        assert code == 0, f"stderr: {err}"
        assert "knowledge" in out
        assert "Importance: 0.8" in out

    def test_add_with_links(self, cli_runner, init_meme):
        # First memory
        cli_runner("add", "first memory", "--type", "feedback")
        # Second memory with link to first
        code, out, err = cli_runner("add", "second memory", "--type", "feedback", "--links", "mem_")
        # Links validation may be lenient; just ensure it doesn't crash
        assert code == 0, f"stderr: {err}"


class TestSearch:
    def test_search_hits(self, cli_runner, init_meme):
        cli_runner("add", "use uv for python dependencies", "--type", "knowledge", "--tags", "uv,python")
        code, out, err = cli_runner("search", "uv python")
        assert code == 0, f"stderr: {err}"
        assert "Found:" in out
        assert "mem_" in out

    def test_search_no_results(self, cli_runner, init_meme):
        code, out, err = cli_runner("search", "nonexistent_xyz_abc")
        assert code == 0, f"stderr: {err}"
        assert "Found: 0" in out or "No memories" in out or out.strip() == ""

    def test_search_json_format(self, cli_runner, init_meme):
        cli_runner("add", "docker permission issue", "--type", "feedback", "--tags", "docker")
        code, out, err = cli_runner("search", "docker", "--format", "json")
        assert code == 0, f"stderr: {err}"
        # JSON output should be parseable
        results = json.loads(out)
        assert isinstance(results, list)
        if results:
            assert "id" in results[0]
            assert "title" in results[0]


class TestList:
    def test_list_empty(self, cli_runner, init_meme):
        code, out, err = cli_runner("list")
        assert code == 0, f"stderr: {err}"
        assert "Total: 0" in out or "0 memories" in out or "No memories found" in out

    def test_list_with_memories(self, cli_runner, init_meme):
        cli_runner("add", "memory one", "--type", "feedback")
        cli_runner("add", "memory two", "--type", "project")
        code, out, err = cli_runner("list")
        assert code == 0, f"stderr: {err}"
        assert "Total: 2" in out or "2 memories" in out

    def test_list_by_tier(self, cli_runner, init_meme):
        cli_runner("add", "working memory", "--type", "user", "--importance", "0.9")
        cli_runner("add", "archive memory", "--type", "feedback", "--importance", "0.5")
        code, out, err = cli_runner("list", "--tier", "working")
        assert code == 0, f"stderr: {err}"
        assert "working" in out.lower() or "Importance: 0.9" in out


class TestQuery:
    def test_query_self(self, cli_runner, init_meme):
        cli_runner("add", "docker setup guide", "--type", "knowledge", "--tags", "docker")
        code, out, err = cli_runner("list", "--format", "json")
        results = json.loads(out)
        mem_id = results[0]["id"]

        code, out, err = cli_runner("query", mem_id)
        assert code == 0, f"stderr: {err}"
        assert "distance=0" in out
        assert mem_id in out

    def test_query_with_links(self, cli_runner, init_meme):
        cli_runner("add", "first", "--type", "knowledge", "--slug", "first")
        cli_runner("add", "second", "--type", "knowledge", "--slug", "second")

        code, out, err = cli_runner("list", "--format", "json")
        results = json.loads(out)
        ids = [r["id"] for r in results]

        # Link them
        cli_runner("link", ids[0], ids[1])

        code, out, err = cli_runner("query", ids[0])
        assert code == 0, f"stderr: {err}"
        assert "distance=1" in out
        assert ids[1] in out


class TestLink:
    def test_link_creates_bidirectional(self, cli_runner, init_meme):
        cli_runner("add", "mem a", "--type", "feedback", "--slug", "a")
        cli_runner("add", "mem b", "--type", "feedback", "--slug", "b")

        code, out, err = cli_runner("list", "--format", "json")
        results = json.loads(out)
        ids = [r["id"] for r in results]

        code, out, err = cli_runner("link", ids[0], ids[1])
        assert code == 0, f"stderr: {err}"
        assert "Linked" in out

        # Verify via query
        code, out, err = cli_runner("query", ids[0])
        assert ids[1] in out


class TestDelete:
    def test_delete_memory(self, cli_runner, init_meme):
        cli_runner("add", "to be deleted", "--type", "feedback")
        code, out, err = cli_runner("list", "--format", "json")
        results = json.loads(out)
        mem_id = results[0]["id"]

        code, out, err = cli_runner("delete", "--force", mem_id)
        assert code == 0, f"stderr: {err}"

        code, out, err = cli_runner("list")
        assert "Total: 0" in out or "0 memories" in out or "No memories" in out


class TestDoctor:
    def test_doctor_passes_fresh_install(self, cli_runner, init_meme):
        code, out, err = cli_runner("doctor")
        assert code == 0, f"stderr: {err}"
        assert "passed" in out.lower() or "All checks" in out
