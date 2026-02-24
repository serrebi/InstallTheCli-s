import os
import subprocess
import tempfile
import types
import unittest
from unittest.mock import MagicMock, patch

import ai_cli_installer_gui as m


class DummyFrame:
    def __init__(self) -> None:
        self.logs: list[str] = []
        self.statuses: list[str] = []
        self.gauges: list[int] = []
        self.busy: list[bool] = []

    def log(self, message: str) -> None:
        self.logs.append(message)

    def set_status(self, text: str) -> None:
        self.statuses.append(text)

    def set_gauge(self, value: int) -> None:
        self.gauges.append(value)

    def set_busy(self, busy: bool) -> None:
        self.busy.append(busy)


class DummyCheckbox:
    def __init__(self, value: bool) -> None:
        self.value = value

    def GetValue(self) -> bool:
        return self.value

    def SetValue(self, value: bool) -> None:
        self.value = value


class DummyLogCtrl:
    def __init__(self) -> None:
        self.cleared = False
        self.appended: list[str] = []
        self._pos = 0

    def Clear(self) -> None:
        self.cleared = True

    def AppendText(self, text: str) -> None:
        self.appended.append(text)
        self._pos += len(text)

    def ShowPosition(self, _pos: int) -> None:
        return None

    def GetLastPosition(self) -> int:
        return self._pos


class DummyThreadState:
    def __init__(self, alive: bool) -> None:
        self._alive = alive

    def is_alive(self) -> bool:
        return self._alive


class FakeRegistryKey:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class UtilityFunctionTests(unittest.TestCase):
    def test_is_windows_reflects_os_name(self) -> None:
        with patch.object(m.os, "name", "nt"):
            self.assertTrue(m.is_windows())
        with patch.object(m.os, "name", "posix"):
            self.assertFalse(m.is_windows())

    def test_is_linux_reflects_platform(self) -> None:
        with patch.object(m.sys, "platform", "linux"):
            self.assertTrue(m.is_linux())
        with patch.object(m.sys, "platform", "win32"):
            self.assertFalse(m.is_linux())

    def test_is_admin_handles_success_and_exception(self) -> None:
        windll_true = types.SimpleNamespace(
            shell32=types.SimpleNamespace(IsUserAnAdmin=lambda: 1)
        )
        windll_fail = types.SimpleNamespace(
            shell32=types.SimpleNamespace(IsUserAnAdmin=MagicMock(side_effect=RuntimeError("boom")))
        )
        with patch.object(m.ctypes, "windll", windll_true, create=True):
            self.assertTrue(m.is_admin())
        with patch.object(m.ctypes, "windll", windll_fail, create=True):
            self.assertFalse(m.is_admin())

    def test_is_admin_linux_uses_geteuid(self) -> None:
        with (
            patch.object(m.os, "name", "posix"),
            patch.object(m.os, "geteuid", return_value=0, create=True),
        ):
            self.assertTrue(m.is_admin())
        with (
            patch.object(m.os, "name", "posix"),
            patch.object(m.os, "geteuid", side_effect=OSError("nope"), create=True),
        ):
            self.assertFalse(m.is_admin())
        with (
            patch.object(m.os, "name", "posix"),
            patch.object(m.os, "geteuid", None, create=True),
        ):
            self.assertFalse(m.is_admin())

    def test_broadcast_environment_change_calls_windows_api(self) -> None:
        send_mock = MagicMock()
        fake_windll = types.SimpleNamespace(
            user32=types.SimpleNamespace(SendMessageTimeoutW=send_mock)
        )
        fake_result = object()
        with (
            patch.object(m.ctypes, "windll", fake_windll, create=True),
            patch.object(m.ctypes, "c_ulong", return_value=fake_result),
            patch.object(m.ctypes, "byref", side_effect=lambda x: ("ref", x)),
        ):
            m.broadcast_environment_change()

        args = send_mock.call_args.args
        self.assertEqual(args[0], 0xFFFF)
        self.assertEqual(args[1], m.WM_SETTINGCHANGE)
        self.assertEqual(args[3], "Environment")

    def test_broadcast_environment_change_swallows_errors(self) -> None:
        send_mock = MagicMock(side_effect=RuntimeError("fail"))
        fake_windll = types.SimpleNamespace(
            user32=types.SimpleNamespace(SendMessageTimeoutW=send_mock)
        )
        with (
            patch.object(m.ctypes, "windll", fake_windll, create=True),
            patch.object(m.ctypes, "c_ulong", return_value=object()),
            patch.object(m.ctypes, "byref", side_effect=lambda x: x),
        ):
            m.broadcast_environment_change()

    def test_broadcast_environment_change_returns_early_on_non_windows(self) -> None:
        with patch.object(m.os, "name", "posix"):
            self.assertIsNone(m.broadcast_environment_change())

    def test_subprocess_creationflags_kwargs_windows_and_non_windows(self) -> None:
        with patch.object(m.os, "name", "nt"):
            self.assertEqual(m.subprocess_creationflags_kwargs(), {"creationflags": m.CREATE_NO_WINDOW})
        with patch.object(m.os, "name", "posix"):
            self.assertEqual(m.subprocess_creationflags_kwargs(), {})

    def test_grok_spec_uses_vibe_kit_package(self) -> None:
        grok = next(spec for spec in m.CLI_SPECS if spec.key == "grok")
        self.assertEqual(grok.package_candidates, ("@vibe-kit/grok-cli",))
        self.assertIn("grok", grok.command_candidates)

    def test_ollama_spec_uses_official_winget_package(self) -> None:
        ollama = next(spec for spec in m.CLI_SPECS if spec.key == "ollama")
        self.assertEqual(ollama.package_candidates, (m.OLLAMA_WINGET_ID,))
        self.assertEqual(ollama.command_candidates, ("ollama",))
        self.assertIn("official", ollama.help_text.lower())

    def test_split_path_filters_empty_parts(self) -> None:
        self.assertEqual(m.split_path(""), [])
        self.assertEqual(m.split_path("A;;B;"), ["A", "B"])

    def test_normalize_path_for_compare_strips_and_normalizes(self) -> None:
        raw = r"  C:\Temp\foo\..\bar\  "
        expected = os.path.normcase(os.path.normpath(r"C:\Temp\foo\..\bar\\"))
        self.assertEqual(m.normalize_path_for_compare(raw), expected)

    def test_is_path_within_handles_success_and_errors(self) -> None:
        self.assertTrue(m.is_path_within(r"C:\Users\Admin\AppData\Roaming\npm", r"C:\Users\Admin"))
        self.assertFalse(m.is_path_within(r"C:\Program Files\nodejs", r"C:\Users\Admin"))
        with patch.object(m.os.path, "commonpath", side_effect=ValueError):
            self.assertFalse(m.is_path_within("x", "y"))

    def test_powershell_single_quote_escapes_embedded_quotes(self) -> None:
        self.assertEqual(m.powershell_single_quote("abc"), "'abc'")
        self.assertEqual(m.powershell_single_quote("a'b"), "'a''b'")

    def test_dedupe_preserve_order_keeps_first_occurrence(self) -> None:
        values = ["a", "b", "a", "c", "b"]
        self.assertEqual(m.dedupe_preserve_order(values), ["a", "b", "c"])

    def test_get_app_support_directory_uses_localappdata_or_fallback(self) -> None:
        with patch.dict(m.os.environ, {"LocalAppData": r"C:\Users\Admin\AppData\Local"}, clear=False):
            self.assertEqual(
                m.get_app_support_directory(),
                r"C:\Users\Admin\AppData\Local\InstallTheCli",
            )

    def test_get_app_support_directory_linux_uses_xdg_or_local_state(self) -> None:
        with (
            patch.object(m, "is_linux", return_value=True),
            patch.dict(m.os.environ, {"XDG_STATE_HOME": "/home/admin/.state"}, clear=False),
        ):
            self.assertEqual(m.get_app_support_directory(), os.path.join("/home/admin/.state", "InstallTheCli"))

        with (
            patch.object(m, "is_linux", return_value=True),
            patch.dict(m.os.environ, {}, clear=True),
            patch.object(m.os.path, "expanduser", return_value="/home/admin"),
        ):
            self.assertEqual(
                m.get_app_support_directory(),
                os.path.join("/home/admin", ".local", "state", "InstallTheCli"),
            )

    def test_gui_last_run_log_helpers_create_and_append(self) -> None:
        with tempfile.TemporaryDirectory(dir=".") as tmp_dir:
            support_dir = os.path.join(tmp_dir, "state", "InstallTheCli")
            with patch.object(m, "get_app_support_directory", return_value=support_dir):
                path = m.get_gui_last_run_log_path()
                self.assertEqual(path, os.path.join(support_dir, m.GUI_LAST_RUN_LOG_FILE))

                created = m.reset_gui_last_run_log()
                self.assertEqual(created, path)
                self.assertTrue(os.path.isfile(path))

                err = m.append_persistent_log_line(path, "hello")
                self.assertIsNone(err)
                with open(path, "r", encoding="utf-8") as f:
                    text = f.read()
                self.assertIn("InstallTheCli GUI log started:", text)
                self.assertIn("hello\n", text)

    def test_append_persistent_log_line_handles_none_and_oserror(self) -> None:
        self.assertIsNone(m.append_persistent_log_line(None, "ignored"))
        with patch("builtins.open", side_effect=OSError("disk full")):
            err = m.append_persistent_log_line("x.log", "line")
        self.assertIn("disk full", err or "")

    def test_reset_gui_last_run_log_returns_none_on_failure(self) -> None:
        with (
            patch.object(m, "get_app_support_directory", return_value=r"C:\Denied\InstallTheCli"),
            patch.object(m.os, "makedirs", side_effect=OSError("denied")),
        ):
            self.assertIsNone(m.reset_gui_last_run_log())

    def test_read_linux_os_release_and_detect_family(self) -> None:
        os_release = "ID=ubuntu\nID_LIKE=debian\nNAME='Ubuntu'\n#comment\n"
        with (
            patch.object(m, "is_linux", return_value=True),
            patch("builtins.open", unittest.mock.mock_open(read_data=os_release)),
        ):
            parsed = m.read_linux_os_release()
        self.assertEqual(parsed["ID"], "ubuntu")
        self.assertEqual(parsed["ID_LIKE"], "debian")

        with patch.object(m, "is_linux", return_value=False):
            self.assertEqual(m.read_linux_os_release(), {})

        with (
            patch.object(m, "is_linux", return_value=True),
            patch("builtins.open", side_effect=OSError("no file")),
        ):
            self.assertEqual(m.read_linux_os_release(), {})

        with patch.object(m, "is_linux", return_value=False):
            self.assertIsNone(m.detect_linux_distro_family())
        with (
            patch.object(m, "is_linux", return_value=True),
            patch.object(m, "read_linux_os_release", return_value={"ID": "ubuntu", "ID_LIKE": "debian"}),
        ):
            self.assertEqual(m.detect_linux_distro_family(), "debian")
        with (
            patch.object(m, "is_linux", return_value=True),
            patch.object(m, "read_linux_os_release", return_value={"ID": "fedora", "ID_LIKE": "rhel"}),
        ):
            self.assertEqual(m.detect_linux_distro_family(), "fedora")
        with (
            patch.object(m, "is_linux", return_value=True),
            patch.object(m, "read_linux_os_release", return_value={"ID": "arch", "ID_LIKE": ""}),
        ):
            self.assertEqual(m.detect_linux_distro_family(), "arch")
        with (
            patch.object(m, "is_linux", return_value=True),
            patch.object(m, "read_linux_os_release", return_value={"ID": "suse", "ID_LIKE": ""}),
        ):
            self.assertIsNone(m.detect_linux_distro_family())

    def test_linux_root_helpers(self) -> None:
        with patch.object(m, "is_linux", return_value=True):
            self.assertTrue(m.linux_requires_root_for_system_install())
        with patch.object(m, "is_linux", return_value=False):
            self.assertFalse(m.linux_requires_root_for_system_install())

        logs: list[str] = []
        with (
            patch.object(m, "linux_requires_root_for_system_install", return_value=False),
            patch.object(m, "is_admin", return_value=False),
        ):
            self.assertTrue(m.ensure_linux_root_for_package_installs(logs.append))
        self.assertEqual(logs, [])

        with (
            patch.object(m, "linux_requires_root_for_system_install", return_value=True),
            patch.object(m, "is_admin", return_value=True),
        ):
            self.assertTrue(m.ensure_linux_root_for_package_installs(logs.append))

        with (
            patch.object(m, "linux_requires_root_for_system_install", return_value=True),
            patch.object(m, "is_admin", return_value=False),
        ):
            self.assertFalse(m.ensure_linux_root_for_package_installs(logs.append))
        self.assertTrue(any("root privileges" in line for line in logs))

    def test_linux_package_manager_name_delegates(self) -> None:
        with patch.object(m, "detect_linux_distro_family", return_value="debian"):
            self.assertEqual(m.linux_package_manager_name(), "debian")

    def test_pip_install_flags_for_platform_linux_and_non_linux(self) -> None:
        with patch.object(m, "is_linux", return_value=False):
            self.assertEqual(m.pip_install_flags_for_platform(), list(m.PIP_QUIET_FLAGS))
        with patch.object(m, "is_linux", return_value=True):
            flags = m.pip_install_flags_for_platform()
        self.assertIn("--break-system-packages", flags)

        with (
            patch.dict(m.os.environ, {}, clear=True),
            patch.object(m.os.path, "expanduser", return_value=r"C:\Users\Admin"),
        ):
            self.assertEqual(
                m.get_app_support_directory(),
                r"C:\Users\Admin\AppData\Local\InstallTheCli",
            )

    def test_filter_system_path_dirs_excludes_user_scoped_locations(self) -> None:
        dirs = [
            r"C:\Users\Admin\AppData\Roaming\npm",
            r"C:\Users\Admin\Tools",
            r"C:\Program Files\nodejs",
            r"D:\Shared\bin",
        ]
        with (
            patch.object(m.os.path, "expanduser", return_value=r"C:\Users\Admin"),
            patch.dict(
                m.os.environ,
                {
                    "AppData": r"C:\Users\Admin\AppData\Roaming",
                    "LocalAppData": r"C:\Users\Admin\AppData\Local",
                    "UserProfile": r"C:\Users\Admin",
                },
                clear=False,
            ),
        ):
            result = m.filter_system_path_dirs(dirs)
        self.assertEqual(result, [r"C:\Program Files\nodejs", r"D:\Shared\bin"])

    def test_add_dirs_to_path_linux_user_system_and_errors(self) -> None:
        with tempfile.TemporaryDirectory(dir=".") as tmp_dir:
            home = os.path.join(tmp_dir, "home")
            os.makedirs(home, exist_ok=True)
            bin_dir = os.path.join(home, ".local", "bin")
            os.makedirs(bin_dir, exist_ok=True)
            profile_path = os.path.join(home, ".profile")
            with open(profile_path, "w", encoding="utf-8") as f:
                f.write("# existing")

            with (
                patch.object(m, "is_windows", return_value=False),
                patch.object(m.os.path, "expanduser", return_value=home),
                patch.dict(m.os.environ, {"PATH": "/usr/bin"}, clear=False),
            ):
                added, err = m.add_dirs_to_path("user", [bin_dir])
            self.assertEqual(added, [bin_dir])
            self.assertIsNone(err)
            with open(profile_path, "r", encoding="utf-8") as f:
                profile_text = f.read()
            self.assertIn("InstallTheCli PATH", profile_text)
            self.assertIn(bin_dir, profile_text)

            with (
                patch.object(m, "is_windows", return_value=False),
                patch.object(m.os.path, "expanduser", return_value=home),
                patch.dict(m.os.environ, {"PATH": f"/usr/bin{os.pathsep}{bin_dir}"}, clear=False),
            ):
                added, err = m.add_dirs_to_path("user", [bin_dir])
            self.assertEqual(added, [])
            self.assertIsNone(err)

            with open(profile_path, "a", encoding="utf-8") as f:
                f.write(f'\nexport PATH="$PATH:{bin_dir}"  # InstallTheCli PATH {bin_dir}\n')
            with (
                patch.object(m, "is_windows", return_value=False),
                patch.object(m.os.path, "expanduser", return_value=home),
                patch.dict(m.os.environ, {"PATH": "/usr/bin"}, clear=False),
            ):
                added, err = m.add_dirs_to_path("user", [bin_dir])
            self.assertEqual(added, [])
            self.assertIsNone(err)

            with patch.object(m, "is_windows", return_value=False):
                self.assertEqual(m.add_dirs_to_path("system", [bin_dir]), ([], None))
                with self.assertRaises(ValueError):
                    m.add_dirs_to_path("machine", [bin_dir])

        with (
            patch.object(m, "is_windows", return_value=False),
            patch.object(m.os.path, "isdir", return_value=True),
            patch.object(m.os.path, "expanduser", return_value=r"C:\Temp\home"),
            patch("builtins.open", side_effect=OSError("profile denied")),
        ):
            added, err = m.add_dirs_to_path("user", [r"C:\Temp\bin"])
        self.assertEqual(added, [])
        self.assertIn("profile denied", err or "")

    def test_find_desktop_directory_linux_prefers_existing_then_falls_back(self) -> None:
        with (
            patch.object(m, "is_windows", return_value=False),
            patch.object(m.os.path, "expanduser", return_value="/home/admin"),
            patch.dict(m.os.environ, {"XDG_DESKTOP_DIR": "/home/admin/MyDesktop"}, clear=False),
            patch.object(m.os.path, "expandvars", side_effect=lambda p: p),
            patch.object(m.os.path, "isdir", side_effect=lambda p: p == "/home/admin/MyDesktop"),
        ):
            self.assertEqual(m.find_desktop_directory(), "/home/admin/MyDesktop")

        with (
            patch.object(m, "is_windows", return_value=False),
            patch.object(m.os.path, "expanduser", return_value="/home/admin"),
            patch.dict(m.os.environ, {}, clear=True),
            patch.object(m.os.path, "isdir", return_value=False),
        ):
            self.assertEqual(m.find_desktop_directory(), os.path.join("/home/admin", "Desktop"))

    def test_linux_one_click_script_exists_and_contains_expected_commands(self) -> None:
        script_path = os.path.join(os.getcwd(), "install_all_linux.sh")
        self.assertTrue(os.path.isfile(script_path), f"Missing script: {script_path}")
        with open(script_path, "r", encoding="utf-8") as f:
            script = f.read()
        self.assertIn("Ollama", script)
        self.assertIn("mistral-vibe", script)
        self.assertIn("@openai/codex", script)
        self.assertIn("CRON_FILE_PATH", script)
        self.assertIn("@reboot root", script)
        self.assertIn("0 3 * * *", script)
        self.assertIn("--no-update-notifier", script)
        self.assertIn("install <target>", script)
        self.assertIn("setup-cron", script)

        with open(script_path, "rb") as f:
            raw = f.read()
        self.assertNotIn(b"\r\n", raw)

    def test_windows_one_click_powershell_script_exists_and_has_help_and_subcommands(self) -> None:
        script_path = os.path.join(os.getcwd(), "install_all_windows.ps1")
        self.assertTrue(os.path.isfile(script_path), f"Missing script: {script_path}")
        with open(script_path, "r", encoding="utf-8") as f:
            script = f.read()
        self.assertIn("Get-Help .\\install_all_windows.ps1 -Detailed", script)
        self.assertIn("install-all", script)
        self.assertIn("install <target>", script)
        self.assertIn("setup-updater", script)
        self.assertIn("--no-update-notifier", script)


