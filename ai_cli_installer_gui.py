import ctypes
import glob
import os
import shutil
import subprocess
import sys
import threading
import time
import traceback
from dataclasses import dataclass
from typing import Callable, Optional

import wx

try:  # Windows-only
    import winreg
except ImportError:  # pragma: no cover - exercised on non-Windows only
    winreg = None  # type: ignore[assignment]


CREATE_NO_WINDOW = 0x08000000
WM_SETTINGCHANGE = 0x001A
SMTO_ABORTIFHUNG = 0x0002
NODE_WINGET_ID = "OpenJS.NodeJS.LTS"
PYTHON_314_WINGET_ID = "Python.Python.3.14"
OLLAMA_WINGET_ID = "Ollama.Ollama"
LINUX_OLLAMA_INSTALL_URL = "https://ollama.com/install.sh"
AUTO_UPDATE_TASK_NAME = "InstallTheCli - Update AI CLIs"
AUTO_UPDATE_DAILY_TIME = "3:00AM"
AUTO_UPDATE_DIR_NAME = "InstallTheCli"
AUTO_UPDATE_PACKAGES_FILE = "auto_update_packages.txt"
AUTO_UPDATE_SCRIPT_FILE = "auto_update_clis.ps1"
GUI_LAST_RUN_LOG_FILE = "gui_last_run.log"
NPM_INSTALL_MAX_ATTEMPTS = 3
NPM_INSTALL_RETRY_DELAY_SECONDS = 2.0
NPM_QUIET_FLAGS = ["--no-fund", "--no-audit", "--no-update-notifier", "--loglevel", "error"]
PIP_QUIET_FLAGS = ["--disable-pip-version-check", "--no-input", "--quiet"]


@dataclass(frozen=True)
class CliSpec:
    key: str
    label: str
    help_text: str
    package_candidates: tuple[str, ...]
    command_candidates: tuple[str, ...]
    shortcut_name: str
    optional: bool = False


CLI_SPECS: tuple[CliSpec, ...] = (
    CliSpec(
        key="claude",
        label="Claude CLI",
        help_text="Installs Anthropic Claude Code CLI from npm.",
        package_candidates=("@anthropic-ai/claude-code",),
        command_candidates=("claude",),
        shortcut_name="Claude CLI",
    ),
    CliSpec(
        key="codex",
        label="Codex CLI",
        help_text="Installs OpenAI Codex CLI from npm.",
        package_candidates=("@openai/codex",),
        command_candidates=("codex",),
        shortcut_name="Codex CLI",
    ),
    CliSpec(
        key="gemini",
        label="Gemini CLI",
        help_text="Installs Google Gemini CLI from npm.",
        package_candidates=("@google/gemini-cli",),
        command_candidates=("gemini",),
        shortcut_name="Gemini CLI",
    ),
    CliSpec(
        key="grok",
        label="Grok CLI (Vibe Kit)",
        help_text="Optional: installs Grok CLI from npm (@vibe-kit/grok-cli).",
        package_candidates=("@vibe-kit/grok-cli",),
        command_candidates=("grok", "grok-cli"),
        shortcut_name="Grok CLI",
        optional=True,
    ),
    CliSpec(
        key="qwen",
        label="Qwen CLI",
        help_text="Installs Qwen coding CLI from npm.",
        package_candidates=("@qwen-code/qwen-code", "qwen-code"),
        command_candidates=("qwen", "qwen-code"),
        shortcut_name="Qwen CLI",
    ),
    CliSpec(
        key="mistral",
        label="Mistral Vibe CLI",
        help_text="Installs Mistral Vibe CLI from https://docs.mistral.ai/mistral-vibe/introduction (Windows: Python 3.14 + uv/pip; Linux: Python 3.12+ + uv/pip).",
        package_candidates=("mistral-vibe",),
        command_candidates=("vibe", "mistral-vibe"),
        shortcut_name="Mistral Vibe CLI",
        optional=True,
    ),
    CliSpec(
        key="ollama",
        label="Ollama CLI (Official)",
        help_text="Installs official Ollama (Windows: winget Ollama.Ollama; Linux: official install script), including the ollama CLI.",
        package_candidates=(OLLAMA_WINGET_ID,),
        command_candidates=("ollama",),
        shortcut_name="Ollama CLI",
    ),
    CliSpec(
        key="copilot",
        label="GitHub Copilot CLI",
        help_text="Installs GitHub Copilot CLI from npm.",
        package_candidates=("@github/copilot", "@githubnext/github-copilot-cli"),
        command_candidates=("copilot", "github-copilot-cli", "github-copilot"),
        shortcut_name="GitHub Copilot CLI",
    ),
    CliSpec(
        key="openclaw",
        label="OpenClaw CLI",
        help_text="Installs OpenClaw AI CLI from npm (Node 22+ required).",
        package_candidates=("openclaw",),
        command_candidates=("openclaw",),
        shortcut_name="OpenClaw CLI",
        optional=True,
    ),
    CliSpec(
        key="ironclaw",
        label="IronClaw CLI",
        help_text="Installs IronClaw personal AI assistant CLI from npm (Node 22+ required).",
        package_candidates=("ironclaw",),
        command_candidates=("ironclaw",),
        shortcut_name="IronClaw CLI",
        optional=True,
    ),
)


def is_windows() -> bool:
    return os.name == "nt"


def is_linux() -> bool:
    return sys.platform.startswith("linux")


def is_admin() -> bool:
    if not is_windows():
        geteuid = getattr(os, "geteuid", None)
        if callable(geteuid):
            try:
                return geteuid() == 0
            except OSError:
                return False
        return False
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def broadcast_environment_change() -> None:
    if not is_windows():
        return
    try:
        result = ctypes.c_ulong()
        ctypes.windll.user32.SendMessageTimeoutW(
            0xFFFF,
            WM_SETTINGCHANGE,
            0,
            "Environment",
            SMTO_ABORTIFHUNG,
            5000,
            ctypes.byref(result),
        )
    except Exception:
        pass


def subprocess_creationflags_kwargs() -> dict[str, int]:
    if is_windows():
        return {"creationflags": CREATE_NO_WINDOW}
    return {}


def read_linux_os_release() -> dict[str, str]:
    data: dict[str, str] = {}
    if not is_linux():
        return data
    try:
        with open("/etc/os-release", "r", encoding="utf-8") as f:
            for raw_line in f:
                line = raw_line.strip()
                if not line or "=" not in line or line.startswith("#"):
                    continue
                key, value = line.split("=", 1)
                data[key] = value.strip().strip('"').strip("'")
    except OSError:
        return {}
    return data


def detect_linux_distro_family() -> Optional[str]:
    if not is_linux():
        return None
    info = read_linux_os_release()
    values = [info.get("ID", ""), info.get("ID_LIKE", "")]
    haystack = " ".join(v.lower() for v in values if v)
    if any(token in haystack for token in ("ubuntu", "debian")):
        return "debian"
    if any(token in haystack for token in ("fedora", "rhel", "centos")):
        return "fedora"
    if "arch" in haystack:
        return "arch"
    return None


def linux_requires_root_for_system_install() -> bool:
    return is_linux()


def ensure_linux_root_for_package_installs(log: Callable[[str], None]) -> bool:
    if not linux_requires_root_for_system_install():
        return True
    if is_admin():
        return True
    log("Linux package installation requires root privileges. Re-run the installer with sudo/root.")
    return False


def pip_install_flags_for_platform() -> list[str]:
    flags = list(PIP_QUIET_FLAGS)
    if is_linux():
        flags.append("--break-system-packages")
    return flags


def split_path(value: str) -> list[str]:
    if not value:
        return []
    return [part for part in value.split(";") if part]


def normalize_path_for_compare(path: str) -> str:
    expanded = os.path.expandvars(path.strip())
    normalized = os.path.normpath(expanded)
    return os.path.normcase(normalized)


