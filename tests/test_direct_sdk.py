"""Checks for the SDK's direct desktop-client bridge."""

import json
import sys
import unittest
from unittest.mock import patch

from baidunetdisk import BaiduNetdisk
from internal_api import desktop_bridge


class DirectSdkTest(unittest.TestCase):
    def test_sdk_import_does_not_load_http_server(self) -> None:
        self.assertNotIn("internal_api.server", sys.modules)

    def test_sdk_list_files_uses_direct_bridge(self) -> None:
        response = {"files": [{"path": "/example.txt", "size": 123}]}
        with patch.object(desktop_bridge, "evaluate", return_value=response) as evaluate:
            self.assertEqual(BaiduNetdisk().list_files("/", refresh=True), response)
        expression = evaluate.call_args.args[0]
        self.assertIn("app.$fetchFileList", expression)
        self.assertIn('"isForceRefresh":true', expression)

    def test_bridge_evaluates_through_docker_exec(self) -> None:
        with (
            patch.object(desktop_bridge, "ensure_inspector") as ensure_inspector,
            patch.object(
                desktop_bridge,
                "docker",
                return_value=json.dumps({"ok": True, "value": {"count": 0}}),
            ) as docker,
        ):
            self.assertEqual(desktop_bridge.evaluate("1 + 1"), {"count": 0})
        ensure_inspector.assert_called_once_with()
        self.assertEqual(
            docker.call_args.args[:5],
            ("exec", "-i", desktop_bridge.CONTAINER, "python3", "-c"),
        )
        self.assertIn("def connect_inspector():", docker.call_args.args[5])
        self.assertNotIn("/config/internal_api/inspector_eval.py", docker.call_args.args)
        self.assertEqual(json.loads(docker.call_args.kwargs["input_text"]), {"expression": "1 + 1"})


if __name__ == "__main__":
    unittest.main()
