#!/usr/bin/env bash
set -Eeuo pipefail
IFS=$'\n\t'

SCRIPT_NAME="$(basename "$0")"
DRY_RUN=0
NO_CRON=0
CRON_TIME="0 3 * * *"
SUBCOMMAND="install-all"
TARGET="all"

OLLAMA_INSTALL_URL="https://ollama.com/install.sh"
UPDATE_SCRIPT_PATH="/usr/local/bin/installthecli-linux-update.sh"
CRON_FILE_PATH="/etc/cron.d/installthecli-ai-cli-updates"
UPDATE_LOG_PATH="/var/log/installthecli-linux-update.log"

NPM_FLAGS=(--no-fund --no-audit --no-update-notifier --loglevel error)
PIP_FLAGS=(--disable-pip-version-check --no-input --quiet --break-system-packages)

DISTRO_FAMILY=""
CRON_SERVICE_NAME=""
PYTHON_BIN=""

log() {
  printf '[install] %s\n' "$*"
}

warn() {
  printf '[warn] %s\n' "$*" >&2
}

die() {
  printf '[error] %s\n' "$*" >&2
  exit 1
}

print_usage() {
  cat <<'EOF'
One-click Linux installer for InstallTheCli (Debian/Ubuntu, Fedora, Arch).

Usage:
  ./install_all_linux.sh [command] [target] [options]

Commands:
  install-all              Install all supported CLIs (default).
  install <target>         Install a single target CLI (plus prerequisites).
  setup-cron               Install/update only the cron updater.
  list                     List supported install targets.
  help                     Show help.

Options:
  --dry-run     Print commands but do not modify the system.
  --no-cron     Skip installing the cron-based auto-update job.
  --cron-time   Daily cron schedule (default: 0 3 * * *).
  -h, --help    Show this help.

This script installs:
  - Node.js + npm (distro package manager)
  - Claude CLI, Codex CLI, Gemini CLI, Grok CLI, Qwen CLI, GitHub Copilot CLI (npm)
  - Mistral Vibe CLI (Python 3.12+ + pip/uv)
  - Ollama (official install script)
  - Cron updater (@reboot and daily) unless --no-cron is used
EOF
}

print_targets() {
  cat <<'EOF'
Supported targets:
  claude
  codex
  gemini
  grok
  qwen
  copilot
  mistral
  ollama
  all

Examples:
  ./install_all_linux.sh install codex
  ./install_all_linux.sh install mistral --no-cron
  ./install_all_linux.sh install-all --cron-time "15 2 * * *"
  ./install_all_linux.sh setup-cron
EOF
}

print_cmd() {
  printf '[run]'
  printf ' %q' "$@"
  printf '\n'
}

run_cmd() {
  print_cmd "$@"
  if (( DRY_RUN )); then
    return 0
  fi
  "$@"
}

run_shell() {
  local cmd="$1"
  printf '[run] %s\n' "$cmd"
  if (( DRY_RUN )); then
    return 0
  fi
  /bin/sh -lc "$cmd"
}

require_root() {
  if [[ "${EUID:-$(id -u)}" -ne 0 ]]; then
    die "Run this script as root (or with sudo)."
  fi
}

command_exists() {
  command -v "$1" >/dev/null 2>&1
}

npm_package_installed() {
  npm ls -g --depth=0 "$1" >/dev/null 2>&1
}

detect_distro_family() {
  if [[ ! -r /etc/os-release ]]; then
    die "/etc/os-release not found. Unsupported Linux distribution."
  fi
  # shellcheck disable=SC1091
  source /etc/os-release
  local haystack="${ID:-} ${ID_LIKE:-}"
  case " ${haystack,,} " in
    *" debian "*|*" ubuntu "*) DISTRO_FAMILY="debian" ;;
    *" fedora "*|*" rhel "*|*" centos "*) DISTRO_FAMILY="fedora" ;;
    *" arch "*) DISTRO_FAMILY="arch" ;;
    *)
      die "Unsupported Linux distribution. Supported: Debian/Ubuntu, Fedora, Arch."
      ;;
  esac
  log "Detected Linux distro family: ${DISTRO_FAMILY}"
}

