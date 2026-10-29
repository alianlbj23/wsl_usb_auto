import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import wsl_usb_auto  # noqa: E402

# usbipd-win 的輸出一律是 UTF-8
FAKE_LIST = (
    "Connected:\r\n"
    "BUSID  VID:PID    DEVICE                                                        STATE\r\n"
    "1-14   1462:7c92  USB 輸入裝置                                                  Not shared\r\n"
    "4-2    303a:1001  USB 序列裝置 (COM7), USB JTAG/serial debug unit               Shared\r\n"
    "\r\n"
    "Persisted:\r\n"
    "GUID                                  DEVICE\r\n"
).encode("utf-8")

_real_run = subprocess.run


def fake_usbipd_run(args, **kwargs):
    """把 usbipd.exe list 換成一個輸出 FAKE_LIST 位元組的子行程，保留呼叫端的解碼參數。"""
    code = f"import sys; sys.stdout.buffer.write({FAKE_LIST!r})"
    return _real_run([sys.executable, "-c", code], **kwargs)


class ScanDevicesTest(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.object(wsl_usb_auto, "usbipd", return_value="usbipd.exe")
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_decodes_utf8_device_names(self):
        with mock.patch.object(wsl_usb_auto.subprocess, "run", side_effect=fake_usbipd_run):
            devs, _ = wsl_usb_auto.scan_devices()
        self.assertEqual([d["name"] for d in devs],
                         ["USB 輸入裝置", "USB 序列裝置 (COM7), USB JTAG/serial debug unit"])
        self.assertEqual([d["state"] for d in devs], ["Not shared", "Shared"])

    def test_invalid_bytes_do_not_crash(self):
        bad = b"1-1    1234:abcd  USB \xff\xfe bad                Not shared\r\n"

        def run(args, **kwargs):
            code = f"import sys; sys.stdout.buffer.write({bad!r})"
            return _real_run([sys.executable, "-c", code], **kwargs)

        with mock.patch.object(wsl_usb_auto.subprocess, "run", side_effect=run):
            devs, _ = wsl_usb_auto.scan_devices()
        self.assertEqual(len(devs), 1)
        self.assertEqual(devs[0]["id"], "1234:abcd")
        self.assertIn("�", devs[0]["name"])


if __name__ == "__main__":
    unittest.main()