def is_path_within(path: str, root: str) -> bool:
    try:
        norm_path = normalize_path_for_compare(path)
        norm_root = normalize_path_for_compare(root)
        return os.path.commonpath([norm_path, norm_root]) == norm_root
    except (ValueError, OSError):
        return False


def add_dirs_to_path(scope: str, dirs: list[str]) -> tuple[list[str], Optional[str]]:
    if not dirs:
        return ([], None)

    dirs = [d for d in dirs if d and os.path.isdir(os.path.expandvars(d))]
    if not dirs:
        return ([], None)

    if not is_windows():
        if scope == "system":
            # Linux installs typically land in standard system paths. We avoid mutating global shell config here.
            return ([], None)
        if scope != "user":
            raise ValueError(f"Unsupported scope: {scope}")
        profile_path = os.path.join(os.path.expanduser("~"), ".profile")
        try:
            existing_text = ""
            if os.path.isfile(profile_path):
                with open(profile_path, "r", encoding="utf-8") as f:
                    existing_text = f.read()
            current_env_parts = {normalize_path_for_compare(p) for p in os.environ.get("PATH", "").split(os.pathsep) if p}
            added: list[str] = []
            lines_to_append: list[str] = []
            for directory in dirs:
                norm = normalize_path_for_compare(directory)
                marker = f"InstallTheCli PATH {directory}"
                if norm in current_env_parts:
                    continue
                if marker in existing_text:
                    continue
                lines_to_append.append(f'export PATH="$PATH:{directory}"  # {marker}')
                added.append(directory)
                current_env_parts.add(norm)
            if lines_to_append:
                with open(profile_path, "a", encoding="utf-8", newline="\n") as f:
                    if existing_text and not existing_text.endswith("\n"):
                        f.write("\n")
                    for line in lines_to_append:
                        f.write(line + "\n")
            return (added, None)
        except OSError as exc:
            return ([], str(exc))

    if scope == "user":
        root = winreg.HKEY_CURRENT_USER
        subkey = r"Environment"
    elif scope == "system":
        root = winreg.HKEY_LOCAL_MACHINE
        subkey = r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment"
    else:
        raise ValueError(f"Unsupported scope: {scope}")

    added: list[str] = []
    try:
        with winreg.OpenKey(root, subkey, 0, winreg.KEY_READ | winreg.KEY_WRITE) as key:
            try:
                existing_value, reg_type = winreg.QueryValueEx(key, "Path")
            except FileNotFoundError:
                existing_value, reg_type = "", winreg.REG_EXPAND_SZ

            parts = split_path(existing_value)
            seen = {normalize_path_for_compare(p) for p in parts}
            for directory in dirs:
                norm = normalize_path_for_compare(directory)
                if norm not in seen:
                    parts.append(directory)
                    seen.add(norm)
                    added.append(directory)

            if added:
                new_value = ";".join(parts)
                if reg_type not in (winreg.REG_EXPAND_SZ, winreg.REG_SZ):
                    reg_type = winreg.REG_EXPAND_SZ
                winreg.SetValueEx(key, "Path", 0, reg_type, new_value)
    except PermissionError as exc:
        return ([], str(exc))
    except OSError as exc:
        return ([], str(exc))

    if added:
        broadcast_environment_change()
    return (added, None)


def find_desktop_directory() -> str:
    candidates: list[str] = []

    if not is_windows():
        home = os.path.expanduser("~")
        candidates.append(os.path.join(home, "Desktop"))
        xdg_desktop = os.environ.get("XDG_DESKTOP_DIR")
        if xdg_desktop:
            candidates.append(os.path.expandvars(xdg_desktop))
        for path in candidates:
            if path and os.path.isdir(path):
                return path
        return candidates[0]

    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders",
        ) as key:
            value, _ = winreg.QueryValueEx(key, "Desktop")
            if value:
                candidates.append(os.path.expandvars(value))
    except OSError:
        pass

    home = os.path.expanduser("~")
    candidates.append(os.path.join(home, "Desktop"))
    candidates.append(os.path.join(home, "OneDrive", "Desktop"))

    for path in candidates:
        if path and os.path.isdir(path):
            return path
    return candidates[0]


def powershell_single_quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def dedupe_preserve_order(values: list[str]) -> list[str]:
    unique: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value not in seen:
            unique.append(value)
            seen.add(value)
    return unique


def get_app_support_directory() -> str:
    if is_linux():
        xdg_state = os.environ.get("XDG_STATE_HOME")
        if xdg_state:
            return os.path.join(xdg_state, AUTO_UPDATE_DIR_NAME)
        return os.path.join(os.path.expanduser("~"), ".local", "state", AUTO_UPDATE_DIR_NAME)
    local_app = os.environ.get("LocalAppData")
    if local_app:
        return os.path.join(local_app, AUTO_UPDATE_DIR_NAME)
    return os.path.join(os.path.expanduser("~"), "AppData", "Local", AUTO_UPDATE_DIR_NAME)


def get_gui_last_run_log_path() -> str:
    return os.path.join(get_app_support_directory(), GUI_LAST_RUN_LOG_FILE)


def reset_gui_last_run_log() -> Optional[str]:
    path = get_gui_last_run_log_path()
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            started = time.strftime("%Y-%m-%d %H:%M:%S")
            f.write(f"InstallTheCli GUI log started: {started}\n")
        return path
    except OSError:
        return None


def append_persistent_log_line(path: Optional[str], message: str) -> Optional[str]:
    if not path:
        return None
    try:
        with open(path, "a", encoding="utf-8", newline="\n") as f:
            f.write(message + "\n")
        return None
    except OSError as exc:
        return str(exc)


def read_nonempty_lines(path: str) -> list[str]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return [line.strip() for line in f if line.strip()]
    except FileNotFoundError:
        return []


def write_nonempty_lines(path: str, values: list[str]) -> None:
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        for value in values:
            value = value.strip()
            if value:
                f.write(value + "\n")


def write_text_file(path: str, content: str) -> None:
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(content)


def build_cli_auto_update_script(npm_exe: str, packages_file: str) -> str:
    npm_quiet_args = " ".join(
        powershell_single_quote(flag) for flag in NPM_QUIET_FLAGS
    )
    lines = [
        "$ErrorActionPreference = 'Stop'",
        "$ProgressPreference = 'SilentlyContinue'",
        f"$npm = {powershell_single_quote(npm_exe)}",
        "$npmDir = Split-Path -Parent $npm",
        "if ($npmDir) { $env:PATH = $npmDir + ';' + [string]$env:PATH }",
        "$env:npm_config_update_notifier = 'false'",
        f"$packagesFile = {powershell_single_quote(packages_file)}",
        "if (-not (Test-Path -LiteralPath $npm)) { exit 0 }",
        "if (-not (Test-Path -LiteralPath $packagesFile)) { exit 0 }",
        "$packages = Get-Content -LiteralPath $packagesFile -ErrorAction SilentlyContinue | ForEach-Object { $_.Trim() } | Where-Object { $_ }",
        "if (-not $packages -or $packages.Count -eq 0) { exit 0 }",
        f"$null = & $npm {npm_quiet_args} 'update' '-g' @packages *>&1",
        "if ($LASTEXITCODE -is [int]) { exit $LASTEXITCODE }",
        "exit 0",
    ]
    return "\n".join(lines) + "\n"


