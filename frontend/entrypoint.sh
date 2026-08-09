#!/bin/sh
set -eu

# Generates config.js from API_BASE_URL at container start -- the only
# thing that needs to vary per environment, and doing it this way means
# no build step and no template engine for a single line of JS.
echo "window.API_BASE_URL = \"${API_BASE_URL}\";" > /app/config.js

exec python -m http.server 3000 --directory /app