class RegistryAndWindowsTests(unittest.TestCase):
    def test_add_dirs_to_path_returns_early_for_empty_or_missing_dirs(self) -> None:
        self.assertEqual(m.add_dirs_to_path("user", []), ([], None))
        with patch.object(m.os.path, "isdir", return_value=False):
            self.assertEqual(m.add_dirs_to_path("user", [r"C:\Missing"]), ([], None))

    def test_add_dirs_to_path_rejects_unsupported_scope(self) -> None:
        with patch.object(m.os.path, "isdir", return_value=True):
            with self.assertRaises(ValueError):
                m.add_dirs_to_path("machine", [r"C:\Tools"])

    def test_add_dirs_to_path_adds_only_new_dirs_and_broadcasts(self) -> None:
        set_value = MagicMock()
        with (
            patch.object(m.os.path, "isdir", return_value=True),
            patch.object(m.winreg, "OpenKey", return_value=FakeRegistryKey()),
            patch.object(
                m.winreg,
                "QueryValueEx",
                return_value=(r"C:\Existing;C:\Tools", m.winreg.REG_EXPAND_SZ),
            ),
            patch.object(m.winreg, "SetValueEx", set_value),
            patch.object(m, "broadcast_environment_change") as broadcast_mock,
        ):
            added, err = m.add_dirs_to_path("user", [r"C:\TOOLS", r"C:\NewBin"])

        self.assertIsNone(err)
        self.assertEqual(added, [r"C:\NewBin"])
        self.assertTrue(set_value.called)
        self.assertTrue(broadcast_mock.called)
        args = set_value.call_args.args
        self.assertEqual(args[1], "Path")
        self.assertIn(r"C:\NewBin", args[4])

    def test_add_dirs_to_path_handles_missing_path_value(self) -> None:
        set_value = MagicMock()
        with (
            patch.object(m.os.path, "isdir", return_value=True),
            patch.object(m.winreg, "OpenKey", return_value=FakeRegistryKey()),
            patch.object(m.winreg, "QueryValueEx", side_effect=FileNotFoundError()),
            patch.object(m.winreg, "SetValueEx", set_value),
            patch.object(m, "broadcast_environment_change"),
        ):
            added, err = m.add_dirs_to_path("user", [r"C:\NewBin"])

        self.assertEqual(added, [r"C:\NewBin"])
        self.assertIsNone(err)
        self.assertEqual(set_value.call_args.args[3], m.winreg.REG_EXPAND_SZ)

    def test_add_dirs_to_path_normalizes_unknown_registry_type(self) -> None:
        set_value = MagicMock()
        with (
            patch.object(m.os.path, "isdir", return_value=True),
            patch.object(m.winreg, "OpenKey", return_value=FakeRegistryKey()),
            patch.object(m.winreg, "QueryValueEx", return_value=(r"C:\Existing", 99999)),
            patch.object(m.winreg, "SetValueEx", set_value),
            patch.object(m, "broadcast_environment_change"),
        ):
            added, err = m.add_dirs_to_path("user", [r"C:\NewBin"])

        self.assertEqual(added, [r"C:\NewBin"])
        self.assertIsNone(err)
        self.assertEqual(set_value.call_args.args[3], m.winreg.REG_EXPAND_SZ)

    def test_add_dirs_to_path_handles_permission_error(self) -> None:
        with (
            patch.object(m.os.path, "isdir", return_value=True),
            patch.object(m.winreg, "OpenKey", side_effect=PermissionError("denied")),
        ):
            added, err = m.add_dirs_to_path("system", [r"C:\Program Files\nodejs"])
        self.assertEqual(added, [])
        self.assertIn("denied", err or "")

    def test_add_dirs_to_path_handles_oserror(self) -> None:
        with (
            patch.object(m.os.path, "isdir", return_value=True),
            patch.object(m.winreg, "OpenKey", side_effect=OSError("registry error")),
        ):
            added, err = m.add_dirs_to_path("user", [r"C:\Any"])
        self.assertEqual(added, [])
        self.assertIn("registry error", err or "")

    def test_find_desktop_directory_prefers_registry_value(self) -> None:
        with (
            patch.object(m.winreg, "OpenKey", return_value=FakeRegistryKey()),
            patch.object(m.winreg, "QueryValueEx", return_value=(r"%USERPROFILE%\\Desktop", None)),
            patch.object(m.os.path, "expandvars", return_value=r"C:\Users\Admin\Desktop"),
            patch.object(m.os.path, "expanduser", return_value=r"C:\Users\Admin"),
            patch.object(
                m.os.path,
                "isdir",
                side_effect=lambda p: p == r"C:\Users\Admin\Desktop",
            ),
        ):
            result = m.find_desktop_directory()
        self.assertEqual(result, r"C:\Users\Admin\Desktop")

    def test_find_desktop_directory_falls_back_to_onedrive(self) -> None:
        with (
            patch.object(m.winreg, "OpenKey", side_effect=OSError("no key")),
            patch.object(m.os.path, "expanduser", return_value=r"C:\Users\Admin"),
            patch.object(
                m.os.path,
                "isdir",
                side_effect=lambda p: p == r"C:\Users\Admin\OneDrive\Desktop",
            ),
        ):
            result = m.find_desktop_directory()
        self.assertEqual(result, r"C:\Users\Admin\OneDrive\Desktop")

    def test_find_desktop_directory_returns_first_candidate_when_none_exist(self) -> None:
        with (
            patch.object(m.winreg, "OpenKey", side_effect=OSError("no key")),
            patch.object(m.os.path, "expanduser", return_value=r"C:\Users\Admin"),
            patch.object(m.os.path, "isdir", return_value=False),
        ):
            result = m.find_desktop_directory()
        self.assertEqual(result, r"C:\Users\Admin\Desktop")

    def test_create_windows_shortcut_builds_expected_powershell_command(self) -> None:
        with patch.object(m.subprocess, "run") as run_mock:
            m.create_windows_shortcut(
                shortcut_path=r"C:\Users\Admin\Desktop\Grok's.lnk",
                target_path=r"C:\Windows\System32\cmd.exe",
                arguments='/k "grok"',
                working_directory=r"C:\Users\Admin",
                icon_location=r"C:\Windows\System32\cmd.exe,0",
            )

        argv = run_mock.call_args.args[0]
        ps_script = argv[-1]
        self.assertEqual(argv[:4], ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass"])
        self.assertIn("CreateShortcut('C:\\Users\\Admin\\Desktop\\Grok''s.lnk')", ps_script)
        self.assertIn("$sc.Arguments = '/k \"grok\"'", ps_script)

    def test_create_cli_desktop_shortcut_wraps_with_cmd_exe(self) -> None:
        spec = next(spec for spec in m.CLI_SPECS if spec.key == "codex")
        logs: list[str] = []
        with (
            patch.object(m, "find_desktop_directory", return_value=r"C:\Users\Admin\Desktop"),
            patch.object(m.os.path, "expanduser", return_value=r"C:\Users\Admin"),
            patch.dict(m.os.environ, {"ComSpec": r"C:\Windows\System32\cmd.exe"}, clear=False),
            patch.object(m, "create_windows_shortcut") as shortcut_mock,
        ):
            path = m.create_cli_desktop_shortcut(
                spec,
                r"C:\Users\Admin\AppData\Roaming\npm\codex.cmd",
                logs.append,
            )

        self.assertEqual(path, r"C:\Users\Admin\Desktop\Codex CLI.lnk")
        self.assertTrue(any("Created desktop shortcut:" in line for line in logs))
        kwargs = shortcut_mock.call_args.kwargs
        self.assertEqual(kwargs["target_path"], r"C:\Windows\System32\cmd.exe")
        self.assertEqual(
            kwargs["arguments"],
            '/k "C:\\Users\\Admin\\AppData\\Roaming\\npm\\codex.cmd"',
        )

    def test_create_linux_desktop_shortcut_writes_desktop_file(self) -> None:
        with tempfile.TemporaryDirectory(dir=".") as tmp_dir:
            path = os.path.join(tmp_dir, "Desktop", "Codex CLI.desktop")
            chmod_mock = MagicMock()
            with patch.object(m.os, "chmod", chmod_mock):
                m.create_linux_desktop_shortcut(path, "/usr/local/bin/codex", "Codex CLI")

            self.assertTrue(os.path.isfile(path))
            with open(path, "r", encoding="utf-8") as f:
                content = f.read()
            self.assertIn("[Desktop Entry]", content)
            self.assertIn("Exec=/usr/local/bin/codex", content)
            self.assertIn("Terminal=true", content)
            chmod_mock.assert_called_once_with(path, 0o755)

    def test_create_cli_desktop_shortcut_linux_writes_desktop_file(self) -> None:
        spec = next(spec for spec in m.CLI_SPECS if spec.key == "ollama")
        logs: list[str] = []
        with (
            patch.object(m, "is_windows", return_value=False),
            patch.object(m, "find_desktop_directory", return_value="/home/admin/Desktop"),
            patch.object(m, "create_linux_desktop_shortcut") as create_linux_mock,
        ):
            path = m.create_cli_desktop_shortcut(spec, "/usr/local/bin/ollama", logs.append)
        expected_path = os.path.join("/home/admin/Desktop", "Ollama CLI.desktop")
        self.assertEqual(path, expected_path)
        create_linux_mock.assert_called_once_with(expected_path, "/usr/local/bin/ollama", "Ollama CLI")
        self.assertTrue(any("Created desktop shortcut:" in line for line in logs))


class CommandAndDetectionTests(unittest.TestCase):
    def test_run_command_streams_output_and_returns_exit_code(self) -> None:
        logs: list[str] = []

        class FakePopen:
            def __init__(self) -> None:
                self.stdout = iter(["hello\n", "\n", "world\n"])

            def wait(self) -> int:
                return 7

        with patch.object(m.subprocess, "Popen", return_value=FakePopen()) as popen_mock:
            rc = m.run_command(["demo", "arg"], logs.append)

        self.assertEqual(rc, 7)
        self.assertEqual(logs[0], "> demo arg")
        self.assertIn("hello", logs)
        self.assertIn("world", logs)
        self.assertNotIn("", logs)
        self.assertEqual(popen_mock.call_args.args[0], ["demo", "arg"])

    def test_command_exists_handles_success_and_oserror(self) -> None:
        ok = types.SimpleNamespace(returncode=0)
        with patch.object(m.subprocess, "run", return_value=ok):
            self.assertTrue(m.command_exists("npm"))
        with patch.object(m.subprocess, "run", side_effect=OSError("bad")):
            self.assertFalse(m.command_exists("npm"))

    def test_command_exists_and_where_all_use_linux_which(self) -> None:
        ok = types.SimpleNamespace(returncode=0)
        found = types.SimpleNamespace(returncode=0, stdout="/usr/bin/codex\n/usr/local/bin/codex\n")
        with (
            patch.object(m, "is_windows", return_value=False),
            patch.object(m.subprocess, "run", return_value=ok) as run_mock,
        ):
            self.assertTrue(m.command_exists("codex"))
        self.assertEqual(run_mock.call_args.args[0], ["which", "codex"])

        with (
            patch.object(m, "is_windows", return_value=False),
            patch.object(m.subprocess, "run", return_value=found) as run_mock,
        ):
            self.assertEqual(m.where_all("codex"), ["/usr/bin/codex", "/usr/local/bin/codex"])
        self.assertEqual(run_mock.call_args.args[0], ["which", "-a", "codex"])

    def test_where_all_parses_output_and_handles_nonzero(self) -> None:
        found = types.SimpleNamespace(returncode=0, stdout="A\r\n\r\nB\n")
        with patch.object(m.subprocess, "run", return_value=found):
            self.assertEqual(m.where_all("codex"), ["A", "B"])
        miss = types.SimpleNamespace(returncode=1, stdout="")
        with patch.object(m.subprocess, "run", return_value=miss):
            self.assertEqual(m.where_all("codex"), [])

    def test_where_all_handles_oserror(self) -> None:
        with patch.object(m.subprocess, "run", side_effect=OSError("where missing")):
            self.assertEqual(m.where_all("codex"), [])

    def test_find_winget_delegates_to_shutil_which(self) -> None:
        with patch.object(m.shutil, "which", return_value=r"C:\Windows\System32\winget.exe") as which_mock:
            result = m.find_winget()
        self.assertEqual(result, r"C:\Windows\System32\winget.exe")
        which_mock.assert_called_once_with("winget")

    def test_find_uv_and_python_launcher_and_pip3_return_none_when_missing(self) -> None:
        with (
            patch.object(m.shutil, "which", return_value=None),
            patch.object(m.os.path, "isfile", return_value=False),
        ):
            self.assertIsNone(m.find_uv())
            self.assertIsNone(m.find_python_launcher())
            self.assertIsNone(m.find_pip3())
            self.assertIsNone(m.find_ollama())

    def test_find_uv_and_python_launcher_return_detected_paths(self) -> None:
        with patch.object(m.shutil, "which", side_effect=[r"C:\Tools\uv.exe", r"C:\Windows\py.exe"]):
            self.assertEqual(m.find_uv(), r"C:\Tools\uv.exe")
            self.assertEqual(m.find_python_launcher(), r"C:\Windows\py.exe")

    def test_find_ollama_prefers_shutil_which_and_known_locations(self) -> None:
        with (
            patch.object(m.shutil, "which", side_effect=[r"C:\Users\Admin\AppData\Local\Programs\Ollama\ollama.exe"]),
            patch.object(m.os.path, "isfile") as isfile_mock,
        ):
            self.assertEqual(m.find_ollama(), r"C:\Users\Admin\AppData\Local\Programs\Ollama\ollama.exe")
        isfile_mock.assert_not_called()

        expected = r"C:\Users\Admin\AppData\Local\Programs\Ollama\ollama.exe"
        with (
            patch.object(m.shutil, "which", side_effect=[None, None]),
            patch.dict(
                m.os.environ,
                {
                    "LocalAppData": r"C:\Users\Admin\AppData\Local",
                    "ProgramFiles": r"C:\Program Files",
                    "ProgramFiles(x86)": r"C:\Program Files (x86)",
                },
                clear=False,
            ),
            patch.object(m.os.path, "isfile", side_effect=lambda p: p == expected),
        ):
            self.assertEqual(m.find_ollama(), expected)

    def test_find_ollama_uses_linux_known_paths(self) -> None:
        with (
            patch.object(m.shutil, "which", side_effect=[None, None]),
            patch.object(m, "is_linux", return_value=True),
            patch.object(m.os.path, "isfile", side_effect=lambda p: p == "/usr/local/bin/ollama"),
        ):
            self.assertEqual(m.find_ollama(), "/usr/local/bin/ollama")
        with (
            patch.object(m.shutil, "which", side_effect=[None, None]),
            patch.object(m, "is_linux", return_value=True),
            patch.object(m.os.path, "isfile", return_value=False),
        ):
            self.assertIsNone(m.find_ollama())

    def test_find_pip3_delegates_to_shutil_which(self) -> None:
        with patch.object(m.shutil, "which", return_value=r"C:\Users\Admin\AppData\Roaming\Python\Scripts\pip3.exe"):
            self.assertEqual(m.find_pip3(), r"C:\Users\Admin\AppData\Roaming\Python\Scripts\pip3.exe")

    def test_get_python_version_parses_and_handles_failures(self) -> None:
        ok = types.SimpleNamespace(returncode=0, stdout="3.14.2\n")
        with patch.object(m.subprocess, "run", return_value=ok):
            self.assertEqual(m.get_python_version(["py", "-3.14"]), (3, 14, 2))

        bad = types.SimpleNamespace(returncode=1, stdout="")
        with patch.object(m.subprocess, "run", return_value=bad):
            self.assertIsNone(m.get_python_version(["py", "-3.14"]))

        malformed = types.SimpleNamespace(returncode=0, stdout="not-a-version")
        with patch.object(m.subprocess, "run", return_value=malformed):
            self.assertIsNone(m.get_python_version(["py", "-3.14"]))

        with patch.object(m.subprocess, "run", side_effect=OSError("no python")):
            self.assertIsNone(m.get_python_version(["py", "-3.14"]))

    def test_find_python_314_command_prefers_py_launcher(self) -> None:
        def fake_which(name: str) -> str | None:
            if name in ("py.exe", "py"):
                return r"C:\Windows\py.exe"
            return None
        self.assertIsNone(fake_which("other"))

        with (
            patch.object(m.shutil, "which", side_effect=fake_which),
            patch.object(m, "get_python_version", side_effect=lambda args: (3, 14, 1) if args[:2] == [r"C:\Windows\py.exe", "-3.14"] else None),
        ):
            self.assertEqual(m.find_python_314_command(), [r"C:\Windows\py.exe", "-3.14"])

    def test_find_python_314_command_falls_back_to_known_path(self) -> None:
        target = r"C:\Users\Admin\AppData\Local\Programs\Python\Python314\python.exe"

        def fake_which(_name: str) -> None:
            return None

        def fake_isfile(path: str) -> bool:
            return path == target

        with (
            patch.object(m.shutil, "which", side_effect=fake_which),
            patch.dict(m.os.environ, {"LocalAppData": r"C:\Users\Admin\AppData\Local"}, clear=False),
            patch.object(m.os.path, "isfile", side_effect=fake_isfile),
            patch.object(m, "get_python_version", side_effect=lambda args: (3, 14, 0) if args == [target] else None),
        ):
            self.assertEqual(m.find_python_314_command(), [target])

    def test_find_python_314_command_uses_versioned_python_on_path(self) -> None:
        target = r"C:\Python314\python3.14.exe"

        def fake_which(name: str) -> str | None:
            if name == "python3.14.exe":
                return target
            return None

        with (
            patch.object(m.shutil, "which", side_effect=fake_which),
            patch.object(m, "get_python_version", side_effect=lambda args: (3, 14, 5) if args == [target] else None),
        ):
            self.assertEqual(m.find_python_314_command(), [target])

    def test_find_python_314_command_falls_back_to_generic_python_and_none(self) -> None:
        generic = r"C:\Python\python.exe"

        def generic_which(name: str) -> str | None:
            if name == "python.exe":
                return generic
            return None

        with (
            patch.object(m.shutil, "which", side_effect=generic_which),
            patch.object(m.os.path, "isfile", return_value=False),
            patch.object(m, "get_python_version", side_effect=lambda args: (3, 14, 7) if args == [generic] else None),
        ):
            self.assertEqual(m.find_python_314_command(), [generic])

        with (
            patch.object(m.shutil, "which", return_value=None),
            patch.object(m.os.path, "isfile", return_value=False),
            patch.object(m, "get_python_version", return_value=None),
        ):
            self.assertIsNone(m.find_python_314_command())

    def test_find_node_prefers_shutil_which(self) -> None:
        with (
            patch.object(m.shutil, "which", side_effect=[r"C:\Program Files\nodejs\node.exe"]),
            patch.object(m.os.path, "isfile") as isfile_mock,
        ):
            result = m.find_node()
        self.assertEqual(result, r"C:\Program Files\nodejs\node.exe")
        isfile_mock.assert_not_called()

    def test_find_node_fallback_returns_none_without_local_appdata(self) -> None:
        with (
            patch.object(m.shutil, "which", side_effect=[None, None]),
            patch.dict(
                m.os.environ,
                {"ProgramFiles": r"C:\Program Files", "ProgramFiles(x86)": r"C:\Program Files (x86)"},
                clear=True,
            ),
            patch.object(m.os.path, "isfile", return_value=False),
        ):
            self.assertIsNone(m.find_node())

    def test_find_node_fallback_uses_local_appdata_candidate(self) -> None:
        expected = r"C:\Users\Admin\AppData\Local\Programs\nodejs\node.exe"
        with (
            patch.object(m.shutil, "which", side_effect=[None, None]),
            patch.dict(
                m.os.environ,
                {
                    "ProgramFiles": r"C:\Program Files",
                    "ProgramFiles(x86)": r"C:\Program Files (x86)",
                    "LocalAppData": r"C:\Users\Admin\AppData\Local",
                },
                clear=False,
            ),
            patch.object(m.os.path, "isfile", side_effect=lambda p: p == expected),
        ):
            self.assertEqual(m.find_node(), expected)

    def test_find_npm_prefers_shutil_which(self) -> None:
        with (
            patch.object(m.shutil, "which", side_effect=[r"C:\Program Files\nodejs\npm.cmd"]),
            patch.object(m.os.path, "isfile") as isfile_mock,
        ):
            result = m.find_npm()
        self.assertEqual(result, r"C:\Program Files\nodejs\npm.cmd")
        isfile_mock.assert_not_called()

    def test_find_npm_falls_back_to_known_install_locations(self) -> None:
        expected = r"C:\Users\Admin\AppData\Local\Programs\nodejs\npm.cmd"
        with (
            patch.object(m.shutil, "which", side_effect=[None, None]),
            patch.dict(
                m.os.environ,
                {
                    "ProgramFiles": r"C:\Program Files",
                    "ProgramFiles(x86)": r"C:\Program Files (x86)",
                    "LocalAppData": r"C:\Users\Admin\AppData\Local",
                },
                clear=False,
            ),
            patch.object(m.os.path, "isfile", side_effect=lambda p: p == expected),
        ):
            result = m.find_npm()
        self.assertEqual(result, expected)

    def test_find_npm_fallback_returns_none_without_local_appdata(self) -> None:
        with (
            patch.object(m.shutil, "which", side_effect=[None, None]),
            patch.dict(
                m.os.environ,
                {"ProgramFiles": r"C:\Program Files", "ProgramFiles(x86)": r"C:\Program Files (x86)"},
                clear=True,
            ),
            patch.object(m.os.path, "isfile", return_value=False),
        ):
            self.assertIsNone(m.find_npm())

    def test_get_npm_global_prefix_uses_fallback_command(self) -> None:
        responses = [
            types.SimpleNamespace(returncode=1, stdout=""),
            types.SimpleNamespace(returncode=0, stdout=r"C:\Users\Admin\AppData\Roaming\npm" + "\n"),
        ]
        with (
            patch.object(m.subprocess, "run", side_effect=responses) as run_mock,
            patch.dict(m.os.environ, {"PATH": r"C:\Windows\System32"}, clear=False),
            patch.object(
                m.os.path,
                "isdir",
                side_effect=lambda p: p == r"C:\Users\Admin\AppData\Roaming\npm",
            ),
        ):
            prefix = m.get_npm_global_prefix("npm.cmd", lambda _msg: None)

        self.assertEqual(prefix, r"C:\Users\Admin\AppData\Roaming\npm")
        first_call = run_mock.call_args_list[0].args[0]
        second_call = run_mock.call_args_list[1].args[0]
        self.assertEqual(first_call, ["npm.cmd", "prefix", "-g"])
        self.assertEqual(second_call, ["npm.cmd", "config", "get", "prefix"])
        first_env = run_mock.call_args_list[0].kwargs["env"]
        self.assertEqual(first_env["PATH"], r"C:\Windows\System32")
        self.assertEqual(first_env["npm_config_update_notifier"], "false")

    def test_get_npm_global_prefix_prepends_npm_dir_to_subprocess_path(self) -> None:
        responses = [types.SimpleNamespace(returncode=0, stdout=r"C:\Users\Admin\AppData\Roaming\npm" + "\n")]
        with (
            patch.object(m.subprocess, "run", side_effect=responses) as run_mock,
            patch.dict(m.os.environ, {"PATH": r"C:\Windows\System32"}, clear=False),
            patch.object(m.os.path, "isdir", return_value=True),
        ):
            result = m.get_npm_global_prefix(r"C:\Program Files\nodejs\npm.cmd", lambda _msg: None)
        self.assertEqual(result, r"C:\Users\Admin\AppData\Roaming\npm")
        env = run_mock.call_args.kwargs["env"]
        self.assertTrue(env["PATH"].startswith(r"C:\Program Files\nodejs;"))

    def test_get_npm_global_prefix_logs_oserror(self) -> None:
        logs: list[str] = []
        with patch.object(m.subprocess, "run", side_effect=OSError("boom")):
            result = m.get_npm_global_prefix("npm.cmd", logs.append)
        self.assertIsNone(result)
        self.assertTrue(any("Unable to query npm prefix" in line for line in logs))

    def test_get_npm_global_prefix_returns_none_when_path_missing(self) -> None:
        responses = [
            types.SimpleNamespace(returncode=0, stdout=r"C:\MissingPrefix\n"),
            types.SimpleNamespace(returncode=1, stdout=""),
        ]
        with (
            patch.object(m.subprocess, "run", side_effect=responses),
            patch.object(m.os.path, "isdir", return_value=False),
        ):
            result = m.get_npm_global_prefix("npm.cmd", lambda _msg: None)
        self.assertIsNone(result)

    def test_get_cli_bin_dirs_merges_sources_and_dedupes(self) -> None:
        with (
            patch.dict(
                m.os.environ,
                {
                    "ProgramFiles": r"C:\Program Files",
                    "ProgramFiles(x86)": r"C:\Program Files (x86)",
                    "AppData": r"C:\Users\Admin\AppData\Roaming",
                },
                clear=False,
            ),
            patch.object(
                m.os.path,
                "isdir",
                side_effect=lambda p: p in {
                    r"C:\Program Files\nodejs",
                    r"C:\Users\Admin\AppData\Roaming\npm",
                },
            ),
            patch.object(m, "get_npm_global_prefix", return_value=r"C:\Users\Admin\AppData\Roaming\npm"),
        ):
            dirs = m.get_cli_bin_dirs("npm.cmd", lambda _msg: None)
        self.assertEqual(dirs, [r"C:\Program Files\nodejs", r"C:\Users\Admin\AppData\Roaming\npm"])

    def test_get_cli_bin_dirs_linux_uses_standard_bins_and_prefix_bin_or_prefix(self) -> None:
        with (
            patch.object(m, "is_linux", return_value=True),
            patch.object(m, "is_windows", return_value=False),
            patch.object(m.os.path, "isdir", side_effect=lambda p: p in {"/usr/bin", "/usr", "/usr/local/bin"}),
            patch.object(m, "get_npm_global_prefix", return_value="/usr"),
            patch.dict(m.os.environ, {}, clear=True),
        ):
            dirs = m.get_cli_bin_dirs("npm", lambda _msg: None)
        self.assertIn("/usr/bin", dirs)
        self.assertIn("/usr/local/bin", dirs)

        with (
            patch.object(m, "is_linux", return_value=True),
            patch.object(m, "is_windows", return_value=False),
            patch.object(m.os.path, "isdir", side_effect=lambda p: p in {"/usr/bin", "/custom"}),
            patch.object(m, "get_npm_global_prefix", return_value="/custom"),
            patch.dict(m.os.environ, {}, clear=True),
        ):
            dirs = m.get_cli_bin_dirs("npm", lambda _msg: None)
        self.assertIn("/custom", dirs)

    def test_get_ollama_cli_bin_dirs_linux(self) -> None:
        with (
            patch.object(m, "is_linux", return_value=True),
            patch.object(m.os.path, "isdir", side_effect=lambda p: p == "/usr/local/bin"),
        ):
            self.assertEqual(m.get_ollama_cli_bin_dirs(lambda _msg: None), ["/usr/local/bin"])

    def test_get_python_cli_bin_dirs_merges_and_dedupes(self) -> None:
        home = r"C:\Users\Admin"
        appdata = r"C:\Users\Admin\AppData\Roaming"
        local_app = r"C:\Users\Admin\AppData\Local"
        home_bin = os.path.join(home, ".local", "bin")
        roaming_scripts = os.path.join(appdata, "Python", "Python314", "Scripts")
        local_scripts = os.path.join(local_app, "Programs", "Python", "Python314", "Scripts")

        def fake_glob(pattern: str) -> list[str]:
            if pattern == os.path.join(appdata, "Python", "Python*", "Scripts"):
                return [roaming_scripts, roaming_scripts]
            if pattern == os.path.join(local_app, "Programs", "Python", "Python*", "Scripts"):
                return [local_scripts]
            return []
        self.assertEqual(fake_glob("unmatched"), [])

        existing = {home_bin, roaming_scripts, local_scripts}
        with (
            patch.object(m.os.path, "expanduser", return_value=home),
            patch.dict(m.os.environ, {"AppData": appdata, "LocalAppData": local_app}, clear=False),
            patch.object(m.glob, "glob", side_effect=fake_glob),
            patch.object(m.os.path, "isdir", side_effect=lambda p: p in existing),
        ):
            dirs = m.get_python_cli_bin_dirs(lambda _msg: None)

        self.assertEqual(dirs, [home_bin, roaming_scripts, local_scripts])

    def test_get_ollama_cli_bin_dirs_merges_and_dedupes(self) -> None:
        local_app = r"C:\Users\Admin\AppData\Local"
        local_dir = os.path.join(local_app, "Programs", "Ollama")
        program_files_dir = r"C:\Program Files\Ollama"
        with (
            patch.dict(
                m.os.environ,
                {
                    "LocalAppData": local_app,
                    "ProgramFiles": r"C:\Program Files",
                    "ProgramFiles(x86)": r"C:\Program Files (x86)",
                },
                clear=False,
            ),
            patch.object(
                m.os.path,
                "isdir",
                side_effect=lambda p: p in {local_dir, program_files_dir},
            ),
        ):
            dirs = m.get_ollama_cli_bin_dirs(lambda _msg: None)
        self.assertEqual(dirs, [local_dir, program_files_dir])

    def test_try_install_package_candidates_retries_until_success(self) -> None:
        logs: list[str] = []
        qwen = next(spec for spec in m.CLI_SPECS if spec.key == "qwen")
        with patch.object(m, "npm_install_global", side_effect=[2, 0]) as npm_mock:
            success, pkg = m.try_install_package_candidates("npm.cmd", qwen, logs.append)
        self.assertTrue(success)
        self.assertEqual(pkg, qwen.package_candidates[1])
        self.assertEqual(npm_mock.call_count, 2)
        self.assertTrue(any("Trying npm package for Qwen CLI" in line for line in logs))

    def test_npm_install_global_delegates_to_run_command(self) -> None:
        with (
            patch.object(m, "run_command", return_value=123) as run_mock,
            patch.dict(m.os.environ, {"PATH": r"C:\Windows\System32"}, clear=False),
        ):
            rc = m.npm_install_global("npm.cmd", "@openai/codex", lambda _msg: None)
        self.assertEqual(rc, 123)
        run_mock.assert_called_once()
        self.assertEqual(
            run_mock.call_args.args[0],
            [
                "npm.cmd",
                "--no-fund",
                "--no-audit",
                "--no-update-notifier",
                "--loglevel",
                "error",
                "install",
                "-g",
                "@openai/codex",
            ],
        )
        self.assertEqual(run_mock.call_args.kwargs["env"]["npm_config_update_notifier"], "false")

    def test_npm_install_global_prepends_npm_dir_to_subprocess_path(self) -> None:
        with (
            patch.object(m, "run_command", return_value=0) as run_mock,
            patch.dict(m.os.environ, {"PATH": r"C:\Windows\System32"}, clear=False),
        ):
            m.npm_install_global(r"C:\Program Files\nodejs\npm.cmd", "@openai/codex", lambda _msg: None)
        env = run_mock.call_args.kwargs["env"]
        self.assertTrue(env["PATH"].startswith(r"C:\Program Files\nodejs;"))

    def test_windows_errno_exit_code_helpers(self) -> None:
        self.assertTrue(m.is_probably_windows_errno_exit_code(4294963214))
        self.assertFalse(m.is_probably_windows_errno_exit_code(1))
        self.assertEqual(m.format_exit_code(1), "1")
        self.assertIn("Windows errno -4082", m.format_exit_code(4294963214))

    def test_is_probably_windows_file_lock_error(self) -> None:
        self.assertTrue(m.is_probably_windows_file_lock_error("EBUSY"))
        self.assertTrue(m.is_probably_windows_file_lock_error("Windows errno -4082"))
        self.assertTrue(m.is_probably_windows_file_lock_error("@openai/codex failed with exit code 4294963214"))
        self.assertFalse(m.is_probably_windows_file_lock_error("failed with exit code 1"))
        self.assertFalse(m.is_probably_windows_file_lock_error(None))

    def test_try_install_package_candidates_returns_last_error(self) -> None:
        logs: list[str] = []
        claude = next(spec for spec in m.CLI_SPECS if spec.key == "claude")
        with patch.object(m, "npm_install_global", return_value=9):
            success, err = m.try_install_package_candidates("npm.cmd", claude, logs.append)
        self.assertFalse(success)
        self.assertEqual(err, "@anthropic-ai/claude-code failed with exit code 9")
        self.assertIn(err, logs)

    def test_try_install_package_candidates_retries_transient_windows_lock_error(self) -> None:
        logs: list[str] = []
        codex = next(spec for spec in m.CLI_SPECS if spec.key == "codex")
        with (
            patch.object(m, "npm_install_global", side_effect=[4294963214, 0]) as npm_mock,
            patch.object(m.time, "sleep") as sleep_mock,
        ):
            success, pkg = m.try_install_package_candidates("npm.cmd", codex, logs.append)

        self.assertTrue(success)
        self.assertEqual(pkg, "@openai/codex")
        self.assertEqual(npm_mock.call_count, 2)
        sleep_mock.assert_called_once_with(m.NPM_INSTALL_RETRY_DELAY_SECONDS)
        self.assertTrue(any("Transient npm install failure detected" in line for line in logs))

    def test_resolve_command_path_prefers_cmd(self) -> None:
        with patch.object(
            m,
            "where_all",
            return_value=[
                r"C:\Users\Admin\AppData\Roaming\npm\codex",
                r"C:\Users\Admin\AppData\Roaming\npm\codex.cmd",
                r"C:\Users\Admin\AppData\Roaming\npm\codex.ps1",
            ],
        ):
            result = m.resolve_command_path(("codex",), [])
        self.assertEqual(result, r"C:\Users\Admin\AppData\Roaming\npm\codex.cmd")

    def test_resolve_command_path_returns_first_found_without_priority_extension(self) -> None:
        with patch.object(m, "where_all", return_value=[r"C:\Tools\weirdtool.custom"]):
            result = m.resolve_command_path(("weirdtool",), [])
        self.assertEqual(result, r"C:\Tools\weirdtool.custom")

    def test_resolve_command_path_prefixes_extra_dirs_in_path(self) -> None:
        captured_envs: list[dict[str, str]] = []

        def fake_where_all(_name, env=None):
            assert env is not None
            captured_envs.append(env)
            return []

        with (
            patch.object(m, "where_all", side_effect=fake_where_all),
            patch.dict(m.os.environ, {"PATH": r"C:\Windows\System32"}, clear=False),
            patch.object(m.os.path, "isfile", return_value=False),
        ):
            result = m.resolve_command_path(("grok", "grok-cli"), [r"C:\Users\Admin\AppData\Roaming\npm"])

        self.assertIsNone(result)
        self.assertTrue(captured_envs)
        self.assertTrue(
            captured_envs[0]["PATH"].startswith(r"C:\Users\Admin\AppData\Roaming\npm;C:\Windows\System32")
        )

    def test_resolve_command_path_falls_back_to_direct_file_scan(self) -> None:
        target = r"C:\Users\Admin\AppData\Roaming\npm\grok.cmd"
        with (
            patch.object(m, "where_all", return_value=[]),
            patch.object(m.os.path, "isfile", side_effect=lambda p: p == target),
        ):
            result = m.resolve_command_path(("grok",), [r"C:\Users\Admin\AppData\Roaming\npm"])
        self.assertEqual(result, target)

    def test_resolve_command_path_falls_back_to_direct_file_without_extension(self) -> None:
        direct = r"C:\Users\Admin\AppData\Roaming\npm\grok"
        def fake_isfile(path: str) -> bool:
            return path == direct
        with (
            patch.object(m, "where_all", return_value=[]),
            patch.object(m.os.path, "isfile", side_effect=fake_isfile),
        ):
            result = m.resolve_command_path(("grok",), [r"C:\Users\Admin\AppData\Roaming\npm"])
        self.assertEqual(result, direct)

    def test_resolve_command_path_linux_priority_and_pathsep(self) -> None:
        captured_envs: list[dict[str, str]] = []

        def fake_where_all(_name, env=None):
            assert env is not None
            captured_envs.append(env)
            return ["/tmp/tool", "/tmp/tool.bin"]

        with (
            patch.object(m, "is_windows", return_value=False),
            patch.object(m, "where_all", side_effect=fake_where_all),
            patch.dict(m.os.environ, {"PATH": "/usr/bin"}, clear=False),
            patch.object(m.os, "pathsep", ":"),
        ):
            result = m.resolve_command_path(("tool",), ["/opt/bin"])
        self.assertEqual(result, "/tmp/tool.bin")
        self.assertTrue(captured_envs[0]["PATH"].startswith("/opt/bin:"))

        with (
            patch.object(m, "is_windows", return_value=False),
            patch.object(m, "where_all", return_value=["/tmp/tool"]),
        ):
            self.assertEqual(m.resolve_command_path(("tool",), []), "/tmp/tool")


class AutoUpdateSchedulerTests(unittest.TestCase):
    def test_build_cli_auto_update_script_uses_npm_update_and_suppresses_output(self) -> None:
        script = m.build_cli_auto_update_script(
            r"C:\Program Files\nodejs\npm.cmd",
            r"C:\Users\Admin\AppData\Local\InstallTheCli\auto_update_packages.txt",
        )
        self.assertIn("$npm = 'C:\\Program Files\\nodejs\\npm.cmd'", script)
        self.assertIn("$npmDir = Split-Path -Parent $npm", script)
        self.assertIn("$env:npm_config_update_notifier = 'false'", script)
        self.assertIn("$packagesFile =", script)
        self.assertIn("'--no-fund'", script)
        self.assertIn("'--no-audit'", script)
        self.assertIn("'--no-update-notifier'", script)
        self.assertIn("'--loglevel' 'error'", script)
        self.assertIn("$null = & $npm", script)
        self.assertIn("'update' '-g' @packages *>&1", script)

    def test_ensure_cli_auto_update_task_skips_when_no_packages(self) -> None:
        logs: list[str] = []
        with patch.object(m.subprocess, "run") as run_mock:
            merged = m.ensure_cli_auto_update_task("npm.cmd", [], logs.append)
        self.assertEqual(merged, [])
        run_mock.assert_not_called()
        self.assertTrue(any("Auto-update task unchanged" in line for line in logs))

    def test_ensure_cli_auto_update_task_skips_on_linux(self) -> None:
        logs: list[str] = []
        with (
            patch.object(m, "is_windows", return_value=False),
            patch.object(m.subprocess, "run") as run_mock,
        ):
            merged = m.ensure_cli_auto_update_task("npm", ["@openai/codex"], logs.append)
        self.assertEqual(merged, [])
        run_mock.assert_not_called()
        self.assertTrue(any("Windows-only" in line for line in logs))

    def test_ensure_cli_auto_update_task_writes_files_and_registers_hidden_task(self) -> None:
        logs: list[str] = []
        with tempfile.TemporaryDirectory(dir=".") as tmp_dir:
            with (
                patch.object(m, "get_app_support_directory", return_value=tmp_dir),
                patch.object(m.subprocess, "run") as run_mock,
            ):
                merged = m.ensure_cli_auto_update_task(
                    r"C:\Program Files\nodejs\npm.cmd",
                    ["@openai/codex", "@vibe-kit/grok-cli"],
                    logs.append,
                )

            self.assertEqual(merged, ["@openai/codex", "@vibe-kit/grok-cli"])
            packages_path = os.path.join(tmp_dir, m.AUTO_UPDATE_PACKAGES_FILE)
            script_path = os.path.join(tmp_dir, m.AUTO_UPDATE_SCRIPT_FILE)
            self.assertTrue(os.path.isfile(packages_path))
            self.assertTrue(os.path.isfile(script_path))
            self.assertEqual(m.read_nonempty_lines(packages_path), merged)

            with open(script_path, "r", encoding="utf-8") as f:
                script_text = f.read()
            self.assertIn("update' '-g'", script_text)
            self.assertIn("@packages", script_text)

            run_args = run_mock.call_args.args[0]
            task_command = run_args[-1]
            self.assertEqual(run_args[:4], ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass"])
            self.assertIn("New-ScheduledTaskTrigger -AtStartup", task_command)
            self.assertIn("New-ScheduledTaskTrigger -AtLogOn", task_command)
            self.assertIn("New-ScheduledTaskTrigger -Daily -At '3:00AM'", task_command)
            self.assertIn("-WindowStyle Hidden", task_command)
            self.assertIn("New-ScheduledTaskSettingsSet -Hidden", task_command)
            self.assertIn("-LogonType Interactive", task_command)
            self.assertNotIn("InteractiveToken", task_command)
            self.assertIn(m.AUTO_UPDATE_TASK_NAME, task_command)
            self.assertEqual(run_mock.call_args.kwargs["creationflags"], m.CREATE_NO_WINDOW)

        self.assertTrue(any("Configured hidden CLI auto-update task" in line for line in logs))
        self.assertTrue(any("startup, user logon, and daily" in line for line in logs))

    def test_ensure_cli_auto_update_task_merges_existing_package_file(self) -> None:
        with tempfile.TemporaryDirectory(dir=".") as tmp_dir:
            packages_path = os.path.join(tmp_dir, m.AUTO_UPDATE_PACKAGES_FILE)
            m.write_nonempty_lines(packages_path, ["@openai/codex", "@google/gemini-cli"])

            with (
                patch.object(m, "get_app_support_directory", return_value=tmp_dir),
                patch.object(m.subprocess, "run"),
            ):
                merged = m.ensure_cli_auto_update_task(
                    "npm.cmd",
                    ["@google/gemini-cli", "@vibe-kit/grok-cli"],
                    lambda _msg: None,
                )

            self.assertEqual(merged, ["@openai/codex", "@google/gemini-cli", "@vibe-kit/grok-cli"])
            self.assertEqual(m.read_nonempty_lines(packages_path), merged)

    def test_ensure_cli_auto_update_task_raises_on_registration_failure(self) -> None:
        with tempfile.TemporaryDirectory(dir=".") as tmp_dir:
            error = subprocess.CalledProcessError(
                returncode=1,
                cmd=["powershell"],
                stderr="Access denied",
            )
            with (
                patch.object(m, "get_app_support_directory", return_value=tmp_dir),
                patch.object(m.subprocess, "run", side_effect=error),
            ):
                with self.assertRaises(RuntimeError) as ctx:
                    m.ensure_cli_auto_update_task("npm.cmd", ["@openai/codex"], lambda _msg: None)
        self.assertIn("Unable to configure hidden CLI auto-update task", str(ctx.exception))

    def test_ensure_cli_auto_update_task_raises_on_oserror(self) -> None:
        with tempfile.TemporaryDirectory(dir=".") as tmp_dir:
            with (
                patch.object(m, "get_app_support_directory", return_value=tmp_dir),
                patch.object(m.subprocess, "run", side_effect=OSError("no powershell")),
            ):
                with self.assertRaises(RuntimeError) as ctx:
                    m.ensure_cli_auto_update_task("npm.cmd", ["@openai/codex"], lambda _msg: None)
        self.assertIn("Unable to configure hidden CLI auto-update task", str(ctx.exception))


class NodeInstallAndWorkflowTests(unittest.TestCase):
    def test_ensure_node_via_winget_returns_when_already_available(self) -> None:
        logs: list[str] = []
        with (
            patch.object(m, "find_winget", return_value="winget.exe"),
            patch.object(m, "find_node", return_value=r"C:\Program Files\nodejs\node.exe"),
            patch.object(m, "find_npm", return_value=r"C:\Program Files\nodejs\npm.cmd"),
            patch.object(m, "run_command") as run_command_mock,
        ):
            m.ensure_node_via_winget(logs.append)
        run_command_mock.assert_not_called()
        self.assertTrue(any("Node.js is already available" in line for line in logs))
        self.assertTrue(any("npm is already available" in line for line in logs))

    def test_ensure_node_via_winget_raises_when_winget_missing(self) -> None:
        with patch.object(m, "find_winget", return_value=None):
            with self.assertRaises(RuntimeError) as ctx:
                m.ensure_node_via_winget(lambda _msg: None)
        self.assertIn("winget was not found", str(ctx.exception))

    def test_ensure_node_via_winget_installs_when_npm_missing(self) -> None:
        logs: list[str] = []

        with (
            patch.object(m, "find_winget", return_value="winget.exe"),
            patch.object(
                m,
                "find_node",
                side_effect=[r"C:\Program Files\nodejs\node.exe", r"C:\Program Files\nodejs\node.exe"],
            ),
            patch.object(m, "find_npm", side_effect=[None, r"C:\Program Files\nodejs\npm.cmd"]),
            patch.object(m, "run_command", return_value=0) as run_command_mock,
        ):
            m.ensure_node_via_winget(logs.append)

        self.assertEqual(run_command_mock.call_count, 1)
        self.assertTrue(any("includes npm" in line for line in logs))
        self.assertTrue(any("Missing prerequisites: npm" in line for line in logs))
        self.assertTrue(any("npm is available:" in line for line in logs))

    def test_ensure_node_via_winget_raises_when_install_command_fails(self) -> None:
        with (
            patch.object(m, "find_winget", return_value="winget.exe"),
            patch.object(m, "find_node", return_value=None),
            patch.object(m, "find_npm", return_value=None),
            patch.object(m, "run_command", return_value=5),
        ):
            with self.assertRaises(RuntimeError) as ctx:
                m.ensure_node_via_winget(lambda _msg: None)
        self.assertIn("exit code 5", str(ctx.exception))

    def test_ensure_node_via_winget_raises_when_binaries_still_missing_after_install(self) -> None:
        with (
            patch.object(m, "find_winget", return_value="winget.exe"),
            patch.object(m, "find_node", side_effect=[None, None]),
            patch.object(m, "find_npm", side_effect=[None, None]),
            patch.object(m, "run_command", return_value=0),
        ):
            with self.assertRaises(RuntimeError) as ctx:
                m.ensure_node_via_winget(lambda _msg: None)
        self.assertIn("could not be found", str(ctx.exception))

    def test_linux_package_manager_install_commands_for_supported_and_unsupported(self) -> None:
        with patch.object(m, "linux_package_manager_name", return_value="debian"):
            self.assertEqual(
                m.linux_package_manager_install_commands(["nodejs", "npm"]),
                [["apt-get", "update"], ["apt-get", "install", "-y", "nodejs", "npm"]],
            )
        with patch.object(m, "linux_package_manager_name", return_value="fedora"):
            self.assertEqual(
                m.linux_package_manager_install_commands(["nodejs"]),
                [["dnf", "install", "-y", "nodejs"]],
            )
        with patch.object(m, "linux_package_manager_name", return_value="arch"):
            self.assertEqual(
                m.linux_package_manager_install_commands(["python"]),
                [["pacman", "-Sy", "--noconfirm", "python"]],
            )
        with patch.object(m, "linux_package_manager_name", return_value=None):
            with self.assertRaises(RuntimeError):
                m.linux_package_manager_install_commands(["nodejs"])

    def test_ensure_linux_packages_installed_paths(self) -> None:
        logs: list[str] = []
        with (
            patch.object(m, "is_linux", return_value=False),
            patch.object(m, "run_command") as run_mock,
        ):
            m.ensure_linux_packages_installed(["nodejs"], logs.append)
        run_mock.assert_not_called()

        with (
            patch.object(m, "is_linux", return_value=True),
            patch.object(m, "ensure_linux_root_for_package_installs", return_value=False),
        ):
            with self.assertRaises(RuntimeError) as ctx:
                m.ensure_linux_packages_installed(["nodejs"], lambda _msg: None)
        self.assertIn("root privileges", str(ctx.exception))

        with (
            patch.object(m, "is_linux", return_value=True),
            patch.object(m, "ensure_linux_root_for_package_installs", return_value=True),
            patch.object(m, "linux_package_manager_install_commands", return_value=[["apt-get", "update"], ["apt-get", "install", "-y", "nodejs"]]),
            patch.object(m, "run_command", side_effect=[0, 0]) as run_command_mock,
        ):
            m.ensure_linux_packages_installed(["nodejs"], logs.append)
        self.assertEqual(run_command_mock.call_count, 2)
        self.assertTrue(any("Installing Linux packages:" in line for line in logs))

        with (
            patch.object(m, "is_linux", return_value=True),
            patch.object(m, "ensure_linux_root_for_package_installs", return_value=True),
            patch.object(m, "linux_package_manager_install_commands", return_value=[["apt-get", "install", "-y", "nodejs"]]),
            patch.object(m, "run_command", return_value=9),
        ):
            with self.assertRaises(RuntimeError) as ctx:
                m.ensure_linux_packages_installed(["nodejs"], lambda _msg: None)
        self.assertIn("exit code 9", str(ctx.exception))

    def test_ensure_node_via_winget_linux_branches(self) -> None:
        logs: list[str] = []
        with (
            patch.object(m, "is_linux", return_value=True),
            patch.object(m, "find_node", return_value="/usr/bin/node"),
            patch.object(m, "find_npm", return_value="/usr/bin/npm"),
        ):
            m.ensure_node_via_winget(logs.append)
        self.assertTrue(any("Node.js is already available" in line for line in logs))

        logs = []
        with (
            patch.object(m, "is_linux", return_value=True),
            patch.object(m, "find_node", side_effect=[None, "/usr/bin/node"]),
            patch.object(m, "find_npm", side_effect=[None, "/usr/bin/npm"]),
            patch.object(m, "ensure_linux_packages_installed") as ensure_pkgs,
        ):
            m.ensure_node_via_winget(logs.append)
        ensure_pkgs.assert_called_once_with(["nodejs", "npm"], logs.append)
        self.assertTrue(any("Linux package manager" in line for line in logs))

        with (
            patch.object(m, "is_linux", return_value=True),
            patch.object(m, "find_node", side_effect=[None, None]),
            patch.object(m, "find_npm", side_effect=[None, None]),
            patch.object(m, "ensure_linux_packages_installed"),
        ):
            with self.assertRaises(RuntimeError) as ctx:
                m.ensure_node_via_winget(lambda _msg: None)
        self.assertIn("could not be found", str(ctx.exception))

    def test_ensure_ollama_via_winget_linux_branches(self) -> None:
        logs: list[str] = []
        with (
            patch.object(m, "is_linux", return_value=True),
            patch.object(m, "find_ollama", return_value="/usr/local/bin/ollama"),
            patch.object(m, "command_exists", side_effect=lambda name: name in {"curl", "sh"}),
            patch.object(m, "run_command", return_value=0),
        ):
            ok, pkg = m.ensure_ollama_via_winget(logs.append)
        self.assertTrue(ok)
        self.assertEqual(pkg, m.OLLAMA_WINGET_ID)
        self.assertTrue(any("already available" in line for line in logs))

        logs = []
        with (
            patch.object(m, "is_linux", return_value=True),
            patch.object(m, "find_ollama", return_value=None),
            patch.object(m, "command_exists", side_effect=lambda name: name in {"curl", "sh"}),
            patch.object(m, "run_command", return_value=0) as run_command_mock,
        ):
            ok, pkg = m.ensure_ollama_via_winget(logs.append)
        self.assertTrue(ok)
        self.assertEqual(pkg, m.OLLAMA_WINGET_ID)
        self.assertEqual(run_command_mock.call_args.args[0][0:2], ["sh", "-c"])

        with (
            patch.object(m, "is_linux", return_value=True),
            patch.object(m, "find_ollama", return_value=None),
            patch.object(m, "command_exists", side_effect=lambda name: name == "curl"),
        ):
            ok, err = m.ensure_ollama_via_winget(lambda _msg: None)
        self.assertFalse(ok)
        self.assertIn("sh was not found", str(err))

        with (
            patch.object(m, "is_linux", return_value=True),
            patch.object(m, "find_ollama", return_value=None),
            patch.object(m, "command_exists", return_value=False),
            patch.object(m, "ensure_linux_packages_installed", side_effect=RuntimeError("pkg failed")),
        ):
            ok, err = m.ensure_ollama_via_winget(lambda _msg: None)
        self.assertFalse(ok)
        self.assertEqual(err, "pkg failed")

        logs = []
        with (
            patch.object(m, "is_linux", return_value=True),
            patch.object(m, "find_ollama", side_effect=[None, "/usr/local/bin/ollama"]),
            patch.object(m, "command_exists", side_effect=lambda name: name in {"curl", "sh"}),
            patch.object(m, "run_command", return_value=5),
        ):
            ok, pkg = m.ensure_ollama_via_winget(logs.append)
        self.assertTrue(ok)
        self.assertEqual(pkg, m.OLLAMA_WINGET_ID)
        self.assertTrue(any("Using existing installation and continuing" in line for line in logs))

        with (
            patch.object(m, "is_linux", return_value=True),
            patch.object(m, "find_ollama", return_value=None),
            patch.object(m, "command_exists", side_effect=lambda name: name in {"curl", "sh"}),
            patch.object(m, "run_command", return_value=5),
        ):
            ok, err = m.ensure_ollama_via_winget(lambda _msg: None)
        self.assertFalse(ok)
        self.assertIn(m.OLLAMA_WINGET_ID, str(err))

    def test_ensure_ollama_via_winget_installs_official_package(self) -> None:
        logs: list[str] = []
        with (
            patch.object(m, "find_winget", return_value="winget.exe"),
            patch.object(m, "find_ollama", return_value=None),
            patch.object(m, "run_command", return_value=0) as run_command_mock,
        ):
            ok, pkg = m.ensure_ollama_via_winget(logs.append)
        self.assertTrue(ok)
        self.assertEqual(pkg, m.OLLAMA_WINGET_ID)
        self.assertEqual(run_command_mock.call_count, 1)
        self.assertIn("install", run_command_mock.call_args.args[0])
        self.assertTrue(any("official Ollama" in line for line in logs))

    def test_ensure_ollama_via_winget_uses_upgrade_or_existing_on_failure(self) -> None:
        logs: list[str] = []
        with (
            patch.object(m, "find_winget", return_value="winget.exe"),
            patch.object(m, "find_ollama", side_effect=[r"C:\Users\Admin\AppData\Local\Programs\Ollama\ollama.exe", r"C:\Users\Admin\AppData\Local\Programs\Ollama\ollama.exe"]),
            patch.object(m, "run_command", side_effect=[5, 9]) as run_command_mock,
        ):
            ok, pkg = m.ensure_ollama_via_winget(logs.append)
        self.assertTrue(ok)
        self.assertEqual(pkg, m.OLLAMA_WINGET_ID)
        self.assertEqual(run_command_mock.call_count, 2)
        self.assertTrue(any("trying winget upgrade" in line.lower() for line in logs))
        self.assertTrue(any("Using existing installation and continuing" in line for line in logs))

    def test_ensure_ollama_via_winget_returns_error_when_winget_missing_or_install_fails(self) -> None:
        logs: list[str] = []
        with patch.object(m, "find_winget", return_value=None):
            ok, err = m.ensure_ollama_via_winget(logs.append)
        self.assertFalse(ok)
        self.assertIn("winget was not found", str(err))

        logs = []
        with (
            patch.object(m, "find_winget", return_value="winget.exe"),
            patch.object(m, "find_ollama", return_value=None),
            patch.object(m, "run_command", side_effect=[5, 9]),
        ):
            ok, err = m.ensure_ollama_via_winget(logs.append)
        self.assertFalse(ok)
        self.assertIn(m.OLLAMA_WINGET_ID, str(err))
        self.assertIn("exit code", str(err))

    def test_ensure_python_314_via_winget_returns_existing_python(self) -> None:
        logs: list[str] = []
        with patch.object(m, "find_python_314_command", return_value=["py.exe", "-3.14"]):
            result = m.ensure_python_314_via_winget(logs.append)
        self.assertEqual(result, ["py.exe", "-3.14"])
        self.assertTrue(any("already available" in line for line in logs))

    def test_find_linux_python_for_mistral_and_ensure_python_for_mistral_on_linux(self) -> None:
        def fake_which(name: str) -> str | None:
            return {"python3.14": "/usr/bin/python3.14", "python3": "/usr/bin/python3"}.get(name)

        with (
            patch.object(m.shutil, "which", side_effect=fake_which),
            patch.object(m, "get_python_version", side_effect=lambda args: (3, 14, 1) if args == ["/usr/bin/python3.14"] else None),
        ):
            self.assertEqual(m.find_linux_python_for_mistral(), ["/usr/bin/python3.14"])

        with (
            patch.object(m.shutil, "which", side_effect=lambda n: "/usr/bin/python3" if n == "python3" else None),
            patch.object(m, "get_python_version", return_value=(3, 12, 0)),
        ):
            self.assertEqual(m.find_linux_python_for_mistral(), ["/usr/bin/python3"])
        with (
            patch.object(m.shutil, "which", return_value=None),
            patch.object(m, "get_python_version", return_value=None),
        ):
            self.assertIsNone(m.find_linux_python_for_mistral())

        logs: list[str] = []
        with (
            patch.object(m, "find_linux_python_for_mistral", return_value=["/usr/bin/python3"]),
            patch.object(m, "get_python_version", return_value=(3, 12, 7)),
        ):
            self.assertEqual(m.ensure_python_for_mistral_on_linux(logs.append), ["/usr/bin/python3"])
        self.assertTrue(any("already available" in line for line in logs))

        with (
            patch.object(m, "find_linux_python_for_mistral", side_effect=[None, ["/usr/bin/python3"]]),
            patch.object(m, "linux_package_manager_name", return_value="arch"),
            patch.object(m, "ensure_linux_packages_installed") as ensure_pkgs,
            patch.object(m, "get_python_version", return_value=(3, 12, 1)),
        ):
            self.assertEqual(m.ensure_python_for_mistral_on_linux(lambda _msg: None), ["/usr/bin/python3"])
        ensure_pkgs.assert_called_once_with(["python", "python-pip"], unittest.mock.ANY)

        with (
            patch.object(m, "find_linux_python_for_mistral", side_effect=[None, None]),
            patch.object(m, "linux_package_manager_name", return_value="debian"),
            patch.object(m, "ensure_linux_packages_installed"),
        ):
            with self.assertRaises(RuntimeError) as ctx:
                m.ensure_python_for_mistral_on_linux(lambda _msg: None)
        self.assertIn("no compatible Python", str(ctx.exception))

        with (
            patch.object(m, "find_linux_python_for_mistral", side_effect=[None, ["/usr/bin/python3"]]),
            patch.object(m, "linux_package_manager_name", return_value="debian"),
            patch.object(m, "ensure_linux_packages_installed"),
            patch.object(m, "get_python_version", return_value=(3, 11, 9)),
        ):
            with self.assertRaises(RuntimeError) as ctx:
                m.ensure_python_for_mistral_on_linux(lambda _msg: None)
        self.assertIn("too old", str(ctx.exception))

    def test_ensure_python_314_via_winget_installs_when_missing(self) -> None:
        logs: list[str] = []
        with (
            patch.object(m, "find_python_314_command", side_effect=[None, [r"C:\Windows\py.exe", "-3.14"]]),
            patch.object(m, "find_winget", return_value=r"C:\Windows\System32\winget.exe"),
            patch.object(m, "run_command", return_value=0) as run_command_mock,
        ):
            result = m.ensure_python_314_via_winget(logs.append)

        self.assertEqual(result, [r"C:\Windows\py.exe", "-3.14"])
        self.assertEqual(run_command_mock.call_count, 1)
        self.assertTrue(any("Installing Python 3.14 via winget" in line for line in logs))

    def test_ensure_python_314_via_winget_raises_when_winget_missing(self) -> None:
        with (
            patch.object(m, "find_python_314_command", return_value=None),
            patch.object(m, "find_winget", return_value=None),
        ):
            with self.assertRaises(RuntimeError) as ctx:
                m.ensure_python_314_via_winget(lambda _msg: None)
        self.assertIn("winget was not found", str(ctx.exception))

    def test_ensure_python_314_via_winget_raises_on_install_failure_and_missing_post_install(self) -> None:
        with (
            patch.object(m, "find_python_314_command", return_value=None),
            patch.object(m, "find_winget", return_value="winget.exe"),
            patch.object(m, "run_command", return_value=5),
        ):
            with self.assertRaises(RuntimeError) as ctx:
                m.ensure_python_314_via_winget(lambda _msg: None)
        self.assertIn("exit code 5", str(ctx.exception))

        with (
            patch.object(m, "find_python_314_command", side_effect=[None, None]),
            patch.object(m, "find_winget", return_value="winget.exe"),
            patch.object(m, "run_command", return_value=0),
        ):
            with self.assertRaises(RuntimeError) as ctx:
                m.ensure_python_314_via_winget(lambda _msg: None)
        self.assertIn("could not be found", str(ctx.exception))

    def test_ensure_pip3_for_python_bootstraps_and_updates(self) -> None:
        logs: list[str] = []
        with (
            patch.object(m, "run_command", side_effect=[1, 0, 0, 0]) as run_command_mock,
            patch.object(m, "find_pip3", return_value=r"C:\Users\Admin\AppData\Roaming\Python\Scripts\pip3.exe"),
        ):
            m.ensure_pip3_for_python(["py.exe", "-3.14"], logs.append)

        self.assertEqual(run_command_mock.call_count, 4)
        self.assertTrue(any("bootstrapping pip" in line for line in logs))
        self.assertTrue(any("Updating pip3" in line for line in logs))
        self.assertTrue(any("pip3 is available:" in line for line in logs))

    def test_ensure_pip3_for_python_logs_existing_and_raises_for_failures(self) -> None:
        logs: list[str] = []
        with (
            patch.object(m, "run_command", side_effect=[0, 0]),
            patch.object(m, "find_pip3", return_value=None),
        ):
            m.ensure_pip3_for_python(["py.exe", "-3.14"], logs.append)
        self.assertTrue(any("already available" in line for line in logs))

        with patch.object(m, "run_command", side_effect=[1, 9]):
            with self.assertRaises(RuntimeError) as ctx:
                m.ensure_pip3_for_python(["py.exe", "-3.14"], lambda _msg: None)
        self.assertIn("ensurepip failed", str(ctx.exception))

        with patch.object(m, "run_command", side_effect=[1, 0, 2]):
            with self.assertRaises(RuntimeError) as ctx:
                m.ensure_pip3_for_python(["py.exe", "-3.14"], lambda _msg: None)
        self.assertIn("still unavailable", str(ctx.exception))

        with patch.object(m, "run_command", side_effect=[0, 4]):
            with self.assertRaises(RuntimeError) as ctx:
                m.ensure_pip3_for_python(["py.exe", "-3.14"], lambda _msg: None)
        self.assertIn("pip3 update failed", str(ctx.exception))

    def test_ensure_uv_for_mistral_installs_and_returns_path(self) -> None:
        logs: list[str] = []
        with (
            patch.object(m, "find_uv", side_effect=[None, r"C:\Users\Admin\AppData\Roaming\Python\Scripts\uv.exe"]),
            patch.object(m, "run_command", return_value=0) as run_command_mock,
        ):
            uv_path = m.ensure_uv_for_mistral(["py.exe", "-3.14"], logs.append)

        self.assertEqual(uv_path, r"C:\Users\Admin\AppData\Roaming\Python\Scripts\uv.exe")
        self.assertEqual(run_command_mock.call_count, 1)
        self.assertTrue(any("uv was not found" in line for line in logs))
        self.assertTrue(any("Updating uv" in line for line in logs))

    def test_ensure_uv_for_mistral_logs_fallback_on_failure(self) -> None:
        logs: list[str] = []
        with (
            patch.object(m, "find_uv", side_effect=[None, None]),
            patch.object(m, "run_command", return_value=9),
        ):
            uv_path = m.ensure_uv_for_mistral(["py.exe", "-3.14"], logs.append)
        self.assertIsNone(uv_path)
        self.assertTrue(any("pip fallback will be used" in line for line in logs))

    def test_ensure_uv_for_mistral_logs_existing_and_missing_path_after_success(self) -> None:
        logs: list[str] = []
        with (
            patch.object(m, "find_uv", side_effect=[r"C:\Tools\uv.exe", None]),
            patch.object(m, "run_command", return_value=0),
        ):
            uv_path = m.ensure_uv_for_mistral(["py.exe", "-3.14"], logs.append)
        self.assertIsNone(uv_path)
        self.assertTrue(any("uv is already available" in line for line in logs))
        self.assertTrue(any("completed, but uv was not found on PATH yet" in line for line in logs))

    def test_ensure_mistral_vibe_dependencies_runs_python_pip_and_uv_steps(self) -> None:
        with (
            patch.object(m, "ensure_python_314_via_winget", return_value=["py.exe", "-3.14"]) as py_mock,
            patch.object(m, "ensure_pip3_for_python") as pip_mock,
            patch.object(m, "ensure_uv_for_mistral", return_value="uv.exe") as uv_mock,
        ):
            python_cmd, uv_exe = m.ensure_mistral_vibe_dependencies(lambda _msg: None)
        self.assertEqual(python_cmd, ["py.exe", "-3.14"])
        self.assertEqual(uv_exe, "uv.exe")
        py_mock.assert_called_once()
        pip_mock.assert_called_once_with(["py.exe", "-3.14"], unittest.mock.ANY)
        uv_mock.assert_called_once_with(["py.exe", "-3.14"], unittest.mock.ANY)

    def test_ensure_mistral_vibe_dependencies_uses_linux_python_branch(self) -> None:
        with (
            patch.object(m, "is_linux", return_value=True),
            patch.object(m, "ensure_python_for_mistral_on_linux", return_value=["/usr/bin/python3"]) as py_mock,
            patch.object(m, "ensure_pip3_for_python") as pip_mock,
            patch.object(m, "ensure_uv_for_mistral", return_value="/home/admin/.local/bin/uv") as uv_mock,
        ):
            python_cmd, uv_exe = m.ensure_mistral_vibe_dependencies(lambda _msg: None)
        self.assertEqual(python_cmd, ["/usr/bin/python3"])
        self.assertEqual(uv_exe, "/home/admin/.local/bin/uv")
        py_mock.assert_called_once()
        pip_mock.assert_called_once_with(["/usr/bin/python3"], unittest.mock.ANY, "Python 3.12+ (Linux)")
        uv_mock.assert_called_once_with(["/usr/bin/python3"], unittest.mock.ANY)

    def test_try_install_mistral_vibe_uses_uv_then_falls_back_to_pip(self) -> None:
        logs: list[str] = []
        spec = next(spec for spec in m.CLI_SPECS if spec.key == "mistral")
        with (
            patch.object(m, "ensure_mistral_vibe_dependencies", return_value=(["py.exe", "-3.14"], "uv.exe")),
            patch.object(m, "run_command", side_effect=[2, 0]) as run_command_mock,
        ):
            ok, pkg = m.try_install_mistral_vibe(spec, logs.append)

        self.assertTrue(ok)
        self.assertEqual(pkg, "mistral-vibe")
        self.assertEqual(run_command_mock.call_count, 2)
        first_args = run_command_mock.call_args_list[0].args[0]
        second_args = run_command_mock.call_args_list[1].args[0]
        self.assertEqual(first_args[:3], ["uv.exe", "tool", "install"])
        self.assertIn("--upgrade", first_args)
        self.assertEqual(second_args[:4], ["py.exe", "-3.14", "-m", "pip"])
        self.assertTrue(any("uv tool install failed" in line for line in logs))

    def test_try_install_mistral_vibe_returns_false_on_dependency_error(self) -> None:
        logs: list[str] = []
        spec = next(spec for spec in m.CLI_SPECS if spec.key == "mistral")
        with patch.object(m, "ensure_mistral_vibe_dependencies", side_effect=RuntimeError("Python 3.14 missing")):
            ok, detail = m.try_install_mistral_vibe(spec, logs.append)
        self.assertFalse(ok)
        self.assertEqual(detail, "Python 3.14 missing")
        self.assertTrue(any("Python 3.14 missing" in line for line in logs))

    def test_try_install_mistral_vibe_returns_success_on_uv_and_failure_on_pip(self) -> None:
        logs: list[str] = []
        spec = next(spec for spec in m.CLI_SPECS if spec.key == "mistral")
        with (
            patch.object(m, "ensure_mistral_vibe_dependencies", return_value=(["py.exe", "-3.14"], "uv.exe")),
            patch.object(m, "run_command", return_value=0),
        ):
            ok, pkg = m.try_install_mistral_vibe(spec, logs.append)
        self.assertTrue(ok)
        self.assertEqual(pkg, "mistral-vibe")

        logs = []
        with (
            patch.object(m, "ensure_mistral_vibe_dependencies", return_value=(["py.exe", "-3.14"], None)),
            patch.object(m, "run_command", return_value=7),
        ):
            ok, err = m.try_install_mistral_vibe(spec, logs.append)
        self.assertFalse(ok)
        self.assertIn("exit code 7", str(err))
        self.assertTrue(any("uv was not found; falling back to pip" in line for line in logs))

    def test_install_worker_logs_failure_and_sets_failed_status(self) -> None:
        dummy = DummyFrame()

        def boom(_selected, _enable_auto_update=True):
            raise RuntimeError("boom")

        dummy._run_install = boom  # type: ignore[attr-defined]
        m.InstallerFrame._install_worker(dummy, [])

        self.assertEqual(dummy.statuses[-1], "Failed")
        self.assertFalse(dummy.busy[-1])
        self.assertTrue(any("ERROR: boom" in line for line in dummy.logs))
        self.assertTrue(any("Traceback" in line for line in dummy.logs))

    def test_run_install_raises_when_npm_missing_after_node_setup(self) -> None:
        dummy = DummyFrame()
        dummy._run_install = types.MethodType(m.InstallerFrame._run_install, dummy)
        selected = [next(spec for spec in m.CLI_SPECS if spec.key == "codex")]

        with (
            patch.object(m, "is_admin", return_value=False),
            patch.object(m, "ensure_node_via_winget"),
            patch.object(m, "find_npm", return_value=None),
        ):
            with self.assertRaises(RuntimeError) as ctx:
                dummy._run_install(selected)
        self.assertIn("npm was not found after Node.js setup", str(ctx.exception))

    def test_run_install_raises_when_required_cli_install_fails(self) -> None:
        dummy = DummyFrame()
        dummy._run_install = types.MethodType(m.InstallerFrame._run_install, dummy)
        codex = next(spec for spec in m.CLI_SPECS if spec.key == "codex")

        with (
            patch.object(m, "is_admin", return_value=True),
            patch.object(m, "ensure_node_via_winget"),
            patch.object(m, "find_npm", return_value=r"C:\Fake\nodejs\npm.cmd"),
            patch.object(m, "get_cli_bin_dirs", return_value=[r"C:\Program Files\nodejs"]),
            patch.object(m, "add_dirs_to_path", return_value=([], None)),
            patch.object(m, "try_install_package_candidates", return_value=(False, "nope")),
        ):
            with self.assertRaises(RuntimeError) as ctx:
                dummy._run_install([codex])
        self.assertIn("Failed to install Codex CLI", str(ctx.exception))

    def test_run_install_logs_shortcut_failure_and_completes(self) -> None:
        dummy = DummyFrame()
        dummy._run_install = types.MethodType(m.InstallerFrame._run_install, dummy)
        codex = next(spec for spec in m.CLI_SPECS if spec.key == "codex")

        with (
            patch.object(m, "is_admin", return_value=True),
            patch.object(m, "ensure_node_via_winget"),
            patch.object(m, "find_npm", return_value=r"C:\Fake\nodejs\npm.cmd"),
            patch.object(
                m,
                "get_cli_bin_dirs",
                side_effect=[
                    [r"C:\Users\Admin\AppData\Roaming\npm"],
                    [r"C:\Users\Admin\AppData\Roaming\npm"],
                    [r"C:\Users\Admin\AppData\Roaming\npm"],
                ],
            ),
            patch.object(m, "add_dirs_to_path", return_value=([], None)),
            patch.object(m, "try_install_package_candidates", return_value=(True, "@openai/codex")),
            patch.object(m, "resolve_command_path", return_value=r"C:\Users\Admin\AppData\Roaming\npm\codex.cmd"),
            patch.object(m, "ensure_cli_auto_update_task", return_value=["@openai/codex"]),
            patch.object(m, "create_cli_desktop_shortcut", side_effect=RuntimeError("shortcut boom")),
        ):
            dummy._run_install([codex])

        self.assertTrue(any("Shortcut creation failed for Codex CLI" in line for line in dummy.logs))
        self.assertTrue(any("Next step: launch a shortcut" in line for line in dummy.logs))
        self.assertIn(98, dummy.gauges)

    def test_run_install_mistral_success_uses_python_cli_dirs_for_resolution(self) -> None:
        dummy = DummyFrame()
        dummy._run_install = types.MethodType(m.InstallerFrame._run_install, dummy)
        mistral = next(spec for spec in m.CLI_SPECS if spec.key == "mistral")

        def fake_auto_update(_npm_exe, _packages, log):
            log("Auto-update task unchanged: no newly installed npm CLI packages in this run.")
            return []

        with (
            patch.object(m, "is_admin", return_value=True),
            patch.object(m, "ensure_node_via_winget"),
            patch.object(m, "find_npm", return_value=r"C:\Fake\nodejs\npm.cmd"),
            patch.object(
                m,
                "get_cli_bin_dirs",
                side_effect=[
                    [r"C:\Users\Admin\AppData\Roaming\npm"],
                    [r"C:\Users\Admin\AppData\Roaming\npm"],
                    [r"C:\Users\Admin\AppData\Roaming\npm"],
                ],
            ),
            patch.object(m, "get_python_cli_bin_dirs", return_value=[r"C:\Users\Admin\AppData\Roaming\Python\Scripts"]),
            patch.object(m, "add_dirs_to_path", return_value=([], None)),
            patch.object(m, "try_install_mistral_vibe", return_value=(True, "mistral-vibe")),
            patch.object(m, "resolve_command_path", return_value=r"C:\Users\Admin\AppData\Roaming\Python\Scripts\vibe.exe") as resolve_mock,
            patch.object(m, "ensure_cli_auto_update_task", side_effect=fake_auto_update),
            patch.object(m, "create_cli_desktop_shortcut", return_value=r"C:\Users\Admin\Desktop\Mistral Vibe CLI.lnk"),
        ):
            dummy._run_install([mistral])

        self.assertTrue(any("Installed Mistral Vibe CLI using package mistral-vibe" in line for line in dummy.logs))
        self.assertTrue(any("Resolved command path for Mistral Vibe CLI" in line for line in dummy.logs))
        self.assertTrue(any("Auto-update task unchanged" in line for line in dummy.logs))
        extra_dirs = resolve_mock.call_args.args[1]
        self.assertIn(r"C:\Users\Admin\AppData\Roaming\Python\Scripts", extra_dirs)

    def test_run_install_ollama_success_uses_ollama_cli_dirs_for_resolution(self) -> None:
        dummy = DummyFrame()
        dummy._run_install = types.MethodType(m.InstallerFrame._run_install, dummy)
        ollama = next(spec for spec in m.CLI_SPECS if spec.key == "ollama")
        state: dict[str, object] = {"auto_update_packages": None}

        def fake_auto_update(_npm_exe, packages, log):
            state["auto_update_packages"] = list(packages)
            log("Auto-update task unchanged: no newly installed npm CLI packages in this run.")
            return []

        with (
            patch.object(m, "is_admin", return_value=True),
            patch.object(m, "ensure_node_via_winget"),
            patch.object(m, "find_npm", return_value=r"C:\Fake\nodejs\npm.cmd"),
            patch.object(
                m,
                "get_cli_bin_dirs",
                side_effect=[
                    [r"C:\Users\Admin\AppData\Roaming\npm"],
                    [r"C:\Users\Admin\AppData\Roaming\npm"],
                    [r"C:\Users\Admin\AppData\Roaming\npm"],
                ],
            ),
            patch.object(m, "get_ollama_cli_bin_dirs", return_value=[r"C:\Users\Admin\AppData\Local\Programs\Ollama"]),
            patch.object(m, "add_dirs_to_path", return_value=([], None)),
            patch.object(m, "ensure_ollama_via_winget", return_value=(True, m.OLLAMA_WINGET_ID)),
            patch.object(m, "resolve_command_path", return_value=r"C:\Users\Admin\AppData\Local\Programs\Ollama\ollama.exe") as resolve_mock,
            patch.object(m, "ensure_cli_auto_update_task", side_effect=fake_auto_update),
            patch.object(m, "create_cli_desktop_shortcut", return_value=r"C:\Users\Admin\Desktop\Ollama CLI.lnk"),
        ):
            dummy._run_install([ollama])

        self.assertTrue(any(f"Installed {ollama.label} using package {m.OLLAMA_WINGET_ID}" in line for line in dummy.logs))
        self.assertTrue(any("Resolved command path for Ollama CLI (Official)" in line for line in dummy.logs))
        self.assertEqual(state["auto_update_packages"], [])
        extra_dirs = resolve_mock.call_args.args[1]
        self.assertIn(r"C:\Users\Admin\AppData\Local\Programs\Ollama", extra_dirs)

    def test_run_install_logs_path_refresh_and_auto_update_warnings(self) -> None:
        dummy = DummyFrame()
        dummy._run_install = types.MethodType(m.InstallerFrame._run_install, dummy)
        codex = next(spec for spec in m.CLI_SPECS if spec.key == "codex")

        add_results = [
            ([], "user path denied"),
            ([r"C:\Program Files\nodejs"], None),
            ([], "user refresh denied"),
            ([r"C:\Program Files\nodejs"], None),
        ]

        with (
            patch.object(m, "is_admin", return_value=True),
            patch.object(m, "ensure_node_via_winget"),
            patch.object(m, "find_npm", return_value=r"C:\Fake\nodejs\npm.cmd"),
            patch.object(
                m,
                "get_cli_bin_dirs",
                side_effect=[
                    [r"C:\Program Files\nodejs"],
                    [r"C:\Program Files\nodejs"],
                    [r"C:\Program Files\nodejs"],
                ],
            ),
            patch.object(m, "add_dirs_to_path", side_effect=add_results),
            patch.object(m, "try_install_package_candidates", return_value=(True, "@openai/codex")),
            patch.object(m, "resolve_command_path", return_value=None),
            patch.object(m, "ensure_cli_auto_update_task", side_effect=RuntimeError("task failed")),
            patch.object(m, "create_cli_desktop_shortcut") as shortcut_mock,
        ):
            dummy._run_install([codex])

        shortcut_mock.assert_not_called()
        self.assertTrue(any("User PATH update warning: user path denied" in line for line in dummy.logs))
        self.assertTrue(any("Added to system PATH: C:\\Program Files\\nodejs" in line for line in dummy.logs))
        self.assertTrue(any("Warning: Could not resolve executable path for Codex CLI" in line for line in dummy.logs))
        self.assertTrue(any("User PATH refresh warning: user refresh denied" in line for line in dummy.logs))
        self.assertTrue(
            any("Added to system PATH (post-install): C:\\Program Files\\nodejs" in line for line in dummy.logs)
        )
        self.assertTrue(any("Auto-update task warning: task failed" in line for line in dummy.logs))

    def test_run_install_skips_auto_update_when_toggle_disabled(self) -> None:
        dummy = DummyFrame()
        dummy._run_install = types.MethodType(m.InstallerFrame._run_install, dummy)
        codex = next(spec for spec in m.CLI_SPECS if spec.key == "codex")

        with (
            patch.object(m, "is_admin", return_value=True),
            patch.object(m, "ensure_node_via_winget"),
            patch.object(m, "find_npm", return_value=r"C:\Fake\nodejs\npm.cmd"),
            patch.object(
                m,
                "get_cli_bin_dirs",
                side_effect=[
                    [r"C:\Users\Admin\AppData\Roaming\npm"],
                    [r"C:\Users\Admin\AppData\Roaming\npm"],
                    [r"C:\Users\Admin\AppData\Roaming\npm"],
                ],
            ),
            patch.object(m, "add_dirs_to_path", return_value=([], None)),
            patch.object(m, "try_install_package_candidates", return_value=(True, "@openai/codex")),
            patch.object(m, "resolve_command_path", return_value=r"C:\Users\Admin\AppData\Roaming\npm\codex.cmd"),
            patch.object(m, "ensure_cli_auto_update_task") as auto_update_mock,
            patch.object(m, "create_cli_desktop_shortcut"),
        ):
            dummy._run_install([codex], enable_auto_update=False)

        auto_update_mock.assert_not_called()
        self.assertTrue(any("Hidden auto-update task disabled for this run." in line for line in dummy.logs))

    def test_run_install_continues_when_required_cli_locked_but_existing_command_found(self) -> None:
        dummy = DummyFrame()
        dummy._run_install = types.MethodType(m.InstallerFrame._run_install, dummy)
        codex = next(spec for spec in m.CLI_SPECS if spec.key == "codex")
        state: dict[str, object] = {"auto_update_packages": None, "shortcuts": []}

        def fake_auto_update(_npm_exe, packages, _log):
            state["auto_update_packages"] = list(packages)
            return list(packages)

        def fake_shortcut(spec, cmd_path, _log):
            cast = state["shortcuts"]
            assert isinstance(cast, list)
            cast.append((spec.key, cmd_path))
            return r"C:\Users\Admin\Desktop\Codex CLI.lnk"

        with (
            patch.object(m, "is_admin", return_value=True),
            patch.object(m, "ensure_node_via_winget"),
            patch.object(m, "find_npm", return_value=r"C:\Program Files\nodejs\npm.cmd"),
            patch.object(
                m,
                "get_cli_bin_dirs",
                side_effect=[
                    [r"C:\Users\Admin\AppData\Roaming\npm"],  # pre-install
                    [r"C:\Users\Admin\AppData\Roaming\npm"],  # lock fallback resolve
                    [r"C:\Users\Admin\AppData\Roaming\npm"],  # refresh
                ],
            ),
            patch.object(m, "add_dirs_to_path", return_value=([], None)),
            patch.object(
                m,
                "try_install_package_candidates",
                return_value=(False, "@openai/codex failed with exit code 4294963214 (Windows errno -4082)"),
            ),
            patch.object(m, "resolve_command_path", return_value=r"C:\Users\Admin\AppData\Roaming\npm\codex.cmd"),
            patch.object(m, "ensure_cli_auto_update_task", side_effect=fake_auto_update),
            patch.object(m, "create_cli_desktop_shortcut", side_effect=fake_shortcut),
        ):
            dummy._run_install([codex])

        self.assertTrue(any("blocked by a locked file" in line for line in dummy.logs))
        self.assertTrue(any("Resolved existing command path for Codex CLI" in line for line in dummy.logs))
        self.assertEqual(state["auto_update_packages"], ["@openai/codex"])
        self.assertEqual(state["shortcuts"], [("codex", r"C:\Users\Admin\AppData\Roaming\npm\codex.cmd")])

    def test_install_worker_mocked_smoke(self) -> None:
        dummy = DummyFrame()
        dummy._run_install = types.MethodType(m.InstallerFrame._run_install, dummy)

        selected = [
            next(spec for spec in m.CLI_SPECS if spec.key == "codex"),
            next(spec for spec in m.CLI_SPECS if spec.key == "mistral"),
            next(spec for spec in m.CLI_SPECS if spec.key == "gemini"),
        ]

        state: dict[str, object] = {
            "bin_dirs_calls": 0,
            "shortcuts": [],
            "npm_installs": [],
            "mistral_installs": [],
            "path_updates": [],
            "auto_update_packages": None,
        }

        def fake_ensure_node(log):
            log("FAKE: Node.js/npm check/install passed")

        def fake_find_npm():
            return r"C:\Fake\nodejs\npm.cmd"

        def fake_get_cli_bin_dirs(_npm_exe, _log):
            state["bin_dirs_calls"] = int(state["bin_dirs_calls"]) + 1
            if int(state["bin_dirs_calls"]) <= 2:
                return [r"C:\Users\Admin\AppData\Roaming\npm"]
            return [r"C:\Users\Admin\AppData\Roaming\npm", r"C:\Program Files\nodejs"]

        def fake_add_dirs_to_path(scope, dirs):
            cast_list = state["path_updates"]
            assert isinstance(cast_list, list)
            cast_list.append((scope, tuple(dirs)))
            if scope == "user":
                return ([d for d in dirs if "Roaming\\npm" in d], None)
            return ([], "Access is denied")

        def fake_try_install(_npm_exe, spec, log):
            cast_list = state["npm_installs"]
            assert isinstance(cast_list, list)
            cast_list.append(spec.key)
            return (True, spec.package_candidates[0])

        def fake_try_install_mistral(spec, log):
            cast_list = state["mistral_installs"]
            assert isinstance(cast_list, list)
            cast_list.append(spec.key)
            log("FAKE: no working Mistral install candidate")
            return (False, "no candidate")

        def fake_resolve(command_candidates, _extra_dirs):
            cmd = command_candidates[0]
            return rf"C:\Users\Admin\AppData\Roaming\npm\{cmd}.cmd"

        def fake_shortcut(spec, cmd_path, log):
            cast_list = state["shortcuts"]
            assert isinstance(cast_list, list)
            cast_list.append((spec.key, cmd_path))
            log(f"FAKE: shortcut created for {spec.key}")
            return rf"C:\Users\Admin\Desktop\{spec.shortcut_name}.lnk"

        def fake_auto_update(_npm_exe, packages, log):
            state["auto_update_packages"] = list(packages)
            log("FAKE: hidden auto-update task configured")
            return list(packages)

        with (
            patch.object(m, "is_admin", return_value=False),
            patch.object(m, "ensure_node_via_winget", side_effect=fake_ensure_node),
            patch.object(m, "find_npm", side_effect=fake_find_npm),
            patch.object(m, "get_cli_bin_dirs", side_effect=fake_get_cli_bin_dirs),
            patch.object(m, "get_python_cli_bin_dirs", return_value=[]),
            patch.object(m, "add_dirs_to_path", side_effect=fake_add_dirs_to_path),
            patch.object(m, "try_install_package_candidates", side_effect=fake_try_install),
            patch.object(m, "try_install_mistral_vibe", side_effect=fake_try_install_mistral),
            patch.object(m, "resolve_command_path", side_effect=fake_resolve),
            patch.object(m, "ensure_cli_auto_update_task", side_effect=fake_auto_update),
            patch.object(m, "create_cli_desktop_shortcut", side_effect=fake_shortcut),
        ):
            m.InstallerFrame._install_worker(dummy, selected)

        self.assertEqual(dummy.statuses[-1], "Complete")
        self.assertEqual(dummy.gauges[-1], 100)
        self.assertFalse(dummy.busy[-1])
        self.assertTrue(any("Windows 11 AI CLI Installer started." in x for x in dummy.logs))
        self.assertTrue(any("Using npm executable:" in x for x in dummy.logs))
        self.assertTrue(any("Skipping optional Mistral Vibe CLI" in x for x in dummy.logs))
        self.assertTrue(any("Installed Codex CLI" in x for x in dummy.logs))
        self.assertTrue(any("Installed Gemini CLI" in x for x in dummy.logs))
        self.assertTrue(any("System PATH update warning:" in x for x in dummy.logs))
        self.assertTrue(any("hidden auto-update task configured" in x.lower() for x in dummy.logs))
        self.assertTrue(any("Installation workflow complete." in x for x in dummy.logs))
        self.assertTrue(any("Next step: launch a shortcut" in x for x in dummy.logs))
        self.assertEqual(
            state["shortcuts"],
            [
                ("codex", r"C:\Users\Admin\AppData\Roaming\npm\codex.cmd"),
                ("gemini", r"C:\Users\Admin\AppData\Roaming\npm\gemini.cmd"),
            ],
        )
        self.assertEqual(state["npm_installs"], ["codex", "gemini"])
        self.assertEqual(state["mistral_installs"], ["mistral"])
        self.assertEqual(state["auto_update_packages"], ["@openai/codex", "@google/gemini-cli"])
        system_updates = [dirs for scope, dirs in state["path_updates"] if scope == "system"]
        self.assertTrue(system_updates, "Expected at least one system PATH update call")
        self.assertTrue(
            all(not any("Roaming\\npm" in d for d in dirs) for dirs in system_updates),
            f"User npm dir should not be added to system PATH: {system_updates}",
        )

    def test_run_install_logs_persistent_log_path_when_available(self) -> None:
        dummy = DummyFrame()
        dummy._persistent_log_path = r"C:\Users\Admin\AppData\Local\InstallTheCli\gui_last_run.log"
        dummy._run_install = types.MethodType(m.InstallerFrame._run_install, dummy)

        with (
            patch.object(m, "is_admin", return_value=True),
            patch.object(m, "ensure_node_via_winget"),
            patch.object(m, "find_npm", return_value=r"C:\Fake\nodejs\npm.cmd"),
            patch.object(m, "get_cli_bin_dirs", side_effect=[[], []]),
            patch.object(m, "add_dirs_to_path", return_value=([], None)),
            patch.object(m, "filter_system_path_dirs", side_effect=lambda dirs: dirs),
            patch.object(m, "ensure_cli_auto_update_task"),
        ):
            dummy._run_install([], enable_auto_update=False)

        self.assertTrue(any("Persistent log file: " in line for line in dummy.logs))


class UiHandlerTests(unittest.TestCase):
    def test_append_log_writes_line_and_scrolls(self) -> None:
        dummy = types.SimpleNamespace(log_ctrl=DummyLogCtrl())
        m.InstallerFrame._append_log(dummy, "hello")
        self.assertEqual(dummy.log_ctrl.appended, ["hello\n"])

    def test_append_log_writes_persistent_file_when_path_available(self) -> None:
        with tempfile.TemporaryDirectory(dir=".") as tmp_dir:
            log_path = os.path.join(tmp_dir, "gui_last_run.log")
            dummy = types.SimpleNamespace(
                log_ctrl=DummyLogCtrl(),
                _persistent_log_path=log_path,
                _persistent_log_write_warning_shown=False,
            )
            m.InstallerFrame._append_log(dummy, "hello")
            with open(log_path, "r", encoding="utf-8") as f:
                self.assertEqual(f.read(), "hello\n")

    def test_append_log_warns_once_when_persistent_log_write_fails(self) -> None:
        dummy = types.SimpleNamespace(
            log_ctrl=DummyLogCtrl(),
            _persistent_log_path=r"C:\bad\gui_last_run.log",
            _persistent_log_write_warning_shown=False,
        )
        with patch.object(m, "append_persistent_log_line", side_effect=["disk full", "disk full"]):
            m.InstallerFrame._append_log(dummy, "first")
            m.InstallerFrame._append_log(dummy, "second")

        self.assertTrue(dummy._persistent_log_write_warning_shown)
        self.assertEqual(
            dummy.log_ctrl.appended,
            [
                "first\n",
                "Persistent log write warning: disk full\n",
                "second\n",
            ],
        )

    def test_reset_persistent_log_for_new_run_updates_state(self) -> None:
        dummy = types.SimpleNamespace(
            _persistent_log_path="old",
            _persistent_log_write_warning_shown=True,
        )
        with patch.object(m, "reset_gui_last_run_log", return_value="new.log"):
            m.InstallerFrame._reset_persistent_log_for_new_run(dummy)
        self.assertEqual(dummy._persistent_log_path, "new.log")
        self.assertFalse(dummy._persistent_log_write_warning_shown)

    def test_log_set_status_set_gauge_use_callafter(self) -> None:
        log_ctrl = DummyLogCtrl()
        status_label = types.SimpleNamespace(SetLabel=MagicMock())
        gauge = types.SimpleNamespace(SetValue=MagicMock())
        dummy = types.SimpleNamespace(log_ctrl=log_ctrl, status_label=status_label, gauge=gauge)
        dummy._append_log = types.MethodType(m.InstallerFrame._append_log, dummy)
        calls: list[str] = []

        def immediate(fn, *args, **kwargs):
            calls.append(getattr(fn, "__name__", str(fn)))
            return fn(*args, **kwargs)

        with patch.object(m.wx, "CallAfter", side_effect=immediate):
            m.InstallerFrame.log(dummy, "line")
            m.InstallerFrame.set_status(dummy, "Working")
            m.InstallerFrame.set_gauge(dummy, 150)

        self.assertEqual(log_ctrl.appended, ["line\n"])
        status_label.SetLabel.assert_called_once_with("Status: Working")
        gauge.SetValue.assert_called_once_with(100)
        self.assertTrue(calls)

    def test_set_busy_disables_buttons_and_pulses_when_busy(self) -> None:
        install_btn = types.SimpleNamespace(Enable=MagicMock())
        select_all_btn = types.SimpleNamespace(Enable=MagicMock())
        select_none_btn = types.SimpleNamespace(Enable=MagicMock())
        gauge = types.SimpleNamespace(Pulse=MagicMock())
        dummy = types.SimpleNamespace(
            install_btn=install_btn,
            select_all_btn=select_all_btn,
            select_none_btn=select_none_btn,
            gauge=gauge,
        )

        with patch.object(m.wx, "CallAfter", side_effect=lambda fn, *a, **k: fn(*a, **k)):
            m.InstallerFrame.set_busy(dummy, True)
            m.InstallerFrame.set_busy(dummy, False)

        install_btn.Enable.assert_any_call(False)
        install_btn.Enable.assert_any_call(True)
        select_all_btn.Enable.assert_any_call(False)
        select_none_btn.Enable.assert_any_call(False)
        gauge.Pulse.assert_called_once()

    def test_on_select_all_and_none_toggle_checkboxes(self) -> None:
        dummy = types.SimpleNamespace(checkboxes={"a": DummyCheckbox(False), "b": DummyCheckbox(True)})
        m.InstallerFrame.on_select_all(dummy, None)
        self.assertTrue(all(cb.GetValue() for cb in dummy.checkboxes.values()))
        m.InstallerFrame.on_select_none(dummy, None)
        self.assertTrue(all(not cb.GetValue() for cb in dummy.checkboxes.values()))

    def test_on_close_blocks_when_worker_thread_running(self) -> None:
        dummy = types.SimpleNamespace(worker_thread=DummyThreadState(True), Close=MagicMock())
        with patch.object(m.wx, "MessageBox") as msg_mock:
            m.InstallerFrame.on_close(dummy, None)
        msg_mock.assert_called_once()
        dummy.Close.assert_not_called()

    def test_on_close_closes_when_idle(self) -> None:
        dummy = types.SimpleNamespace(worker_thread=DummyThreadState(False), Close=MagicMock())
        m.InstallerFrame.on_close(dummy, None)
        dummy.Close.assert_called_once()

    def test_on_install_returns_if_worker_already_running(self) -> None:
        dummy = types.SimpleNamespace(worker_thread=DummyThreadState(True))
        with patch.object(m.wx, "MessageBox") as msg_mock:
            m.InstallerFrame.on_install(dummy, None)
        msg_mock.assert_not_called()

    def test_on_install_warns_when_no_cli_selected(self) -> None:
        dummy = types.SimpleNamespace(
            worker_thread=None,
            checkboxes={spec.key: DummyCheckbox(False) for spec in m.CLI_SPECS},
            auto_update_checkbox=DummyCheckbox(True),
            log_ctrl=DummyLogCtrl(),
            set_status=MagicMock(),
            set_gauge=MagicMock(),
            set_busy=MagicMock(),
            _install_worker=MagicMock(),
        )
        with patch.object(m.wx, "MessageBox") as msg_mock:
            m.InstallerFrame.on_install(dummy, None)
        msg_mock.assert_called_once()
        self.assertFalse(dummy.log_ctrl.cleared)
        dummy.set_busy.assert_not_called()

    def test_on_install_starts_worker_thread_for_selected_clis(self) -> None:
        class ThreadSpy:
            instances: list["ThreadSpy"] = []

            def __init__(self, target, args, daemon) -> None:
                self.target = target
                self.args = args
                self.daemon = daemon
                self.started = False
                ThreadSpy.instances.append(self)

            def start(self) -> None:
                self.started = True

            def is_alive(self) -> bool:
                return self.started

        selected_key = "codex"
        dummy = types.SimpleNamespace(
            worker_thread=None,
            checkboxes={spec.key: DummyCheckbox(spec.key == selected_key) for spec in m.CLI_SPECS},
            log_ctrl=DummyLogCtrl(),
            set_status=MagicMock(),
            set_gauge=MagicMock(),
            set_busy=MagicMock(),
            _install_worker=MagicMock(),
            auto_update_checkbox=DummyCheckbox(True),
        )

        with patch.object(m.threading, "Thread", ThreadSpy):
            m.InstallerFrame.on_install(dummy, None)

        self.assertTrue(dummy.log_ctrl.cleared)
        dummy.set_status.assert_called_once_with("Starting...")
        dummy.set_gauge.assert_called_once_with(0)
        dummy.set_busy.assert_called_once_with(True)
        self.assertIsNotNone(dummy.worker_thread)
        self.assertTrue(ThreadSpy.instances[0].started)
        self.assertTrue(ThreadSpy.instances[0].daemon)
        chosen = ThreadSpy.instances[0].args[0]
        self.assertEqual([spec.key for spec in chosen], [selected_key])
        self.assertTrue(ThreadSpy.instances[0].args[1])
        self.assertTrue(dummy.worker_thread.is_alive())

    def test_on_install_resets_persistent_log_file_when_helper_present(self) -> None:
        class ThreadSpy:
            instances: list["ThreadSpy"] = []

            def __init__(self, target, args, daemon) -> None:
                self.args = args
                ThreadSpy.instances.append(self)

            def start(self) -> None:
                return None

        reset_mock = MagicMock()
        selected_key = "codex"
        dummy = types.SimpleNamespace(
            worker_thread=None,
            checkboxes={spec.key: DummyCheckbox(spec.key == selected_key) for spec in m.CLI_SPECS},
            log_ctrl=DummyLogCtrl(),
            set_status=MagicMock(),
            set_gauge=MagicMock(),
            set_busy=MagicMock(),
            _install_worker=MagicMock(),
            auto_update_checkbox=DummyCheckbox(True),
            _reset_persistent_log_for_new_run=reset_mock,
        )

        with patch.object(m.threading, "Thread", ThreadSpy):
            m.InstallerFrame.on_install(dummy, None)

        reset_mock.assert_called_once()

    def test_on_install_respects_auto_update_toggle_off(self) -> None:
        class ThreadSpy:
            instances: list["ThreadSpy"] = []

            def __init__(self, target, args, daemon) -> None:
                self.target = target
                self.args = args
                self.daemon = daemon
                ThreadSpy.instances.append(self)

            def start(self) -> None:
                return None

        selected_key = "codex"
        dummy = types.SimpleNamespace(
            worker_thread=None,
            checkboxes={spec.key: DummyCheckbox(spec.key == selected_key) for spec in m.CLI_SPECS},
            auto_update_checkbox=DummyCheckbox(False),
            log_ctrl=DummyLogCtrl(),
            set_status=MagicMock(),
            set_gauge=MagicMock(),
            set_busy=MagicMock(),
            _install_worker=MagicMock(),
        )

        with patch.object(m.threading, "Thread", ThreadSpy):
            m.InstallerFrame.on_install(dummy, None)

        self.assertFalse(ThreadSpy.instances[0].args[1])

    def test_on_install_defaults_auto_update_enabled_when_toggle_missing(self) -> None:
        class ThreadSpy:
            instances: list["ThreadSpy"] = []

            def __init__(self, target, args, daemon) -> None:
                self.args = args
                ThreadSpy.instances.append(self)

            def start(self) -> None:
                return None

        selected_key = "codex"
        dummy = types.SimpleNamespace(
            worker_thread=None,
            checkboxes={spec.key: DummyCheckbox(spec.key == selected_key) for spec in m.CLI_SPECS},
            log_ctrl=DummyLogCtrl(),
            set_status=MagicMock(),
            set_gauge=MagicMock(),
            set_busy=MagicMock(),
            _install_worker=MagicMock(),
        )

        with patch.object(m.threading, "Thread", ThreadSpy):
            m.InstallerFrame.on_install(dummy, None)

        self.assertTrue(ThreadSpy.instances[0].args[1])


class AppEntrypointTests(unittest.TestCase):
    def test_installer_app_oninit_rejects_unsupported_os(self) -> None:
        with (
            patch.object(m, "is_windows", return_value=False),
            patch.object(m, "is_linux", return_value=False),
            patch.object(m.wx, "MessageBox") as msg_mock,
        ):
            ok = m.InstallerApp.OnInit(types.SimpleNamespace())
        self.assertFalse(ok)
        msg_mock.assert_called_once()

    def test_installer_app_oninit_creates_and_shows_frame(self) -> None:
        frame = types.SimpleNamespace(Show=MagicMock())
        with (
            patch.object(m, "is_windows", return_value=True),
            patch.object(m, "is_linux", return_value=False),
            patch.object(m, "InstallerFrame", return_value=frame) as frame_cls,
        ):
            ok = m.InstallerApp.OnInit(types.SimpleNamespace())
        self.assertTrue(ok)
        frame_cls.assert_called_once()
        frame.Show.assert_called_once_with()

    def test_installer_app_oninit_allows_linux(self) -> None:
        frame = types.SimpleNamespace(Show=MagicMock())
        with (
            patch.object(m, "is_windows", return_value=False),
            patch.object(m, "is_linux", return_value=True),
            patch.object(m, "InstallerFrame", return_value=frame) as frame_cls,
            patch.object(m.wx, "MessageBox") as msg_mock,
        ):
            ok = m.InstallerApp.OnInit(types.SimpleNamespace())
        self.assertTrue(ok)
        frame_cls.assert_called_once()
        frame.Show.assert_called_once_with()
        msg_mock.assert_not_called()

    def test_main_runs_app_loop_and_returns_zero(self) -> None:
        app = types.SimpleNamespace(MainLoop=MagicMock())
        with patch.object(m, "InstallerApp", return_value=app) as app_cls:
            rc = m.main()
        self.assertEqual(rc, 0)
        app_cls.assert_called_once_with(False)
        app.MainLoop.assert_called_once_with()


if __name__ == "__main__":  # pragma: no cover
    unittest.main(verbosity=2)