def ensure_cli_auto_update_task(
    npm_exe: str,
    package_names: list[str],
    log: Callable[[str], None],
) -> list[str]:
    if not is_windows():
        log("Hidden auto-update scheduler is currently Windows-only; skipping on Linux.")
        return []
    clean_packages = dedupe_preserve_order([p.strip() for p in package_names if p and p.strip()])
    if not clean_packages:
        log("Auto-update task unchanged: no newly installed npm CLI packages in this run.")
        return []

    support_dir = get_app_support_directory()
    os.makedirs(support_dir, exist_ok=True)

    packages_file = os.path.join(support_dir, AUTO_UPDATE_PACKAGES_FILE)
    script_file = os.path.join(support_dir, AUTO_UPDATE_SCRIPT_FILE)

    existing_packages = read_nonempty_lines(packages_file)
    merged_packages = dedupe_preserve_order(existing_packages + clean_packages)

    write_nonempty_lines(packages_file, merged_packages)
    write_text_file(script_file, build_cli_auto_update_script(npm_exe, packages_file))

    action_args = f'-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "{script_file}"'
    register_lines = [
        "$currentUser = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name",
        f"$action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument {powershell_single_quote(action_args)}",
        "$triggerStartup = New-ScheduledTaskTrigger -AtStartup",
        "$triggerLogon = New-ScheduledTaskTrigger -AtLogOn",
        f"$triggerDaily = New-ScheduledTaskTrigger -Daily -At {powershell_single_quote(AUTO_UPDATE_DAILY_TIME)}",
        "$settings = New-ScheduledTaskSettingsSet -Hidden -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries",
        "$principal = New-ScheduledTaskPrincipal -UserId $currentUser -LogonType Interactive -RunLevel Limited",
        "Register-ScheduledTask "
        + f"-TaskName {powershell_single_quote(AUTO_UPDATE_TASK_NAME)} "
        + "-Action $action "
        + "-Trigger @($triggerStartup, $triggerLogon, $triggerDaily) "
        + "-Settings $settings "
        + "-Principal $principal "
        + f"-Description {powershell_single_quote('Hidden npm AI CLI auto-update (user logon + daily) created by InstallTheCli.')} "
        + "-Force | Out-Null",
    ]

    try:
        subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-Command",
                "; ".join(register_lines),
            ],
            check=True,
            capture_output=True,
            text=True,
            **subprocess_creationflags_kwargs(),
        )
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or str(exc)).strip()
        raise RuntimeError(
            "Unable to configure hidden CLI auto-update task. "
            + (detail if detail else "Task Scheduler registration failed.")
        ) from exc
    except OSError as exc:
        raise RuntimeError(f"Unable to configure hidden CLI auto-update task: {exc}") from exc

    log(
        "Configured hidden CLI auto-update task (startup, user logon, and daily "
        + AUTO_UPDATE_DAILY_TIME
        + ")."
    )
    return merged_packages


def create_windows_shortcut(
    shortcut_path: str,
    target_path: str,
    arguments: str = "",
    working_directory: str = "",
    icon_location: str = "",
) -> None:
    script_lines = [
        "$ws = New-Object -ComObject WScript.Shell",
        f"$sc = $ws.CreateShortcut({powershell_single_quote(shortcut_path)})",
        f"$sc.TargetPath = {powershell_single_quote(target_path)}",
    ]
    if arguments:
        script_lines.append(f"$sc.Arguments = {powershell_single_quote(arguments)}")
    if working_directory:
        script_lines.append(
            f"$sc.WorkingDirectory = {powershell_single_quote(working_directory)}"
        )
    if icon_location:
        script_lines.append(f"$sc.IconLocation = {powershell_single_quote(icon_location)}")
    script_lines.append("$sc.Save()")

    subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", "; ".join(script_lines)],
        check=True,
        capture_output=True,
        text=True,
        **subprocess_creationflags_kwargs(),
    )


def run_command(
    args: list[str],
    log: Callable[[str], None],
    env: Optional[dict[str, str]] = None,
    cwd: Optional[str] = None,
) -> int:
    log("> " + " ".join(args))
    process = subprocess.Popen(
        args,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        cwd=cwd,
        **subprocess_creationflags_kwargs(),
    )
    assert process.stdout is not None
    for line in process.stdout:
        text = line.rstrip()
        if text:
            log(text)
    return process.wait()


def command_exists(name: str, env: Optional[dict[str, str]] = None) -> bool:
    probe = ["where", name] if is_windows() else ["which", name]
    try:
        completed = subprocess.run(
            probe,
            capture_output=True,
            text=True,
            env=env,
            **subprocess_creationflags_kwargs(),
        )
        return completed.returncode == 0
    except OSError:
        return False


def where_all(name: str, env: Optional[dict[str, str]] = None) -> list[str]:
    probe = ["where", name] if is_windows() else ["which", "-a", name]
    try:
        completed = subprocess.run(
            probe,
            capture_output=True,
            text=True,
            env=env,
            **subprocess_creationflags_kwargs(),
        )
    except OSError:
        return []
    if completed.returncode != 0:
        return []
    return [line.strip() for line in completed.stdout.splitlines() if line.strip()]


def find_winget() -> Optional[str]:
    return shutil.which("winget")


def find_uv() -> Optional[str]:
    for name in ("uv.exe", "uv"):
        path = shutil.which(name)
        if path:
            return path
    return None


def find_python_launcher() -> Optional[str]:
    for name in ("py.exe", "py", "python.exe", "python"):
        path = shutil.which(name)
        if path:
            return path
    return None


def find_pip3() -> Optional[str]:
    for name in ("pip3.exe", "pip3", "pip.exe", "pip"):
        path = shutil.which(name)
        if path:
            return path
    return None


def find_ollama() -> Optional[str]:
    for name in ("ollama.exe", "ollama"):
        path = shutil.which(name)
        if path:
            return path

    if is_linux():
        for candidate in ("/usr/local/bin/ollama", "/usr/bin/ollama"):
            if os.path.isfile(candidate):
                return candidate
        return None

    local_app = os.environ.get("LocalAppData", "")
    program_files = os.environ.get("ProgramFiles", r"C:\Program Files")
    program_files_x86 = os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")
    candidates = [
        os.path.join(local_app, "Programs", "Ollama", "ollama.exe") if local_app else "",
        os.path.join(program_files, "Ollama", "ollama.exe"),
        os.path.join(program_files_x86, "Ollama", "ollama.exe"),
    ]
    for candidate in candidates:
        if candidate and os.path.isfile(candidate):
            return candidate
    return None


def get_python_version(prefix_args: list[str]) -> Optional[tuple[int, int, int]]:
    try:
        completed = subprocess.run(
            [
                *prefix_args,
                "-c",
                "import sys; print(f'{sys.version_info[0]}.{sys.version_info[1]}.{sys.version_info[2]}')",
            ],
            capture_output=True,
            text=True,
            **subprocess_creationflags_kwargs(),
        )
    except OSError:
        return None
    if completed.returncode != 0:
        return None
    text = (completed.stdout or "").strip()
    try:
        major_s, minor_s, patch_s = text.split(".", 2)
        return (int(major_s), int(minor_s), int(patch_s))
    except (TypeError, ValueError):
        return None


def find_python_314_command() -> Optional[list[str]]:
    for py_name in ("py.exe", "py"):
        py_path = shutil.which(py_name)
        if not py_path:
            continue
        prefix = [py_path, "-3.14"]
        version = get_python_version(prefix)
        if version and version[:2] == (3, 14):
            return prefix

    for name in ("python3.14.exe", "python3.14"):
        path = shutil.which(name)
        if not path:
            continue
        version = get_python_version([path])
        if version and version[:2] == (3, 14):
            return [path]

    local_app = os.environ.get("LocalAppData", "")
    program_files = os.environ.get("ProgramFiles", r"C:\Program Files")
    program_files_x86 = os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")
    known_python_314_paths = [
        os.path.join(local_app, "Programs", "Python", "Python314", "python.exe") if local_app else "",
        os.path.join(program_files, "Python314", "python.exe"),
        os.path.join(program_files, "Python", "Python314", "python.exe"),
        os.path.join(program_files_x86, "Python314", "python.exe"),
        os.path.join(program_files_x86, "Python", "Python314", "python.exe"),
    ]
    for path in known_python_314_paths:
        if not path or not os.path.isfile(path):
            continue
        version = get_python_version([path])
        if version and version[:2] == (3, 14):
            return [path]

    for name in ("python.exe", "python"):
        path = shutil.which(name)
        if not path:
            continue
        version = get_python_version([path])
        if version and version[:2] == (3, 14):
            return [path]
    return None


