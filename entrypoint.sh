#!/bin/sh
set -eu

exec python -c 'from src.xray.config_renderer import main; raise SystemExit(main())'
