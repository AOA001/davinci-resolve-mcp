"""Skills over MCP: the server serves skill folders it is pointed at, in place."""
import asyncio
import os
import tempfile
import unittest
from unittest import mock

from src.utils.mcp_sdk import IS_FASTMCP4, FastMCP, add_skill_directories


def _skill(root, name, description="does a thing"):
    folder = os.path.join(root, name)
    os.makedirs(folder)
    with open(os.path.join(folder, "SKILL.md"), "w") as fh:
        fh.write(f"---\nname: {name}\ndescription: {description}\n---\nbody\n")
    return folder


def _served(server):
    return sorted(str(r.uri) for r in asyncio.run(server.list_resources())
                  if str(r.uri).endswith("/SKILL.md"))


@unittest.skipUnless(IS_FASTMCP4, "skills are served on FastMCP 4.2+ only")
class SkillSourcesTest(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.good = _skill(self.root, "good-one")
        self.picked = _skill(self.root, "picked")
        _skill(self.root, "bad-one", description="v1.0: broken: yes")

    def test_a_folder_of_skills_skips_the_invalid_one(self):
        server = FastMCP("t")
        self.assertEqual(add_skill_directories(server, [self.root]), 2)
        self.assertEqual(_served(server), ["skill://good-one/SKILL.md", "skill://picked/SKILL.md"])

    def test_one_skill_folder_serves_only_that_skill(self):
        server = FastMCP("t")
        self.assertEqual(add_skill_directories(server, [self.picked]), 1)
        self.assertEqual(_served(server), ["skill://picked/SKILL.md"])

    def test_edits_go_live_without_a_restart(self):
        server = FastMCP("t")
        add_skill_directories(server, [self.root])
        _skill(self.root, "added-later")
        self.assertIn("skill://added-later/SKILL.md", _served(server))

    def test_a_single_skill_entry_never_scans_its_parent(self):
        from fastmcp.skills import provider as _skills

        with mock.patch.object(_skills, "_scan_skill_roots",
                               side_effect=AssertionError("scanned the parent")):
            server = FastMCP("t")
            self.assertEqual(add_skill_directories(server, [self.good, self.picked]), 2)
            self.assertEqual(_served(server), ["skill://good-one/SKILL.md", "skill://picked/SKILL.md"])

    def test_an_invalid_listed_skill_is_logged_not_dropped_silently(self):
        logger = mock.Mock()
        bad = os.path.join(self.root, "bad-one")
        self.assertEqual(add_skill_directories(FastMCP("t"), [bad, self.picked], logger), 1)
        self.assertIn("bad-one", logger.warning.call_args_list[0].args[0])

    def test_missing_entries_are_ignored(self):
        self.assertEqual(add_skill_directories(FastMCP("t"), ["/nope", ""]), 0)


class SkillRootsTest(unittest.TestCase):
    def test_list_file_and_dirs_are_read(self):
        import src.server as s

        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as fh:
            fh.write("# comment\n/a/skill folder\n\n")
        env = {"DAVINCI_MCP_SKILLS_FILE": fh.name, "DAVINCI_MCP_SKILLS_DIRS": "/b" + os.pathsep + "/c"}
        with mock.patch.dict(os.environ, env):
            roots = s._skill_roots()
        self.assertEqual(roots[1:], ["/b", "/c", "/a/skill folder"])

    def test_off_switch(self):
        import src.server as s

        with mock.patch.dict(os.environ, {"DAVINCI_MCP_SKILLS": "0"}):
            self.assertEqual(s._skill_roots(), [])


if __name__ == "__main__":
    unittest.main()
