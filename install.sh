#!/usr/bin/env bash
# ========================================
# Meme — One-line installer
# Usage: curl -sSL https://raw.githubusercontent.com/hyooeewee/Meme/main/install.sh | bash
# ========================================

set -euo pipefail

REPO_URL="${MEME_REPO:-https://github.com/hyooeewee/Meme}"
RAW_URL="${MEME_RAW:-https://raw.githubusercontent.com/hyooeewee/Meme/main}"
INSTALL_DIR="$HOME/.meme/bin"
PKG_DIR="$HOME/.meme/pkg"

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
    # Try specific versions first (Homebrew style), then generic python3
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

check_uv() {
    command -v uv &>/dev/null
}

check_git() {
    command -v git &>/dev/null
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

    # Check/install uv
    if ! check_uv; then
        info "Installing uv (Python package manager)..."
        curl -LsSf https://astral.sh/uv/install.sh | sh
        export PATH="$HOME/.local/bin:$PATH"
        if ! check_uv; then
            error "Failed to install uv. Please install manually: https://docs.astral.sh/uv/"
            exit 1
        fi
    fi
    info "uv $(uv --version) found"

    # Create directories
    mkdir -p "$INSTALL_DIR" "$PKG_DIR"

    # Clone or update repo
    if [[ -d "$PKG_DIR/.git" ]]; then
        info "Updating existing Meme repo..."
        git -C "$PKG_DIR" pull --ff-only 2>/dev/null || {
            warn "Pull failed, re-cloning..."
            rm -rf "$PKG_DIR"
            git clone --depth 1 "$REPO_URL" "$PKG_DIR"
        }
    else
        info "Cloning Meme repo..."
        rm -rf "$PKG_DIR"
        if check_git; then
            git clone --depth 1 "$REPO_URL" "$PKG_DIR"
        else
            # Fallback: download as tarball
            info "git not found, downloading tarball..."
            local tmp_tar
            tmp_tar=$(mktemp)
            if command -v curl &>/dev/null; then
                curl -sSL "$REPO_URL/archive/refs/heads/main.tar.gz" -o "$tmp_tar"
            else
                wget -qO "$tmp_tar" "$REPO_URL/archive/refs/heads/main.tar.gz"
            fi
            mkdir -p "$PKG_DIR"
            tar xzf "$tmp_tar" --strip-components=1 -C "$PKG_DIR"
            rm -f "$tmp_tar"
        fi
    fi

    # Create launcher symlink
    ln -sf "$PKG_DIR/meme" "$INSTALL_DIR/meme"
    chmod +x "$PKG_DIR/meme"

    # Install hook scripts
    info "Installing hook scripts..."
    for hook in session_start.sh query.sh session_end.sh; do
        local src="$PKG_DIR/hooks/$hook"
        local dest="$INSTALL_DIR/meme-$(echo "$hook" | tr '_' '-')"
        if [[ -f "$src" ]]; then
            ln -sf "$src" "$dest"
            chmod +x "$src"
        fi
    done

    # Add to PATH if needed
    if [[ ":$PATH:" != *":$INSTALL_DIR:"* ]]; then
        local shell_rc=""
        case "$(basename "$SHELL")" in
            zsh)  shell_rc="$HOME/.zshrc" ;;
            bash) shell_rc="$HOME/.bash_profile" ;;
            fish) shell_rc="$HOME/.config/fish/config.fish" ;;
            *)    shell_rc="$HOME/.profile" ;;
        esac

        if [[ -n "$shell_rc" ]]; then
            local path_line='export PATH="$HOME/.meme/bin:$PATH"'
            if ! grep -qF '.meme/bin' "$shell_rc" 2>/dev/null; then
                echo "" >> "$shell_rc"
                echo "# meme-memory-system" >> "$shell_rc"
                echo "$path_line" >> "$shell_rc"
                info "Added $INSTALL_DIR to PATH in $shell_rc"
            fi
        fi
        export PATH="$INSTALL_DIR:$PATH"
    fi

    # Run install
    info "Running meme setup..."
    "$INSTALL_DIR/meme" setup ${1+"$@"}

    echo ""
    info "Installation complete!"
    echo "  Run 'meme --help' to get started."
    echo "  You may need to restart your shell or run: source ~/.zshrc"
}

main "$@"
