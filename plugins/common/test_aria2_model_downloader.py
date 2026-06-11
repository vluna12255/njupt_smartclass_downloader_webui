import os
import unittest
import urllib.error
from unittest import mock

from aria2_model_downloader import (
    Aria2ModelDownloadError,
    PluginAria2ProcessManager,
    check_model_source_connectivity,
    system_proxy_url,
)


class FakeClient:
    def call(self, _method, _params=None):
        return {}


class FakeProcess:
    def poll(self):
        return None

    def terminate(self):
        pass

    def wait(self, timeout=None):
        return None


class ModelDownloaderNetworkTests(unittest.TestCase):
    def test_system_proxy_url_prefers_https_proxy(self):
        with mock.patch(
            "aria2_model_downloader.urllib.request.getproxies",
            return_value={"http": "http://http-proxy:1", "https": "http://https-proxy:2"},
        ):
            self.assertEqual(system_proxy_url(), "http://https-proxy:2")

    def test_system_proxy_url_falls_back_to_windows_registry(self):
        with mock.patch(
            "aria2_model_downloader.urllib.request.getproxies",
            return_value={"no": "*"},
        ), mock.patch(
            "aria2_model_downloader.urllib.request.getproxies_registry",
            return_value={"https": "http://registry-proxy:3"},
            create=True,
        ):
            self.assertEqual(system_proxy_url(), "http://registry-proxy:3")

    def test_connectivity_retries_three_times_before_failing(self):
        opener = mock.Mock()
        opener.open.side_effect = urllib.error.URLError("offline")
        with mock.patch("aria2_model_downloader._direct_opener", return_value=opener), mock.patch(
            "aria2_model_downloader.time.sleep"
        ):
            with self.assertRaisesRegex(Aria2ModelDownloadError, "source"):
                check_model_source_connectivity(
                    "source",
                    "https://source.invalid",
                    network_mode="direct",
                    attempts=3,
                    timeout=0.01,
                )
        self.assertEqual(opener.open.call_count, 3)

    def test_missing_system_proxy_is_checked_three_times(self):
        with mock.patch(
            "aria2_model_downloader.system_proxy_url",
            side_effect=Aria2ModelDownloadError("no proxy"),
        ) as proxy, mock.patch("aria2_model_downloader.time.sleep"):
            with self.assertRaisesRegex(Aria2ModelDownloadError, "source"):
                check_model_source_connectivity(
                    "source",
                    "https://source.invalid",
                    network_mode="system_proxy",
                    attempts=3,
                    timeout=0.01,
                )
        self.assertEqual(proxy.call_count, 3)

    def test_proxy_aria2_receives_system_proxy(self):
        captured = {}

        def popen(args, **kwargs):
            captured["args"] = args
            captured["env"] = kwargs["env"]
            return FakeProcess()

        with mock.patch.dict(os.environ, {"MODEL_NETWORK_MODE": "system_proxy"}, clear=False), mock.patch(
            "aria2_model_downloader.system_proxy_url", return_value="http://127.0.0.1:7890"
        ), mock.patch.object(PluginAria2ProcessManager, "_find_binary", return_value="aria2c"), mock.patch.object(
            PluginAria2ProcessManager, "_find_free_port", return_value=6801
        ):
            manager = PluginAria2ProcessManager(client_factory=lambda *_args, **_kwargs: FakeClient(), popen=popen)
            manager.ensure_running()

        self.assertIn("--all-proxy=http://127.0.0.1:7890", captured["args"])
        self.assertIn("--http-proxy=http://127.0.0.1:7890", captured["args"])
        self.assertIn("--https-proxy=http://127.0.0.1:7890", captured["args"])
        self.assertIn("--no-proxy=127.0.0.1,localhost,::1", captured["args"])
        self.assertNotIn("HTTP_PROXY", captured["env"])
        self.assertNotIn("HTTPS_PROXY", captured["env"])

    def test_direct_aria2_environment_removes_proxy_variables(self):
        with mock.patch.dict(
            os.environ,
            {
                "MODEL_NETWORK_MODE": "direct",
                "HTTP_PROXY": "http://proxy:1",
                "HTTPS_PROXY": "http://proxy:2",
            },
            clear=False,
        ):
            manager = PluginAria2ProcessManager()
            env = manager._process_environment()

        self.assertNotIn("HTTP_PROXY", env)
        self.assertNotIn("HTTPS_PROXY", env)
        self.assertEqual(env["NO_PROXY"], "*")


if __name__ == "__main__":
    unittest.main()