configure_distro_package_metadata() {
  case "$DISTRO_FAMILY" in
    debian) CRON_SERVICE_NAME="cron" ;;
    fedora) CRON_SERVICE_NAME="crond" ;;
    arch) CRON_SERVICE_NAME="crond" ;;
    *) die "Unsupported distro family: ${DISTRO_FAMILY}" ;;
  esac
}

install_linux_packages() {
  local pkgs=("$@")
  case "$DISTRO_FAMILY" in
    debian)
      run_cmd apt-get update
      run_cmd apt-get install -y "${pkgs[@]}"
      ;;
    fedora)
      run_cmd dnf install -y "${pkgs[@]}"
      ;;
    arch)
      run_cmd pacman -Sy --noconfirm "${pkgs[@]}"
      ;;
    *)
      die "Unsupported distro family: ${DISTRO_FAMILY}"
      ;;
  esac
}

install_base_dependencies() {
  local pkgs=()
  local cron_bin="cron"
  case "$DISTRO_FAMILY" in
    debian)
      cron_bin="cron"
      ;;
    fedora)
      cron_bin="crond"
      ;;
    arch)
      cron_bin="crond"
      ;;
  esac
  pkgs+=(ca-certificates)
  if ! command_exists curl; then
    pkgs+=(curl)
  fi
  if ! command_exists node; then
    pkgs+=(nodejs)
  fi
  if ! command_exists npm; then
    pkgs+=(npm)
  fi

  if ! command_exists python3 && ! command_exists python; then
    if [[ "$DISTRO_FAMILY" == "arch" ]]; then
      pkgs+=(python)
    else
      pkgs+=(python3)
    fi
  fi

  if ! select_python_for_mistral >/dev/null 2>&1; then
    # Ensure pip package is present even if Python exists but is missing pip.
    if [[ "$DISTRO_FAMILY" == "arch" ]]; then
      pkgs+=(python-pip)
    else
      pkgs+=(python3-pip)
    fi
  elif ! "$PYTHON_BIN" -m pip --version >/dev/null 2>&1; then
    if [[ "$DISTRO_FAMILY" == "arch" ]]; then
      pkgs+=(python-pip)
    else
      pkgs+=(python3-pip)
    fi
  fi

  if ! command_exists "$cron_bin"; then
    if [[ "$DISTRO_FAMILY" == "debian" ]]; then
      pkgs+=(cron)
    else
      pkgs+=(cronie)
    fi
  fi

  if [[ ${#pkgs[@]} -eq 0 ]]; then
    log "Base dependencies already available."
    return 0
  fi
  log "Installing base dependencies: ${pkgs[*]}"
  install_linux_packages "${pkgs[@]}"
}

verify_core_binaries() {
  if (( DRY_RUN )); then
    log "Dry-run: skipping post-install binary verification."
    return 0
  fi
  command_exists node || die "node not found after install."
  command_exists npm || die "npm not found after install."
  command_exists curl || die "curl not found after install."
  log "Core tools available: node=$(command -v node), npm=$(command -v npm), curl=$(command -v curl)"
}

python_meets_mistral_requirement() {
  local python_bin="$1"
  "$python_bin" - <<'PY' >/dev/null 2>&1
import sys
raise SystemExit(0 if sys.version_info >= (3, 12) else 1)
PY
}

select_python_for_mistral() {
  local candidate
  for candidate in python3.14 python3 python; do
    if ! command_exists "$candidate"; then
      continue
    fi
    if python_meets_mistral_requirement "$candidate"; then
      PYTHON_BIN="$candidate"
      log "Using Python for Mistral Vibe: $("$candidate" -V 2>&1) (${candidate})"
      return 0
    fi
  done
  return 1
}

ensure_root_local_symlink() {
  local source_path="$1"
  local link_path="$2"
  if [[ ! -x "$source_path" ]]; then
    return 0
  fi
  if [[ -L "$link_path" ]] || [[ -e "$link_path" ]]; then
    return 0
  fi
  run_cmd ln -s "$source_path" "$link_path"
}

install_npm_cli() {
  local label="$1"
  shift
  local candidate
  for candidate in "$@"; do
    log "Trying npm package for ${label}: ${candidate}"
    if run_cmd npm "${NPM_FLAGS[@]}" install -g "$candidate"; then
      log "Installed ${label} using package ${candidate}"
      return 0
    fi
    warn "npm install failed for ${candidate}; trying next candidate (if any)."
  done
  die "Failed to install ${label} via npm."
}

install_all_npm_clis() {
  install_npm_cli "Claude CLI" "@anthropic-ai/claude-code"
  install_npm_cli "Codex CLI" "@openai/codex"
  install_npm_cli "Gemini CLI" "@google/gemini-cli"
  install_npm_cli "Grok CLI (Vibe Kit)" "@vibe-kit/grok-cli"
  install_npm_cli "Qwen CLI" "@qwen-code/qwen-code" "qwen-code"
  install_npm_cli "GitHub Copilot CLI" "@github/copilot" "@githubnext/github-copilot-cli"
}

install_npm_target() {
  local target="$1"
  case "$target" in
    claude) install_npm_cli "Claude CLI" "@anthropic-ai/claude-code" ;;
    codex) install_npm_cli "Codex CLI" "@openai/codex" ;;
    gemini) install_npm_cli "Gemini CLI" "@google/gemini-cli" ;;
    grok) install_npm_cli "Grok CLI (Vibe Kit)" "@vibe-kit/grok-cli" ;;
    qwen) install_npm_cli "Qwen CLI" "@qwen-code/qwen-code" "qwen-code" ;;
    copilot) install_npm_cli "GitHub Copilot CLI" "@github/copilot" "@githubnext/github-copilot-cli" ;;
    *)
      return 1
      ;;
  esac
  return 0
}

