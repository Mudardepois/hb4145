#!/bin/sh
# Executa o main.py no HOST, a partir do PyCharm Flatpak.
# Use como "Shell Script" run configuration no PyCharm.
#
# Requer a permissao org.freedesktop.Flatpak no Flatpak do PyCharm:
#   flatpak override --user --talk-name=org.freedesktop.Flatpak com.jetbrains.PyCharm-Professional

DIR="$(cd "$(dirname "$0")" && pwd)"

if [ -x "$DIR/.venv-host/bin/python" ]; then
    PYTHON="$DIR/.venv-host/bin/python"
elif [ -x "$DIR/.venv/bin/python" ]; then
    PYTHON="$DIR/.venv/bin/python"
else
    PYTHON="python3"
fi

exec flatpak-spawn --host --directory="$DIR" "$PYTHON" main.py "$@"
