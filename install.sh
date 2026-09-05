#!/bin/sh
# Installer buat lanshare (share-lan) — macOS, Ubuntu, Fedora, dan Linux lain.
# Cuma butuh Python (>=3.9) yang udah ada di sistem. Nggak install package
# manager lain (uv, pipx, dll) dan nggak butuh git.
#
#   curl -LsSf https://raw.githubusercontent.com/harizinside/share-lan/main/install.sh | sh

set -eu

REPO_TARBALL="https://github.com/harizinside/share-lan/archive/refs/heads/main.tar.gz"
INSTALL_DIR="$HOME/.local/share/lanshare"
VENV_DIR="$INSTALL_DIR/venv"
BIN_DIR="$HOME/.local/bin"

err() {
    echo "✗ $1" >&2
    exit 1
}

find_python() {
    for candidate in python3 python; do
        if command -v "$candidate" >/dev/null 2>&1; then
            ok=$("$candidate" -c 'import sys; print(1 if sys.version_info >= (3, 9) else 0)' 2>/dev/null || echo 0)
            if [ "$ok" = "1" ]; then
                echo "$candidate"
                return 0
            fi
        fi
    done
    return 1
}

PYTHON=$(find_python) || {
    echo "✗ Python 3.9+ nggak ketemu di sistem lu." >&2
    echo "" >&2
    if command -v brew >/dev/null 2>&1 || [ "$(uname -s)" = "Darwin" ]; then
        echo "  macOS:  brew install python3" >&2
    fi
    if command -v apt >/dev/null 2>&1; then
        echo "  Ubuntu/Debian:  sudo apt install python3 python3-venv python3-pip" >&2
    fi
    if command -v dnf >/dev/null 2>&1; then
        echo "  Fedora:  sudo dnf install python3 python3-pip" >&2
    fi
    echo "" >&2
    echo "  Install Python dulu, terus jalanin lagi installer ini." >&2
    exit 1
}

echo "→ Pakai $($PYTHON --version 2>&1) ($PYTHON)"

mkdir -p "$INSTALL_DIR"

echo "→ Bikin virtualenv terisolasi di $VENV_DIR"
if ! "$PYTHON" -m venv "$VENV_DIR"; then
    echo "" >&2
    echo "✗ Gagal bikin virtualenv." >&2
    if command -v apt >/dev/null 2>&1; then
        echo "  Coba: sudo apt install python3-venv" >&2
    fi
    exit 1
fi

echo "→ Install lanshare dari $REPO_TARBALL"
"$VENV_DIR/bin/pip" install --quiet --upgrade pip
"$VENV_DIR/bin/pip" install --quiet --upgrade --force-reinstall "$REPO_TARBALL"

mkdir -p "$BIN_DIR"
ln -sf "$VENV_DIR/bin/lanshare" "$BIN_DIR/lanshare"

echo ""
echo "✓ lanshare kepasang di $BIN_DIR/lanshare"

case ":$PATH:" in
    *":$BIN_DIR:"*) ;;
    *)
        echo ""
        echo "  $BIN_DIR belum ada di PATH lu. Tambahin baris ini ke shell profile"
        echo "  (~/.bashrc, ~/.zshrc, dst), terus buka terminal baru:"
        echo ""
        echo "    export PATH=\"$BIN_DIR:\$PATH\""
        ;;
esac

echo ""
echo "  Coba: lanshare --help"
