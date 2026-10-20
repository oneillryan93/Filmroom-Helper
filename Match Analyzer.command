#!/bin/sh
# Match Analyzer launcher (macOS: double-click it; Linux: run it, or `sh "Match Analyzer.command"`).
# No questions asked: if uv isn't available it's installed into .tools/uv inside this folder (no admin, nothing
# added to PATH), then uv sets up Python and the libraries in pyproject.toml and starts the app, which opens in
# your browser. Only the first start downloads anything.
cd "$(dirname "$0")" || exit 1
LOCAL_UV="$(pwd)/.tools/uv"

find_uv() {
    UV=""
    # this folder's own copy first, then one already on the computer
    for c in "$LOCAL_UV/uv" "$LOCAL_UV/bin/uv" "$(command -v uv 2>/dev/null)" "$HOME/.local/bin/uv" "$HOME/.cargo/bin/uv"; do
        if [ -z "$UV" ] && [ -n "$c" ] && [ -x "$c" ]; then UV=$c; fi
    done
}

find_uv
if [ -z "$UV" ]; then
    echo "First start: setting up Match Analyzer. This only happens once and takes a minute or two..."
    if command -v curl >/dev/null 2>&1; then
        curl -LsSf https://astral.sh/uv/install.sh | env UV_INSTALL_DIR="$LOCAL_UV" UV_NO_MODIFY_PATH=1 sh
    else
        wget -qO- https://astral.sh/uv/install.sh | env UV_INSTALL_DIR="$LOCAL_UV" UV_NO_MODIFY_PATH=1 sh
    fi
    find_uv
fi
if [ -z "$UV" ]; then
    echo "Setup couldn't finish: check your internet connection and run this again."
    printf "Press Enter to close. "; read -r _
    exit 1
fi

# keep the AI add-on once it has been turned on in the app
if [ -f .models/ai-enabled ]; then exec "$UV" run --extra ai app.py "$@"; fi
exec "$UV" run app.py "$@"
