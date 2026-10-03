import unittest
from pathlib import Path

from radar.settings import Settings


class SettingsTests(unittest.TestCase):
    def test_reads_spawn_env(self):
        s = Settings.from_env(
            {
                "PORT": "6123",
                "KIROCREW_APP_NAME": "integration-radar",
                "KIROCREW_HOME": "/tmp/crew",
                "KIROCREW_PROXY_SECRET": "s",
                "KIROCREW_GATEWAY_ORIGIN": "http://127.0.0.1:7000/",
            }
        )
        self.assertEqual(s.port, 6123)
        self.assertEqual(s.proxy_secret, "s")
        self.assertEqual(s.gateway_origin, "http://127.0.0.1:7000")
        self.assertEqual(s.gateway_port, "7000")
        self.assertEqual(s.data_dir, Path("/tmp/crew/apps/integration-radar/data"))
        self.assertEqual(s.app_secret_path, Path("/tmp/crew/apps/integration-radar/.app_secret"))

    def test_gateway_origin_falls_back_to_kirocrew_port(self):
        s = Settings.from_env({"KIROCREW_HOME": "/tmp/crew", "KIROCREW_PORT": "5999"})
        self.assertEqual(s.gateway_origin, "http://127.0.0.1:5999")

    def test_gateway_origin_defaults_to_5476(self):
        s = Settings.from_env({"KIROCREW_HOME": "/tmp/crew"})
        self.assertEqual(s.gateway_origin, "http://127.0.0.1:5476")
        self.assertEqual(s.proxy_secret, "")
        self.assertEqual(s.port, 0)


if __name__ == "__main__":
    unittest.main()