install_mistral_vibe() {
  select_python_for_mistral || die "Python 3.12+ is required for Mistral Vibe CLI."

  run_cmd "$PYTHON_BIN" -m pip install --user --upgrade "${PIP_FLAGS[@]}" pip
  run_cmd "$PYTHON_BIN" -m pip install --user --upgrade "${PIP_FLAGS[@]}" uv

  export PATH="/root/.local/bin:${PATH}"
  if command_exists uv; then
    if run_cmd uv tool install --upgrade mistral-vibe; then
      log "Installed Mistral Vibe CLI using uv."
    else
      warn "uv tool install failed; falling back to pip."
      run_cmd "$PYTHON_BIN" -m pip install --user --upgrade "${PIP_FLAGS[@]}" mistral-vibe
      log "Installed Mistral Vibe CLI using pip."
    fi
  else
    warn "uv not found after install; falling back to pip."
    run_cmd "$PYTHON_BIN" -m pip install --user --upgrade "${PIP_FLAGS[@]}" mistral-vibe
    log "Installed Mistral Vibe CLI using pip."
  fi

  ensure_root_local_symlink "/root/.local/bin/uv" "/usr/local/bin/uv"
  ensure_root_local_symlink "/root/.local/bin/vibe" "/usr/local/bin/vibe"
  ensure_root_local_symlink "/root/.local/bin/mistral-vibe" "/usr/local/bin/mistral-vibe"
}

install_ollama_official() {
  if command_exists ollama; then
    log "Ollama already available: $(command -v ollama)"
  fi
  run_shell "curl -fsSL ${OLLAMA_INSTALL_URL} | sh"
  if command_exists ollama; then
    log "Ollama CLI available: $(command -v ollama)"
  fi
}

install_all_targets() {
  install_all_npm_clis
  install_mistral_vibe
  install_ollama_official
}

install_single_target() {
  local target_key="${1,,}"
  case "$target_key" in
    all)
      install_all_targets
      ;;
    mistral|mistral-vibe|vibe)
      install_mistral_vibe
      ;;
    ollama)
      install_ollama_official
      ;;
    claude|codex|gemini|grok|qwen|copilot)
      install_npm_target "$target_key"
      ;;
    *)
      die "Unknown target: ${target_key}. Use '$SCRIPT_NAME list' to see supported targets."
      ;;
  esac
}

