#!/bin/bash
# PropIntel daily refresh — pulls fresh ABS data and rebuilds the report.
# Run manually (`./refresh.sh`) or on a schedule (see the LaunchAgent / README).

cd "/Users/gullesh/Claude Code/Domain Scraper" || exit 1
PY="/opt/anaconda3/bin/python3"
LOG="data/refresh.log"

echo "===== Sersi refresh started $(date '+%Y-%m-%d %H:%M:%S') =====" >> "$LOG"
# snapshot yesterday's data so the digest can diff against it
cp -f data/suburb_analysis.json data/suburb_analysis.prev.json 2>/dev/null
"$PY" -m propintel analyze  >> "$LOG" 2>&1
A=$?
if [ $A -ne 0 ]; then
  echo "!!! ANALYZE FAILED (exit $A) — data NOT updated; rebuilding report from existing data" >> "$LOG"
fi
"$PY" -m propintel.news_feed >> "$LOG" 2>&1   # pull fresh news / project announcements
"$PY" -m propintel.digest   >> "$LOG" 2>&1   # write the daily "what changed" update
"$PY" -m propintel report   >> "$LOG" 2>&1
R=$?
echo "===== Sersi refresh finished $(date '+%Y-%m-%d %H:%M:%S') (analyze=$A report=$R) =====" >> "$LOG"
STATUS=$(( A != 0 ? A : R ))   # non-zero if EITHER step failed, so failures are visible

# keep the log from growing forever (last ~500 lines)
tail -n 500 "$LOG" > "$LOG.tmp" && mv "$LOG.tmp" "$LOG"
exit $STATUS
