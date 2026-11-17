import tempfile
import unittest
from unittest.mock import patch
import json
from pathlib import Path

import fossic_api
import scrape


class ApiTests(unittest.TestCase):
    def row(self, allow=True):
        return {"mod_id": "demo", "mod_version": "2.0", "mod_update_date": 1790000000,
                "mod_allow_direct_download": allow,
                "thread_meta": {"tid": 1, "fid": 46, "title": "Demo"},
                "mod_releases": [
                    {"mod_version": "9.0", "game_version_id": "modVersion_097x",
                     "attachment_id": 1, "file_name": "old.zip", "download_url": "https://cdn.example/old.zip"},
                    {"mod_version": "2.0", "game_version_id": "modVersion_098x",
                     "attachment_id": 2, "file_name": "new.zip", "download_url": "https://cdn.example/new.zip"}]}

    def test_newest_game_and_public_permission(self):
        for allow in (True, False):
            rec = fossic_api.build_catalog({"mods": []}, {}, [self.row(allow)])["demo"]
            self.assertEqual(rec["version"], "2.0")
            self.assertEqual(rec["aid"], 2)
            self.assertEqual(bool(rec["download"]), allow)

    def test_override_and_missing_history(self):
        previous = {"legacy": {"id": "legacy", "version": "1"}}
        config = {"mods": [{"id": "addon", "thread": 1, "releaseIndex": 0}]}
        result = fossic_api.build_catalog(config, previous, [self.row()])
        self.assertEqual(result["addon"]["version"], "9.0")
        self.assertEqual(result["legacy"], previous["legacy"])

    def test_empty_index_rejected(self):
        with self.assertRaises(ValueError):
            fossic_api.build_catalog({"mods": []}, {}, [])

    def test_withdrawn_download_clears_existing_dl_file(self):
        rec = {"id": "demo", "version": "2", "origin": "repost", "source": "api",
               "thread": "https://www.fossic.org/thread-1-1-1.html", "title": "demo",
               "download": None}
        with tempfile.TemporaryDirectory() as folder, patch.object(scrape, "VERSIONS_DIR", folder):
            path = Path(folder) / "demo.dl.version"
            path.write_text('{"directDownloadURL":"https://example/old.zip"}')
            scrape.write_master({"rawBase": "https://example"}, "demo", rec)
            self.assertNotIn("directDownloadURL", json.loads(path.read_text()))


if __name__ == "__main__":
    unittest.main()
