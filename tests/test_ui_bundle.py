"""The built UI ships with the app, so it must exist and be current.

Crew skips ``setup.onInstall`` for an app that is trusted after it is installed
(the normal order), so nothing builds the UI on the user's machine.
"""

import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
UI = ROOT / "ui"
DIST = UI / "dist"
HOST_MODULES = {"react", "react/jsx-runtime", "lucide-react", "@kirocrew/app-sdk", "@kirocrew/app-sdk/ui"}
#: Checkout and copy tools can leave sources a moment newer than the bundle.
TOLERANCE_SECONDS = 5


class UiBundleTests(unittest.TestCase):
    def test_bundle_and_icon_exist(self):
        manifest = json.loads((ROOT / "app.json").read_text())
        self.assertTrue((UI / manifest["ui"]["entry"]).is_file(), "run `make ui` and commit ui/dist")
        self.assertTrue((UI / manifest["ui"]["pages"][0]["iconUrl"]).is_file())
        self.assertTrue((ROOT / manifest["iconPath"]).is_file())

    def test_bundle_is_not_older_than_its_sources(self):
        bundle = (DIST / "index.mjs").stat().st_mtime
        sources = [p for p in (UI / "src").rglob("*") if p.is_file()]
        sources += [p for p in (UI / "public").rglob("*") if p.is_file()]
        sources += [UI / n for n in ("vite.config.ts", "package.json", "tsconfig.json")]
        stale = [str(p.relative_to(ROOT)) for p in sources if p.stat().st_mtime > bundle + TOLERANCE_SECONDS]
        self.assertEqual(stale, [], "ui/dist is older than these sources; run `make ui` and commit ui/dist")

    def test_bundle_imports_only_host_modules(self):
        text = (DIST / "index.mjs").read_text()
        imported = set(re.findall(r'from\s*["\']([^"\']+)["\']', text))
        self.assertTrue(imported)
        self.assertLessEqual(imported, HOST_MODULES, f"bundled imports the host does not provide: {imported - HOST_MODULES}")

    def test_no_install_time_build(self):
        manifest = json.loads((ROOT / "app.json").read_text())
        self.assertNotIn("onInstall", manifest.get("setup", {}))


if __name__ == "__main__":
    unittest.main()