update_script_content() {
  cat <<'EOF'
#!/usr/bin/env bash
set -Eeuo pipefail
IFS=$'\n\t'
PATH="/root/.local/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
LOG_PREFIX="[installthecli-update]"
OLLAMA_INSTALL_URL="https://ollama.com/install.sh"
NPM_FLAGS=(--no-fund --no-audit --no-update-notifier --loglevel error)
PIP_FLAGS=(--disable-pip-version-check --no-input --quiet --break-system-packages)

log() {
  printf '%s %s\n' "${LOG_PREFIX}" "$*"
}

command_exists() {
  command -v "$1" >/dev/null 2>&1
}

run_cmd() {
  log "+ $*"
  "$@"
}

run_shell() {
  log "+ $1"
  /bin/sh -lc "$1"
}

update_npm_cli() {
  local label="$1"
  shift
  local candidate
  for candidate in "$@"; do
    if ! npm_package_installed "$candidate"; then
      continue
    fi
    if run_cmd npm "${NPM_FLAGS[@]}" update -g "$candidate"; then
      log "Updated ${label} via ${candidate}"
      return 0
    fi
  done
  log "Skipped or failed to update ${label}"
  return 0
}

update_npm_all() {
  if ! command_exists npm; then
    log "npm not found; skipping npm CLI updates"
    return 0
  fi
  update_npm_cli "Claude CLI" "@anthropic-ai/claude-code"
  update_npm_cli "Codex CLI" "@openai/codex"
  update_npm_cli "Gemini CLI" "@google/gemini-cli"
  update_npm_cli "Grok CLI (Vibe Kit)" "@vibe-kit/grok-cli"
  update_npm_cli "Qwen CLI" "@qwen-code/qwen-code" "qwen-code"
  update_npm_cli "GitHub Copilot CLI" "@github/copilot" "@githubnext/github-copilot-cli"
}

select_python() {
  local candidate
  for candidate in python3.14 python3 python; do
    if ! command_exists "$candidate"; then
      continue
    fi
    if "$candidate" - <<'PY' >/dev/null 2>&1
import sys
raise SystemExit(0 if sys.version_info >= (3, 12) else 1)
PY
    then
      printf '%s\n' "$candidate"
      return 0
    fi
  done
  return 1
}

ensure_root_symlink_if_missing() {
  local source_path="$1"
  local link_path="$2"
  if [[ -x "$source_path" && ! -e "$link_path" ]]; then
    ln -s "$source_path" "$link_path" || true
  fi
}

update_mistral_vibe() {
  local py
  py="$(select_python || true)"
  if [[ -z "${py}" ]]; then
    log "Python 3.12+ not found; skipping Mistral Vibe update"
    return 0
  fi
  run_cmd "$py" -m pip install --user --upgrade "${PIP_FLAGS[@]}" pip || true
  run_cmd "$py" -m pip install --user --upgrade "${PIP_FLAGS[@]}" uv || true
  if command_exists uv; then
    run_cmd uv tool install --upgrade mistral-vibe || true
  else
    run_cmd "$py" -m pip install --user --upgrade "${PIP_FLAGS[@]}" mistral-vibe || true
  fi
  ensure_root_symlink_if_missing "/root/.local/bin/uv" "/usr/local/bin/uv"
  ensure_root_symlink_if_missing "/root/.local/bin/vibe" "/usr/local/bin/vibe"
  ensure_root_symlink_if_missing "/root/.local/bin/mistral-vibe" "/usr/local/bin/mistral-vibe"
}

update_ollama() {
  if ! command_exists curl; then
    log "curl not found; skipping Ollama update"
    return 0
  fi
  run_shell "curl -fsSL ${OLLAMA_INSTALL_URL} | sh" || true
}

main() {
  update_npm_all
  update_mistral_vibe
  update_ollama
}

main "$@"
EOF
}

cron_file_content() {
  cat <<EOF
SHELL=/bin/bash
PATH=/root/.local/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
@reboot root ${UPDATE_SCRIPT_PATH} >> ${UPDATE_LOG_PATH} 2>&1
${CRON_TIME} root ${UPDATE_SCRIPT_PATH} >> ${UPDATE_LOG_PATH} 2>&1
EOF
}

write_root_file() {
  local path="$1"
  local mode="$2"
  local content="$3"
  if (( DRY_RUN )); then
    log "Would write ${path} (mode ${mode})"
    return 0
  fi
  mkdir -p "$(dirname "$path")"
  printf '%s' "$content" > "$path"
  chmod "$mode" "$path"
}