def find_node() -> Optional[str]:
    for name in ("node.exe", "node"):
        path = shutil.which(name)
        if path:
            return path

    program_files = os.environ.get("ProgramFiles", r"C:\Program Files")
    local_app = os.environ.get("LocalAppData", "")
    program_files_x86 = os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")

    candidates = [
        os.path.join(program_files, "nodejs", "node.exe"),
        os.path.join(program_files_x86, "nodejs", "node.exe"),
    ]
    if local_app:
        candidates.append(os.path.join(local_app, "Programs", "nodejs", "node.exe"))

    for candidate in candidates:
        if os.path.isfile(candidate):
            return candidate
    return None


def find_npm() -> Optional[str]:
    for name in ("npm.cmd", "npm"):
        path = shutil.which(name)
        if path:
            return path

    program_files = os.environ.get("ProgramFiles", r"C:\Program Files")
    local_app = os.environ.get("LocalAppData", "")
    program_files_x86 = os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")

    candidates = [
        os.path.join(program_files, "nodejs", "npm.cmd"),
        os.path.join(program_files_x86, "nodejs", "npm.cmd"),
    ]
    if local_app:
        candidates.append(os.path.join(local_app, "Programs", "nodejs", "npm.cmd"))

    for candidate in candidates:
        if os.path.isfile(candidate):
            return candidate
    return None


def get_npm_global_prefix(npm_exe: str, log: Callable[[str], None]) -> Optional[str]:
    env = os.environ.copy()
    npm_dir = os.path.dirname(npm_exe)
    if npm_dir:
        env["PATH"] = npm_dir + os.pathsep + env.get("PATH", "")
    env["npm_config_update_notifier"] = "false"
    for args in ([npm_exe, "prefix", "-g"], [npm_exe, "config", "get", "prefix"]):
        try:
            completed = subprocess.run(
                args,
                capture_output=True,
                text=True,
                env=env,
                **subprocess_creationflags_kwargs(),
            )
        except OSError as exc:
            log(f"Unable to query npm prefix: {exc}")
            return None
        if completed.returncode == 0:
            prefix = completed.stdout.strip()
            if prefix and os.path.isdir(prefix):
                return prefix
    return None


