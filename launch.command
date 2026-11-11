#!/bin/bash
# ZIP installation: use only files on this computer; no Git checkout required.
set -u
cd "$(dirname "$0")" || exit 1
export PYTHONUTF8=1
export PYTHONUNBUFFERED=1
if [[ ! -f scripts/bootstrap.py || ! -d cavity_fem || ! -f requirements.txt ]]; then
  echo "ソースZIP全体を展開し、展開したcavity-lab-publicフォルダ内のlaunch.commandを開いてください。"
  read -r -p "Enterキーで終了します。" || true
  exit 1
fi
cavity_python="${CAVITY_FEM_PYTHON:-}"
if [[ -z "$cavity_python" ]]; then
  for cavity_candidate in python3.12 python3.11 python3.10 python3.9 python3 python; do
    if command -v "$cavity_candidate" >/dev/null 2>&1; then
      cavity_python="$(command -v "$cavity_candidate")"
      break
    fi
  done
fi
if [[ -z "$cavity_python" ]]; then
  echo "Python 3.9〜3.12が必要です（3.12推奨）。https://www.python.org/downloads/ からインストール後、再度開いてください。"
  read -r -p "Enterキーで終了します。" || true
  exit 1
fi
"$cavity_python" -X utf8 scripts/bootstrap.py "$@"
cavity_exit=$?
if [[ "$cavity_exit" -ne 0 ]]; then
  echo "起動できませんでした。上の案内を確認してください。"
  read -r -p "Enterキーで終了します。" || true
fi
exit "$cavity_exit"
