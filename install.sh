#!/usr/bin/env bash
# ========================================
# Meme — One-line installer
# Usage: curl -sSL https://raw.githubusercontent.com/hyooeewee/Meme/main/install.sh | bash
# ========================================

set -euo pipefail

REPO_URL="${MEME_REPO:-https://github.com/hyooeewee/Meme}"
INSTALL_DIR="$HOME/.meme/bin"
VENV_DIR="$HOME/.meme/venv"

# --- Colors ---
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

info()  { echo -e "${GREEN}[meme]${NC} $*"; }
warn()  { echo -e "${YELLOW}[meme]${NC} $*"; }
error() { echo -e "${RED}[meme]${NC} $*" >&2; }

# --- Check prerequisites ---
PYTHON_CMD=""
check_python() {
    for cmd in python3.12 python3.11 python3.10 python3 python; do
        if command -v "$cmd" &>/dev/null; then
            local ver
            ver=$($cmd -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
            local major minor
            major=$(echo "$ver" | cut -d. -f1)
            minor=$(echo "$ver" | cut -d. -f2)
            if [[ "$major" -ge 3 && "$minor" -ge 10 ]]; then
                PYTHON_CMD="$cmd"
                return 0
            fi
        fi
    done
    return 1
}

# --- Main ---
main() {
    info "Installing Meme memory system..."

    # Check Python
    if ! check_python; then
        error "Python 3.10+ is required."
        echo "  Install with: brew install python@3.12  (macOS)"
        echo "                apt install python3.10    (Ubuntu)"
        echo "                or visit https://www.python.org/downloads/"
        exit 1
    fi
    info "Python $($PYTHON_CMD -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")') found"

    # Create directories
    mkdir -p "$INSTALL_DIR"

    # Create venv (idempotent)
    if [[ ! -d "$VENV_DIR" ]]; then
        info "Creating virtual environment..."
        "$PYTHON_CMD" -m venv "$VENV_DIR"
    fi
    local venv_pip="$VENV_DIR/bin/pip"
    local venv_python="$VENV_DIR/bin/python"

    # Download and install from tarball
    info "Downloading Meme..."
    local tmp_dir
    tmp_dir=$(mktemp -d)
    local tmp_tar
    tmp_tar=$(mktemp)

    if command -v curl &>/dev/null; then
        curl -sSL "$REPO_URL/archive/refs/heads/main.tar.gz" -o "$tmp_tar"
    else
        wget -qO "$tmp_tar" "$REPO_URL/archive/refs/heads/main.tar.gz"
    fi

    tar xzf "$tmp_tar" --strip-components=1 -C "$tmp_dir"
    rm -f "$tmp_tar"

    info "Installing package..."
    "$venv_pip" install "$tmp_dir"
    rm -rf "$tmp_dir"

    # Symlink entry point into ~/.meme/bin/
    local venv_meme="$VENV_DIR/bin/meme"
    if [[ -f "$venv_meme" ]]; then
        ln -sf "$venv_meme" "$INSTALL_DIR/meme"
    fi

    # Ensure PATH is available in current shell
    export PATH="$INSTALL_DIR:$PATH"

    # Add to shell rc file if not already present
    local shell_rc=""
    case "$(basename "$SHELL")" in
        zsh)  shell_rc="$HOME/.zshrc" ;;
        bash) shell_rc="$HOME/.bash_profile" ;;
        fish) shell_rc="$HOME/.config/fish/config.fish" ;;
        *)    shell_rc="$HOME/.profile" ;;
    esac

    local added_to_rc=false
    if [[ -n "$shell_rc" ]]; then
        if ! grep -qF '.meme/bin' "$shell_rc" 2>/dev/null; then
            echo "" >> "$shell_rc"
            echo "# meme-memory-system" >> "$shell_rc"
            echo 'export PATH="$HOME/.meme/bin:$PATH"' >> "$shell_rc"
            info "Added $INSTALL_DIR to PATH in $shell_rc"
            added_to_rc=true
        fi
    fi

    # Run setup
    info "Running meme setup..."
    "$INSTALL_DIR/meme" setup ${1+"$@"}

    echo ""
    info "Installation complete!"
    echo "  Run 'meme --help' to get started."
    if [[ "$added_to_rc" == true ]]; then
        echo "  Restart your shell or run: source $shell_rc"
    fi
}

main "$@"