enable_cron_service() {
  if command_exists systemctl; then
    if run_cmd systemctl enable --now "$CRON_SERVICE_NAME"; then
      return 0
    fi
    warn "systemctl could not enable/start ${CRON_SERVICE_NAME}; trying service command."
  fi
  if command_exists service; then
    run_cmd service "$CRON_SERVICE_NAME" start || warn "Failed to start ${CRON_SERVICE_NAME} via service."
    return 0
  fi
  warn "No supported service manager found. Cron may already be running."
}

setup_cron_updater() {
  local updater cron_file
  updater="$(update_script_content)"
  cron_file="$(cron_file_content)"

  write_root_file "$UPDATE_SCRIPT_PATH" "0755" "$updater"
  write_root_file "$CRON_FILE_PATH" "0644" "$cron_file"
  if (( DRY_RUN )); then
    log "Would ensure update log file exists: ${UPDATE_LOG_PATH}"
  else
    touch "$UPDATE_LOG_PATH"
    chmod 0644 "$UPDATE_LOG_PATH" || true
  fi
  enable_cron_service
  log "Configured cron updater: @reboot and daily (${CRON_TIME})"
}

parse_args() {
  local positional=()
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --dry-run)
        DRY_RUN=1
        ;;
      --no-cron)
        NO_CRON=1
        ;;
      --cron-time)
        shift
        [[ $# -gt 0 ]] || die "--cron-time requires a value"
        CRON_TIME="$1"
        ;;
      -h|--help)
        SUBCOMMAND="help"
        ;;
      install-all|install|setup-cron|cron|list|help)
        positional+=("$1")
        ;;
      *)
        if [[ "$1" == -* ]]; then
          die "Unknown argument: $1 (use --help)"
        fi
        positional+=("$1")
        ;;
    esac
    shift
  done

  if [[ "${SUBCOMMAND}" == "help" ]]; then
    return 0
  fi

  if [[ ${#positional[@]} -eq 0 ]]; then
    return 0
  fi

  case "${positional[0],,}" in
    install-all)
      SUBCOMMAND="install-all"
      ;;
    install)
      SUBCOMMAND="install"
      [[ ${#positional[@]} -ge 2 ]] || die "install requires a target. Use '$SCRIPT_NAME list'."
      TARGET="${positional[1],,}"
      ;;
    setup-cron|cron)
      SUBCOMMAND="setup-cron"
      ;;
    list)
      SUBCOMMAND="list"
      ;;
    help)
      SUBCOMMAND="help"
      ;;
    claude|codex|gemini|grok|qwen|copilot|mistral|mistral-vibe|vibe|ollama|all)
      # Convenience alias: treat first positional target as "install <target>"
      SUBCOMMAND="install"
      TARGET="${positional[0],,}"
      ;;
    *)
      die "Unknown command: ${positional[0]} (use --help)"
      ;;
  esac
}

main() {
  parse_args "$@"

  case "$SUBCOMMAND" in
    help)
      print_usage
      print_targets
      exit 0
      ;;
    list)
      print_targets
      exit 0
      ;;
  esac

  require_root
  detect_distro_family
  configure_distro_package_metadata

  case "$SUBCOMMAND" in
    install-all)
      log "Installing all supported AI CLIs for Linux..."
      install_base_dependencies
      verify_core_binaries
      install_all_targets
      if (( NO_CRON )); then
        log "Skipping cron updater (--no-cron)"
      else
        setup_cron_updater
      fi
      ;;
    install)
      log "Installing Linux CLI target: ${TARGET}"
      install_base_dependencies
      verify_core_binaries
      install_single_target "$TARGET"
      if (( NO_CRON )); then
        log "Skipping cron updater (--no-cron)"
      else
        setup_cron_updater
      fi
      ;;
    setup-cron)
      if (( NO_CRON )); then
        log "Skipping cron updater (--no-cron)"
      else
        setup_cron_updater
      fi
      ;;
    *)
      die "Unsupported command state: ${SUBCOMMAND}"
      ;;
  esac

  log "Done."
  if [[ "$SUBCOMMAND" == "setup-cron" ]]; then
    log "Cron updater is configured."
  else
    log "Open a new shell and run: claude, codex, gemini, grok, qwen, copilot, vibe, ollama"
  fi
}

main "$@"
