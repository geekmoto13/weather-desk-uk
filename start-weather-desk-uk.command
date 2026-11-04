#!/bin/bash
cd -- "$(dirname -- "$0")" || exit 1
exec python3 -I -X utf8 -B launch_weather.py --installed-python --open