def get_cli_bin_dirs(npm_exe: Optional[str], log: Callable[[str], None]) -> list[str]:
    dirs: list[str] = []

    if is_linux():
        node_dir_candidates = ["/usr/local/bin", "/usr/bin"]
    else:
        node_dir_candidates = [
            os.path.join(os.environ.get("ProgramFiles", r"C:\Program Files"), "nodejs"),
            os.path.join(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"), "nodejs"),
        ]
    for d in node_dir_candidates:
        if os.path.isdir(d):
            dirs.append(d)

    appdata = os.environ.get("AppData")
    if appdata:
        npm_global_default = os.path.join(appdata, "npm")
        if os.path.isdir(npm_global_default):
            dirs.append(npm_global_default)

    if npm_exe:
        prefix = get_npm_global_prefix(npm_exe, log)
        if prefix:
            prefix_bin = prefix if is_windows() else os.path.join(prefix, "bin")
            if os.path.isdir(prefix_bin):
                dirs.append(prefix_bin)
            elif os.path.isdir(prefix):
                dirs.append(prefix)

    unique: list[str] = []
    seen: set[str] = set()
    for d in dirs:
        norm = normalize_path_for_compare(d)
        if norm not in seen:
            unique.append(d)
            seen.add(norm)
    return unique


def get_python_cli_bin_dirs(log: Callable[[str], None]) -> list[str]:
    del log  # reserved for future diagnostics to keep call shape consistent with other helpers
    dirs: list[str] = []

    home = os.path.expanduser("~")
    if home:
        dirs.append(os.path.join(home, ".local", "bin"))

    appdata = os.environ.get("AppData")
    if appdata:
        dirs.extend(glob.glob(os.path.join(appdata, "Python", "Python*", "Scripts")))

    local_app = os.environ.get("LocalAppData")
    if local_app:
        dirs.extend(glob.glob(os.path.join(local_app, "Programs", "Python", "Python*", "Scripts")))

    existing_dirs = [d for d in dirs if d and os.path.isdir(d)]
    unique: list[str] = []
    seen: set[str] = set()
    for d in existing_dirs:
        norm = normalize_path_for_compare(d)
        if norm not in seen:
            unique.append(d)
            seen.add(norm)
    return unique


def get_ollama_cli_bin_dirs(log: Callable[[str], None]) -> list[str]:
    del log  # reserved for future diagnostics to keep call shape consistent with other helpers
    dirs: list[str] = []

    if is_linux():
        dirs.extend(["/usr/local/bin", "/usr/bin"])
        existing_dirs = [d for d in dirs if d and os.path.isdir(d)]
        unique: list[str] = []
        seen: set[str] = set()
        for d in existing_dirs:
            norm = normalize_path_for_compare(d)
            if norm not in seen:
                unique.append(d)
                seen.add(norm)
        return unique

    local_app = os.environ.get("LocalAppData")
    program_files = os.environ.get("ProgramFiles", r"C:\Program Files")
    program_files_x86 = os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")
    if local_app:
        dirs.append(os.path.join(local_app, "Programs", "Ollama"))
    dirs.append(os.path.join(program_files, "Ollama"))
    dirs.append(os.path.join(program_files_x86, "Ollama"))

    existing_dirs = [d for d in dirs if d and os.path.isdir(d)]
    unique: list[str] = []
    seen: set[str] = set()
    for d in existing_dirs:
        norm = normalize_path_for_compare(d)
        if norm not in seen:
            unique.append(d)
            seen.add(norm)
    return unique


def filter_system_path_dirs(dirs: list[str]) -> list[str]:
    user_roots: list[str] = []
    home = os.path.expanduser("~")
    if home:
        user_roots.append(home)
    for env_name in ("AppData", "LocalAppData", "UserProfile"):
        value = os.environ.get(env_name)
        if value:
            user_roots.append(value)

    filtered: list[str] = []
    for directory in dirs:
        if any(is_path_within(directory, root) for root in user_roots):
            continue
        filtered.append(directory)
    return filtered


def linux_package_manager_name() -> Optional[str]:
    return detect_linux_distro_family()


def linux_package_manager_install_commands(packages: list[str]) -> list[list[str]]:
    family = linux_package_manager_name()
    if family == "debian":
        return [
            ["apt-get", "update"],
            ["apt-get", "install", "-y", *packages],
        ]
    if family == "fedora":
        return [["dnf", "install", "-y", *packages]]
    if family == "arch":
        return [["pacman", "-Sy", "--noconfirm", *packages]]
    raise RuntimeError(
        "Unsupported Linux distribution. Supported families: Debian/Ubuntu, Fedora, Arch."
    )


def ensure_linux_packages_installed(packages: list[str], log: Callable[[str], None]) -> None:
    if not is_linux():
        return
    if not ensure_linux_root_for_package_installs(log):
        raise RuntimeError("Linux package installation requires root privileges.")
    commands = linux_package_manager_install_commands(packages)
    log("Installing Linux packages: " + ", ".join(packages))
    for args in commands:
        code = run_command(args, log)
        if code != 0:
            raise RuntimeError(
                "Linux package install failed with exit code "
                + format_exit_code(code)
                + f" while running: {' '.join(args)}"
            )


def ensure_node_via_winget(log: Callable[[str], None]) -> None:
    if is_linux():
        node_path = find_node()
        npm_path = find_npm()
        if node_path and npm_path:
            log(f"Node.js is already available: {node_path}")
            log(f"npm is already available: {npm_path}")
            return
        missing = []
        if not node_path:
            missing.append("Node.js")
        if not npm_path:
            missing.append("npm")
        log("Installing Node.js + npm via Linux package manager...")
        log("Missing prerequisites: " + ", ".join(missing))
        ensure_linux_packages_installed(["nodejs", "npm"], log)
        node_path = find_node()
        npm_path = find_npm()
        if not node_path or not npm_path:
            raise RuntimeError(
                "Node.js installation completed, but node and/or npm could not be found. "
                "Try reopening the app or install Node.js manually."
            )
        log(f"Node.js is available: {node_path}")
        log(f"npm is available: {npm_path}")
        return

    winget = find_winget()
    if not winget:
        raise RuntimeError("winget was not found. Install Microsoft App Installer / winget first.")

    node_path = find_node()
    npm_path = find_npm()
    if node_path and npm_path:
        log(f"Node.js is already available: {node_path}")
        log(f"npm is already available: {npm_path}")
        return

    missing = []
    if not node_path:
        missing.append("Node.js")
    if not npm_path:
        missing.append("npm")
    log("Installing Node.js LTS via winget (includes npm)...")
    log("Missing prerequisites: " + ", ".join(missing))
    code = run_command(
        [
            winget,
            "install",
            "--id",
            NODE_WINGET_ID,
            "-e",
            "--accept-package-agreements",
            "--accept-source-agreements",
            "--silent",
            "--disable-interactivity",
        ],
        log,
    )
    if code != 0:
        raise RuntimeError(f"winget Node.js install failed with exit code {code}.")

    node_path = find_node()
    npm_path = find_npm()
    if not node_path or not npm_path:
        raise RuntimeError(
            "Node.js installation completed, but node and/or npm could not be found. "
            "Try reopening the app or install Node.js manually from nodejs.org."
        )
    log(f"Node.js is available: {node_path}")
    log(f"npm is available: {npm_path}")


def ensure_ollama_via_winget(log: Callable[[str], None]) -> tuple[bool, Optional[str]]:
    package_name = OLLAMA_WINGET_ID

    if is_linux():
        existing = find_ollama()
        if existing:
            log(f"Ollama CLI is already available: {existing}")
        try:
            if not command_exists("curl"):
                ensure_linux_packages_installed(["curl"], log)
            if not command_exists("sh"):
                return (False, "sh was not found. Unable to run official Ollama Linux installer.")
        except RuntimeError as exc:
            err = str(exc)
            log(err)
            return (False, err)

        log("Installing official Ollama for Linux via install script (includes ollama CLI)...")
        code = run_command(["sh", "-c", f"curl -fsSL {LINUX_OLLAMA_INSTALL_URL} | sh"], log)
        if code != 0:
            existing = find_ollama()
            if existing:
                log(
                    "Warning: Ollama install/update command failed, but an existing Ollama CLI was found. "
                    "Using existing installation and continuing."
                )
                return (True, package_name)
            err = f"{package_name} failed with exit code {format_exit_code(code)}"
            log(err)
            return (False, err)
        return (True, package_name)

    winget = find_winget()
    if not winget:
        err = "winget was not found. Install Microsoft App Installer / winget first to install Ollama."
        log(err)
        return (False, err)

    existing = find_ollama()
    if existing:
        log(f"Ollama CLI is already available: {existing}")

    install_args = [
        winget,
        "install",
        "--id",
        package_name,
        "-e",
        "--accept-package-agreements",
        "--accept-source-agreements",
        "--silent",
        "--disable-interactivity",
    ]
    upgrade_args = [
        winget,
        "upgrade",
        "--id",
        package_name,
        "-e",
        "--accept-package-agreements",
        "--accept-source-agreements",
        "--silent",
        "--disable-interactivity",
    ]

    log("Installing official Ollama for Windows via winget (includes ollama CLI)...")
    code = run_command(install_args, log)
    if code != 0:
        log(
            "winget install for Ollama failed with exit code "
            + format_exit_code(code)
            + "; trying winget upgrade..."
        )
        code = run_command(upgrade_args, log)
        if code != 0:
            existing = find_ollama()
            if existing:
                log(
                    "Warning: Ollama install/update command failed, but an existing Ollama CLI was found. "
                    "Using existing installation and continuing."
                )
                return (True, package_name)
            err = f"{package_name} failed with exit code {format_exit_code(code)}"
            log(err)
            return (False, err)

    return (True, package_name)


def ensure_python_314_via_winget(log: Callable[[str], None]) -> list[str]:
    python_cmd = find_python_314_command()
    if python_cmd:
        log("Python 3.14 is already available for Mistral Vibe: " + " ".join(python_cmd))
        return python_cmd

    winget = find_winget()
    if not winget:
        raise RuntimeError(
            "Python 3.14 is required for Mistral Vibe CLI, but winget was not found. "
            "Install Microsoft App Installer / winget first or install Python 3.14 manually."
        )

    log("Installing Python 3.14 via winget for Mistral Vibe CLI...")
    code = run_command(
        [
            winget,
            "install",
            "--id",
            PYTHON_314_WINGET_ID,
            "-e",
            "--accept-package-agreements",
            "--accept-source-agreements",
            "--silent",
            "--disable-interactivity",
        ],
        log,
    )
    if code != 0:
        raise RuntimeError(f"winget Python 3.14 install failed with exit code {format_exit_code(code)}.")

    python_cmd = find_python_314_command()
    if not python_cmd:
        raise RuntimeError(
            "Python 3.14 installation completed, but Python 3.14 could not be found. "
            "Try reopening the app or install Python 3.14 manually."
        )
    log("Python 3.14 is available for Mistral Vibe: " + " ".join(python_cmd))
    return python_cmd


def find_linux_python_for_mistral() -> Optional[list[str]]:
    for candidate in (["python3.14"], ["python3"], ["python"]):
        exe = shutil.which(candidate[0])
        if not exe:
            continue
        version = get_python_version([exe])
        if version and version >= (3, 12, 0):
            return [exe]
    return None


def ensure_python_for_mistral_on_linux(log: Callable[[str], None]) -> list[str]:
    python_cmd = find_linux_python_for_mistral()
    if python_cmd:
        version = get_python_version(python_cmd)
        label = ".".join(str(v) for v in version) if version else "unknown"
        log("Python is already available for Mistral Vibe on Linux: " + " ".join(python_cmd) + f" (v{label})")
        return python_cmd

    family = linux_package_manager_name()
    if family == "arch":
        packages = ["python", "python-pip"]
    else:
        packages = ["python3", "python3-pip"]
    log("Installing Python + pip for Mistral Vibe via Linux package manager...")
    ensure_linux_packages_installed(packages, log)

    python_cmd = find_linux_python_for_mistral()
    if not python_cmd:
        raise RuntimeError(
            "Python 3.12+ is required for Mistral Vibe CLI, but no compatible Python was found after install."
        )
    version = get_python_version(python_cmd)
    if not version or version < (3, 12, 0):
        raise RuntimeError(
            "Mistral Vibe CLI requires Python 3.12+, but the installed Linux Python is too old."
        )
    log("Python is available for Mistral Vibe on Linux: " + " ".join(python_cmd))
    return python_cmd


def ensure_pip3_for_python(
    python_cmd: list[str],
    log: Callable[[str], None],
    python_label: str = "Python 3.14",
) -> None:
    pip_check = run_command([*python_cmd, "-m", "pip", "--version"], log)
    if pip_check != 0:
        log(f"pip3 was not found for {python_label}; bootstrapping pip with ensurepip...")
        code = run_command([*python_cmd, "-m", "ensurepip", "--upgrade"], log)
        if code != 0:
            raise RuntimeError(f"{python_label} ensurepip failed with exit code {format_exit_code(code)}.")
        pip_check = run_command([*python_cmd, "-m", "pip", "--version"], log)
        if pip_check != 0:
            raise RuntimeError(f"pip3 is still unavailable after ensurepip for {python_label}.")
    else:
        log(f"pip3 is already available for {python_label}.")

    log(f"Updating pip3 for {python_label}...")
    code = run_command(
        [*python_cmd, "-m", "pip", "install", "--user", "--upgrade", *pip_install_flags_for_platform(), "pip"],
        log,
    )
    if code != 0:
        raise RuntimeError(f"pip3 update failed with exit code {format_exit_code(code)}.")

    pip3_path = find_pip3()
    if pip3_path:
        log(f"pip3 is available: {pip3_path}")


def ensure_uv_for_mistral(python_cmd: list[str], log: Callable[[str], None]) -> Optional[str]:
    existing_uv = find_uv()
    if existing_uv:
        log(f"uv is already available: {existing_uv}")
    else:
        log("uv was not found; installing uv via pip3 for Mistral Vibe CLI...")

    log("Updating uv for Mistral Vibe CLI...")
    code = run_command(
        [*python_cmd, "-m", "pip", "install", "--user", "--upgrade", *pip_install_flags_for_platform(), "uv"],
        log,
    )
    if code != 0:
        log(f"uv install/update failed with exit code {format_exit_code(code)}; pip fallback will be used for Mistral Vibe.")
        return find_uv()

    uv_exe = find_uv()
    if uv_exe:
        log(f"uv is available: {uv_exe}")
        return uv_exe

    log("uv install/update completed, but uv was not found on PATH yet; pip fallback will be used for Mistral Vibe.")
    return None


def ensure_mistral_vibe_dependencies(log: Callable[[str], None]) -> tuple[list[str], Optional[str]]:
    if is_linux():
        python_cmd = ensure_python_for_mistral_on_linux(log)
        ensure_pip3_for_python(python_cmd, log, "Python 3.12+ (Linux)")
    else:
        python_cmd = ensure_python_314_via_winget(log)
        ensure_pip3_for_python(python_cmd, log)
    uv_exe = ensure_uv_for_mistral(python_cmd, log)
    return (python_cmd, uv_exe)


def try_install_mistral_vibe(
    spec: CliSpec,
    log: Callable[[str], None],
) -> tuple[bool, Optional[str]]:
    package_name = spec.package_candidates[0] if spec.package_candidates else "mistral-vibe"

    try:
        python_cmd, uv_exe = ensure_mistral_vibe_dependencies(log)
    except RuntimeError as exc:
        err = str(exc)
        log(err)
        return (False, err)

    if uv_exe:
        log(f"Trying official Mistral Vibe install via uv: {package_name}")
        code = run_command([uv_exe, "tool", "install", "--upgrade", package_name], log)
        if code == 0:
            return (True, package_name)
        log(f"uv tool install failed with exit code {format_exit_code(code)}")
    else:
        log("uv was not found; falling back to pip for Mistral Vibe CLI.")

    log(f"Trying official Mistral Vibe install via pip: {package_name}")
    code = run_command(
        [*python_cmd, "-m", "pip", "install", "--user", "--upgrade", *pip_install_flags_for_platform(), package_name],
        log,
    )
    if code == 0:
        return (True, package_name)

    err = f"Mistral Vibe install failed with exit code {format_exit_code(code)}"
    log(err)
    return (False, err)


def npm_install_global(
    npm_exe: str,
    package_name: str,
    log: Callable[[str], None],
) -> int:
    env = os.environ.copy()
    npm_dir = os.path.dirname(npm_exe)
    if npm_dir:
        env["PATH"] = npm_dir + os.pathsep + env.get("PATH", "")
    env["npm_config_update_notifier"] = "false"
    return run_command([npm_exe, *NPM_QUIET_FLAGS, "install", "-g", package_name], log, env=env)


def is_probably_windows_errno_exit_code(code: int) -> bool:
    # npm on Windows sometimes returns negative errno values reinterpreted as unsigned exit codes.
    return code >= 0xFFFF0000


def format_exit_code(code: int) -> str:
    if not is_probably_windows_errno_exit_code(code):
        return str(code)
    signed = code - (1 << 32)
    return f"{code} (Windows errno {signed})"


def is_probably_windows_file_lock_error(detail: Optional[str]) -> bool:
    if not detail:
        return False
    lowered = detail.lower()
    return (
        "ebusy" in lowered
        or "windows errno -4082" in lowered
        or "4294963214" in lowered
    )


def try_install_package_candidates(
    npm_exe: str,
    spec: CliSpec,
    log: Callable[[str], None],
) -> tuple[bool, Optional[str]]:
    last_error: Optional[str] = None
    for package_name in spec.package_candidates:
        for attempt in range(1, NPM_INSTALL_MAX_ATTEMPTS + 1):
            suffix = "" if attempt == 1 else f" (attempt {attempt}/{NPM_INSTALL_MAX_ATTEMPTS})"
            log(f"Trying npm package for {spec.label}: {package_name}{suffix}")
            code = npm_install_global(npm_exe, package_name, log)
            if code == 0:
                return (True, package_name)

            if attempt < NPM_INSTALL_MAX_ATTEMPTS and is_probably_windows_errno_exit_code(code):
                log(
                    "Transient npm install failure detected (possible Windows file lock). "
                    + f"Retrying in {NPM_INSTALL_RETRY_DELAY_SECONDS:.0f}s..."
                )
                time.sleep(NPM_INSTALL_RETRY_DELAY_SECONDS)
                continue

            last_error = f"{package_name} failed with exit code {format_exit_code(code)}"
            log(last_error)
            break
    return (False, last_error)


def resolve_command_path(
    command_candidates: tuple[str, ...],
    extra_dirs: list[str],
) -> Optional[str]:
    env = os.environ.copy()
    if extra_dirs:
        joined = os.pathsep.join(extra_dirs)
        env["PATH"] = joined + os.pathsep + env.get("PATH", "")

    for cmd in command_candidates:
        found = where_all(cmd, env=env)
        if found:
            priority_order = (".cmd", ".exe", ".bat", ".ps1") if is_windows() else (".sh", ".bin", "")
            for ext in priority_order:
                for candidate in found:
                    if ext:
                        if candidate.lower().endswith(ext):
                            return candidate
                    else:
                        return candidate
            return found[0]

    for d in extra_dirs:
        for cmd in command_candidates:
            ext_candidates = (".cmd", ".exe", ".bat", ".ps1") if is_windows() else (".sh", ".bin")
            for ext in ext_candidates:
                candidate = os.path.join(d, cmd + ext)
                if os.path.isfile(candidate):
                    return candidate
            direct = os.path.join(d, cmd)
            if os.path.isfile(direct):
                return direct
    return None


def create_linux_desktop_shortcut(
    shortcut_path: str,
    command_path: str,
    terminal_title: str,
) -> None:
    os.makedirs(os.path.dirname(shortcut_path), exist_ok=True)
    content = "\n".join(
        [
            "[Desktop Entry]",
            "Type=Application",
            f"Name={terminal_title}",
            f"Exec={command_path}",
            "Terminal=true",
            "Categories=Development;",
        ]
    ) + "\n"
    write_text_file(shortcut_path, content)
    os.chmod(shortcut_path, 0o755)


def create_cli_desktop_shortcut(
    spec: CliSpec,
    command_path: str,
    log: Callable[[str], None],
) -> str:
    desktop = find_desktop_directory()
    if not is_windows():
        shortcut_path = os.path.join(desktop, f"{spec.shortcut_name}.desktop")
        create_linux_desktop_shortcut(shortcut_path, command_path, spec.shortcut_name)
        log(f"Created desktop shortcut: {shortcut_path}")
        return shortcut_path

    shortcut_path = os.path.join(desktop, f"{spec.shortcut_name}.lnk")
    cmd_exe = os.environ.get("ComSpec", r"C:\Windows\System32\cmd.exe")
    arguments = f'/k "{command_path}"'
    working_dir = os.path.expanduser("~")
    icon = f"{cmd_exe},0"
    create_windows_shortcut(
        shortcut_path=shortcut_path,
        target_path=cmd_exe,
        arguments=arguments,
        working_directory=working_dir,
        icon_location=icon,
    )
    log(f"Created desktop shortcut: {shortcut_path}")
    return shortcut_path


class InstallerFrame(wx.Frame):
    def __init__(self) -> None:  # pragma: no cover
        platform_label = "Windows 11" if is_windows() else "Linux"
        super().__init__(None, title=f"AI CLI Installer ({platform_label})", size=(920, 680))
        self.worker_thread: Optional[threading.Thread] = None
        self._persistent_log_path: Optional[str] = None
        self._persistent_log_write_warning_shown = False
        self._reset_persistent_log_for_new_run()
        self._build_ui()
        self.Centre()

    def _build_ui(self) -> None:  # pragma: no cover
        panel = wx.Panel(self)
        root = wx.BoxSizer(wx.VERTICAL)

        title_platform = "Windows 11" if is_windows() else "Linux"
        title = wx.StaticText(panel, label=f"Install AI CLI tools on {title_platform}")
        title_font = title.GetFont()
        title_font.MakeBold()
        title_font.PointSize += 2
        title.SetFont(title_font)
        title.SetName("Installer Title")
        root.Add(title, 0, wx.ALL, 12)

        note_lines = [
            (
                "This installer uses winget for Node.js/Ollama, npm for most CLI tools, and uv/pip for Mistral Vibe."
                if is_windows()
                else "This installer uses your Linux package manager for Node.js/npm, the official Ollama install script, npm for most CLI tools, and uv/pip for Mistral Vibe."
            ),
            "Use Tab and Space to navigate/select checkboxes. Native wxPython controls are used for NVDA/JAWS compatibility.",
            "Run as Administrator/root if you want system-level installs and PATH updates to succeed.",
        ]
        note = wx.StaticText(panel, label="\n".join(note_lines))
        note.Wrap(860)
        note.SetName("Instructions")
        root.Add(note, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 12)

        admin_label_name = "Administrator" if is_windows() else "Root"
        admin_text = f"{admin_label_name}: Yes" if is_admin() else f"{admin_label_name}: No (system PATH may fail)"
        self.admin_label = wx.StaticText(panel, label=admin_text)
        self.admin_label.SetName("Admin Status")
        root.Add(self.admin_label, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 12)

        box = wx.StaticBox(panel, label="Select CLI tools to install")
        box_sizer = wx.StaticBoxSizer(box, wx.VERTICAL)

        self.checkboxes: dict[str, wx.CheckBox] = {}
        for spec in CLI_SPECS:
            cb = wx.CheckBox(box, label=spec.label)
            cb.SetName(spec.label)
            cb.SetValue(False)
            cb.SetToolTip(spec.help_text)
            box_sizer.Add(cb, 0, wx.ALL, 6)
            self.checkboxes[spec.key] = cb

        root.Add(box_sizer, 0, wx.LEFT | wx.RIGHT | wx.EXPAND | wx.BOTTOM, 12)

        self.auto_update_checkbox = wx.CheckBox(
            panel,
            label="Enable hidden auto-update task (startup, logon, daily)",
        )
        self.auto_update_checkbox.SetName("Auto Update Toggle")
        self.auto_update_checkbox.SetValue(True)
        self.auto_update_checkbox.SetToolTip(
            "When enabled, a hidden scheduled task updates installed AI CLIs at startup, logon, and daily."
        )
        root.Add(self.auto_update_checkbox, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 12)

        btn_row = wx.BoxSizer(wx.HORIZONTAL)
        self.select_all_btn = wx.Button(panel, label="Select &All")
        self.select_none_btn = wx.Button(panel, label="Select &None")
        self.install_btn = wx.Button(panel, label="&Install Selected")
        self.close_btn = wx.Button(panel, label="&Close")

        self.select_all_btn.Bind(wx.EVT_BUTTON, self.on_select_all)
        self.select_none_btn.Bind(wx.EVT_BUTTON, self.on_select_none)
        self.install_btn.Bind(wx.EVT_BUTTON, self.on_install)
        self.close_btn.Bind(wx.EVT_BUTTON, self.on_close)

        btn_row.Add(self.select_all_btn, 0, wx.RIGHT, 8)
        btn_row.Add(self.select_none_btn, 0, wx.RIGHT, 8)
        btn_row.Add(self.install_btn, 0, wx.RIGHT, 8)
        btn_row.AddStretchSpacer(1)
        btn_row.Add(self.close_btn, 0)
        root.Add(btn_row, 0, wx.LEFT | wx.RIGHT | wx.EXPAND | wx.BOTTOM, 12)

        self.status_label = wx.StaticText(panel, label="Status: Ready")
        self.status_label.SetName("Current Status")
        root.Add(self.status_label, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 8)

        self.gauge = wx.Gauge(panel, range=100, style=wx.GA_HORIZONTAL)
        self.gauge.SetValue(0)
        root.Add(self.gauge, 0, wx.LEFT | wx.RIGHT | wx.EXPAND | wx.BOTTOM, 12)

        log_label = wx.StaticText(panel, label="Installation Log")
        log_label.SetName("Log Label")
        root.Add(log_label, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 4)

        self.log_ctrl = wx.TextCtrl(
            panel,
            style=wx.TE_MULTILINE | wx.TE_READONLY | wx.TE_DONTWRAP | wx.HSCROLL | wx.TE_RICH2,
        )
        self.log_ctrl.SetName("Installation Log")
        self.log_ctrl.SetMinSize((-1, 260))
        root.Add(self.log_ctrl, 1, wx.LEFT | wx.RIGHT | wx.BOTTOM | wx.EXPAND, 12)

        panel.SetSizer(root)
        self.install_btn.SetDefault()

    def log(self, message: str) -> None:
        wx.CallAfter(self._append_log, message)

    def _reset_persistent_log_for_new_run(self) -> None:
        self._persistent_log_path = reset_gui_last_run_log()
        self._persistent_log_write_warning_shown = False

    def _append_log(self, message: str) -> None:
        self.log_ctrl.AppendText(message + "\n")
        self.log_ctrl.ShowPosition(self.log_ctrl.GetLastPosition())
        err = append_persistent_log_line(getattr(self, "_persistent_log_path", None), message)
        if err and not getattr(self, "_persistent_log_write_warning_shown", False):
            self._persistent_log_write_warning_shown = True
            warning = f"Persistent log write warning: {err}"
            self.log_ctrl.AppendText(warning + "\n")
            self.log_ctrl.ShowPosition(self.log_ctrl.GetLastPosition())

    def set_status(self, text: str) -> None:
        wx.CallAfter(self.status_label.SetLabel, f"Status: {text}")

    def set_gauge(self, value: int) -> None:
        value = max(0, min(100, value))
        wx.CallAfter(self.gauge.SetValue, value)

    def set_busy(self, busy: bool) -> None:
        def _apply() -> None:
            self.install_btn.Enable(not busy)
            self.select_all_btn.Enable(not busy)
            self.select_none_btn.Enable(not busy)
            if busy:
                self.gauge.Pulse()
        wx.CallAfter(_apply)

    def on_select_all(self, _event: wx.CommandEvent) -> None:
        for cb in self.checkboxes.values():
            cb.SetValue(True)

    def on_select_none(self, _event: wx.CommandEvent) -> None:
        for cb in self.checkboxes.values():
            cb.SetValue(False)

    def on_close(self, _event: wx.CommandEvent) -> None:
        if self.worker_thread and self.worker_thread.is_alive():
            wx.MessageBox(
                "Installation is still running. Wait for it to finish before closing.",
                "Install In Progress",
                wx.OK | wx.ICON_INFORMATION,
                self,
            )
            return
        self.Close()

    def on_install(self, _event: wx.CommandEvent) -> None:
        if self.worker_thread and self.worker_thread.is_alive():
            return

        selected = [spec for spec in CLI_SPECS if self.checkboxes[spec.key].GetValue()]
        if not selected:
            wx.MessageBox(
                "Select at least one CLI to install.",
                "Nothing Selected",
                wx.OK | wx.ICON_WARNING,
                self,
            )
            return

        self.log_ctrl.Clear()
        reset_log = getattr(self, "_reset_persistent_log_for_new_run", None)
        if callable(reset_log):
            reset_log()
        self.set_status("Starting...")
        self.set_gauge(0)
        self.set_busy(True)
        auto_update_enabled = True
        auto_update_cb = getattr(self, "auto_update_checkbox", None)
        if auto_update_cb is not None and hasattr(auto_update_cb, "GetValue"):
            auto_update_enabled = bool(auto_update_cb.GetValue())

        self.worker_thread = threading.Thread(
            target=self._install_worker,
            args=(selected, auto_update_enabled),
            daemon=True,
        )
        self.worker_thread.start()

    def _install_worker(self, selected: list[CliSpec], enable_auto_update: bool = True) -> None:
        try:
            self._run_install(selected, enable_auto_update)
            self.log("Installation workflow complete.")
            self.set_status("Complete")
            self.set_gauge(100)
        except Exception as exc:
            self.log(f"ERROR: {exc}")
            self.log(traceback.format_exc().rstrip())
            self.set_status("Failed")
        finally:
            self.set_busy(False)

    def _run_install(self, selected: list[CliSpec], enable_auto_update: bool = True) -> None:
        self.log(("Windows 11 AI CLI Installer started." if is_windows() else "Linux AI CLI Installer started."))
        persistent_log_path = getattr(self, "_persistent_log_path", None)
        if persistent_log_path:
            self.log(f"Persistent log file: {persistent_log_path}")
        self.log(f"Administrator mode: {'Yes' if is_admin() else 'No'}")
        if not is_admin():
            self.log(
                "System PATH update may fail without Administrator/root privileges."
            )
        needs_python_cli_dirs = any(spec.key == "mistral" for spec in selected)
        needs_ollama_cli_dirs = any(spec.key == "ollama" for spec in selected)

        self.set_status("Checking/installing Node.js + npm")
        self.set_gauge(5)
        ensure_node_via_winget(self.log)

        self.set_status("Locating npm")
        self.set_gauge(15)
        npm_exe = find_npm()
        if not npm_exe:
            raise RuntimeError(
                "npm was not found after Node.js setup. Try closing and reopening the app, or install Node.js manually."
            )
        self.log(f"Using npm executable: {npm_exe}")

        cli_bin_dirs = get_cli_bin_dirs(npm_exe, self.log)
        if needs_python_cli_dirs:
            cli_bin_dirs = dedupe_preserve_order(cli_bin_dirs + get_python_cli_bin_dirs(self.log))
        if needs_ollama_cli_dirs:
            cli_bin_dirs = dedupe_preserve_order(cli_bin_dirs + get_ollama_cli_bin_dirs(self.log))
        self.log("PATH directories to ensure: " + (", ".join(cli_bin_dirs) if cli_bin_dirs else "(none found yet)"))

        self.set_status("Updating user/system PATH")
        self.set_gauge(20)
        added_user, user_err = add_dirs_to_path("user", cli_bin_dirs)
        if user_err:
            self.log(f"User PATH update warning: {user_err}")
        elif added_user:
            self.log("Added to user PATH: " + ", ".join(added_user))
        else:
            self.log("User PATH already contains required directories.")

        system_path_dirs = filter_system_path_dirs(cli_bin_dirs)
        added_system, system_err = add_dirs_to_path("system", system_path_dirs)
        if system_err:
            self.log(f"System PATH update warning: {system_err}")
        elif added_system:
            self.log("Added to system PATH: " + ", ".join(added_system))
        else:
            self.log("System PATH already contains required directories.")

        total = len(selected)
        installed_commands: list[tuple[CliSpec, str]] = []
        installed_packages: list[str] = []

        for index, spec in enumerate(selected, start=1):
            pct = 20 + int((index - 1) / max(total, 1) * 60)
            self.set_gauge(pct)
            self.set_status(f"Installing {spec.label} ({index}/{total})")

            if spec.key == "mistral":
                success, pkg = try_install_mistral_vibe(spec, self.log)
            elif spec.key == "ollama":
                success, pkg = ensure_ollama_via_winget(self.log)
            else:
                success, pkg = try_install_package_candidates(npm_exe, spec, self.log)
            if not success:
                if spec.optional:
                    self.log(f"Skipping optional {spec.label}: no working install candidate.")
                    continue
                if is_probably_windows_file_lock_error(pkg):
                    cli_bin_dirs = get_cli_bin_dirs(npm_exe, self.log)
                    command_path = resolve_command_path(spec.command_candidates, cli_bin_dirs)
                    if command_path:
                        self.log(
                            f"Warning: {spec.label} install/update is blocked by a locked file "
                            "(likely a running CLI process). Using existing installation and continuing."
                        )
                        self.log(f"Resolved existing command path for {spec.label}: {command_path}")
                        installed_commands.append((spec, command_path))
                        if spec.package_candidates:
                            installed_packages.append(spec.package_candidates[0])
                        continue
                raise RuntimeError(f"Failed to install {spec.label}.")

            assert pkg is not None
            self.log(f"Installed {spec.label} using package {pkg}")
            if spec.key not in ("mistral", "ollama"):
                installed_packages.append(pkg)

            cli_bin_dirs = get_cli_bin_dirs(npm_exe, self.log)
            if spec.key == "mistral":
                cli_bin_dirs = dedupe_preserve_order(cli_bin_dirs + get_python_cli_bin_dirs(self.log))
            if spec.key == "ollama":
                cli_bin_dirs = dedupe_preserve_order(cli_bin_dirs + get_ollama_cli_bin_dirs(self.log))
            command_path = resolve_command_path(spec.command_candidates, cli_bin_dirs)
            if command_path:
                self.log(f"Resolved command path for {spec.label}: {command_path}")
                installed_commands.append((spec, command_path))
            else:
                self.log(f"Warning: Could not resolve executable path for {spec.label}. Shortcut will be skipped.")

        self.set_status("Refreshing PATH entries")
        self.set_gauge(85)
        cli_bin_dirs = get_cli_bin_dirs(npm_exe, self.log)
        if needs_python_cli_dirs:
            cli_bin_dirs = dedupe_preserve_order(cli_bin_dirs + get_python_cli_bin_dirs(self.log))
        if needs_ollama_cli_dirs:
            cli_bin_dirs = dedupe_preserve_order(cli_bin_dirs + get_ollama_cli_bin_dirs(self.log))
        added_user, user_err = add_dirs_to_path("user", cli_bin_dirs)
        if user_err:
            self.log(f"User PATH refresh warning: {user_err}")
        elif added_user:
            self.log("Added to user PATH (post-install): " + ", ".join(added_user))

        system_path_dirs = filter_system_path_dirs(cli_bin_dirs)
        added_system, system_err = add_dirs_to_path("system", system_path_dirs)
        if system_err:
            self.log(f"System PATH refresh warning: {system_err}")
        elif added_system:
            self.log("Added to system PATH (post-install): " + ", ".join(added_system))

        self.set_status("Configuring auto-updates")
        self.set_gauge(90)
        if enable_auto_update:
            try:
                ensure_cli_auto_update_task(npm_exe, installed_packages, self.log)
            except Exception as exc:
                self.log(f"Auto-update task warning: {exc}")
        else:
            self.log("Hidden auto-update task disabled for this run.")

        self.set_status("Creating desktop shortcuts")
        self.set_gauge(92)
        for spec, cmd_path in installed_commands:
            try:
                create_cli_desktop_shortcut(spec, cmd_path, self.log)
            except Exception as exc:
                self.log(f"Shortcut creation failed for {spec.label}: {exc}")

        self.set_status("Finalizing")
        self.set_gauge(98)
        self.log("")
        self.log("Next step: launch a shortcut on the Desktop, or open a new terminal and run the installed CLI command.")


class InstallerApp(wx.App):
    def OnInit(self) -> bool:
        if not (is_windows() or is_linux()):
            wx.MessageBox(
                "This installer currently supports Windows and Linux (Debian/Ubuntu, Fedora, Arch).",
                "Unsupported OS",
                wx.OK | wx.ICON_ERROR,
            )
            return False
        frame = InstallerFrame()
        frame.Show()
        return True


def main() -> int:
    app = InstallerApp(False)
    app.MainLoop()
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
