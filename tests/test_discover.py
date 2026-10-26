import os
import shutil
import tempfile
import unittest

from imefuse import discover


class DetectionTest(unittest.TestCase):
    """Every case runs against a simulated Mac via IMEFUSE_HOME / IMEFUSE_APP_DIRS."""

    def setUp(self):
        self.home = tempfile.mkdtemp(prefix="imefuse-home-")
        self.apps = tempfile.mkdtemp(prefix="imefuse-apps-")
        self._env = {k: os.environ.get(k) for k in ("IMEFUSE_HOME", "IMEFUSE_APP_DIRS")}
        os.environ["IMEFUSE_HOME"] = self.home
        os.environ["IMEFUSE_APP_DIRS"] = self.apps

    def tearDown(self):
        for key, value in self._env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        shutil.rmtree(self.home, ignore_errors=True)
        shutil.rmtree(self.apps, ignore_errors=True)

    def _install(self, ime, version="1.0"):
        meta = discover.BY_NAME[ime]
        app = os.path.join(self.apps, meta["app_names"][0])
        os.makedirs(os.path.join(app, "Contents"), exist_ok=True)
        with open(os.path.join(app, "Contents", "Info.plist"), "w") as fh:
            fh.write(
                '<?xml version="1.0" encoding="UTF-8"?>\n'
                '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" '
                '"http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
                '<plist version="1.0"><dict>'
                "<key>CFBundleIdentifier</key><string>%s</string>"
                "<key>CFBundleShortVersionString</key><string>%s</string>"
                "</dict></plist>" % (meta["bundle_id"], version))
        return app

    def _make_data(self, ime):
        root = discover.data_root(ime)
        os.makedirs(root, exist_ok=True)
        return root

    def test_nothing_installed(self):
        rows = {r["ime"]: r for r in discover.probe_all()}
        for row in rows.values():
            self.assertEqual(row["state"], "missing")
            self.assertFalse(row["installed"])
            self.assertFalse(row["running"])
        self.assertEqual(discover.usable(), [])

    def test_installed_but_no_data(self):
        for ime in ("wetype", "sogou", "doubao"):
            self._install(ime, "9.9.9")
        rows = {r["ime"]: r for r in discover.probe_all()}
        for row in rows.values():
            self.assertTrue(row["installed"])
            self.assertEqual(row["state"], "no-data")
            self.assertEqual(row["version"], "9.9.9")
        self.assertEqual(discover.usable(), [])

    def test_wrong_bundle_id_is_ignored(self):
        app = self._install("wetype")
        with open(os.path.join(app, "Contents", "Info.plist"), "w") as fh:
            fh.write(
                '<?xml version="1.0" encoding="UTF-8"?>\n'
                '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" '
                '"http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
                '<plist version="1.0"><dict>'
                "<key>CFBundleIdentifier</key><string>com.example.notwetype</string>"
                "</dict></plist>")
        row = discover.probe("wetype")
        self.assertFalse(row["installed"])
        self.assertEqual(row["state"], "missing")

    def test_data_root_is_under_the_override_home(self):
        self._make_data("sogou")
        self.assertTrue(discover.data_root("sogou").startswith(self.home))

    def test_probe_never_raises_on_broken_store(self):
        self._install("doubao")
        root = self._make_data("doubao")
        bad = os.path.join(root, "EngineUserDictAccounts", "deadbeef")
        os.makedirs(bad)
        with open(os.path.join(bad, "usr.dat"), "wb") as fh:
            fh.write(b"not a doubao container at all")
        row = discover.probe("doubao")
        self.assertIn(row["state"], ("unknown-format", "error", "no-data"))
        self.assertIsNotNone(row["detail"])


if __name__ == "__main__":
    unittest.main()
