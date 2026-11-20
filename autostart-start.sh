#!/bin/sh
# Started at login by ~/Library/LaunchAgents/com.sasha.arbscanner.plist. launchd runs it
# again whenever it exits, so the scanner comes back after a crash or a restart.
RUN="$HOME/arb-scanner/run"
DATA="$HOME/arb-scanner/data"
# Already running (for example started by hand)? Check again in 5 minutes.
if /usr/bin/curl -s -m 5 -o /dev/null http://127.0.0.1:8787/; then sleep 300; exit 0; fi
cd "$RUN" || { sleep 300; exit 1; }
echo $$ > "$DATA/scanner.pid"
exec /usr/bin/python3 -u -m scanner.run >> "$DATA/scanner.log" 2>&1
