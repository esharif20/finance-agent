#!/bin/sh
# finance-agent installer:
#   curl -fsSL https://raw.githubusercontent.com/esharif20/finance-agent/main/install.sh | sh
#
# What it does, in order (no sudo, safe to run again, never touches your data):
#   1. checks for macOS on Apple Silicon;
#   2. installs uv if missing (Homebrew if you have it, else uv's own installer,
#      pinned to one version and checked against its SHA-256);
#   3. clones or updates the app in ~/.local/share/finance-agent (git checks
#      every object by hash) and installs the `fa` command with `uv tool`;
#   4. offers `brew install ollama` for the local AI.
# Then run `fa setup` (or `fa demo` to try it on synthetic data first).
set -eu

REPO="${FA_REPO:-https://github.com/esharif20/finance-agent.git}"
REF="${FA_REF:-main}"
APP="${FA_APP_DIR:-$HOME/.local/share/finance-agent/app}"
UV_VERSION="0.13.6"
UV_INSTALLER_SHA256="f7a56e7afaec49a3bcbc3ac3cdbe917e7c6ce30e0d815308a12e531aa5ef23b7"

say() { printf '\033[1;33m==>\033[0m %s\n' "$1"; }
die() { printf 'fa install: %s\n' "$1" >&2; exit 1; }

[ "$(uname -s)" = "Darwin" ] || die "finance-agent runs on macOS only."
[ "$(uname -m)" = "arm64" ] || die "finance-agent needs an Apple Silicon Mac."
command -v git >/dev/null 2>&1 || die "git is missing: run 'xcode-select --install' first."

if ! command -v uv >/dev/null 2>&1; then
  if command -v brew >/dev/null 2>&1; then
    say "Installing uv with Homebrew"
    brew install uv
  else
    say "Installing uv $UV_VERSION"
    tmp="$(mktemp -d)"
    trap 'rm -rf "$tmp"' EXIT
    curl -fsSL "https://astral.sh/uv/$UV_VERSION/install.sh" -o "$tmp/uv-install.sh"
    echo "$UV_INSTALLER_SHA256  $tmp/uv-install.sh" | shasum -a 256 -c - >/dev/null \
      || die "the uv installer did not match its pinned checksum; nothing was run."
    sh "$tmp/uv-install.sh" --no-modify-path
    PATH="$HOME/.local/bin:$PATH"
  fi
fi

if [ -d "$APP/.git" ]; then
  say "Updating finance-agent"
  git -C "$APP" fetch --quiet --depth 1 origin "$REF"
  git -C "$APP" checkout --quiet --force FETCH_HEAD
else
  say "Downloading finance-agent"
  mkdir -p "$(dirname "$APP")"
  git clone --quiet --depth 1 --branch "$REF" "$REPO" "$APP"
fi

say "Installing the fa command"
uv tool install --quiet --force --editable "$APP"
uv tool update-shell >/dev/null 2>&1 || true

if [ ! -d "$APP/web/dist" ]; then
  if command -v pnpm >/dev/null 2>&1; then
    say "Building the web UI"
    pnpm --dir "$APP/web" install --frozen-lockfile --silent
    pnpm --dir "$APP/web" run build >/dev/null
  else
    say "No pnpm: the API runs, but build the UI later with 'pnpm --dir $APP/web run build'"
  fi
fi

if ! command -v ollama >/dev/null 2>&1 && command -v brew >/dev/null 2>&1; then
  printf 'Install Ollama for the local AI now (brew install ollama)? [y/N] '
  answer=""
  read -r answer </dev/tty || answer=""
  case "$answer" in
    y|Y|yes) brew install ollama && brew services start ollama ;;
    *) say "Skipped; 'fa models install' offers it again later." ;;
  esac
fi

say "Done. Next: 'fa setup' (or 'fa demo' to look around on synthetic data)."
say "Open a new terminal if 'fa' is not found yet."
