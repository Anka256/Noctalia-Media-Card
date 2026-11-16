#!/bin/bash
# End-to-end test of the seekbar plugin against a silent fake MPRIS player.
T="$(cd "$(dirname "$0")" && pwd)"
rm -f ~/.cache/noctalia-seekbar/focus
python3 -I "$T/fakempris.py" "$T" > /tmp/seekbar-fake.log 2>&1 &
FP=$!
LOG=~/.cache/noctalia/noctalia.log; L=$(wc -l < "$LOG")
ipc(){ noctalia msg plugin onur/seekbar:seek all "$@" >/dev/null; }
R(){ tail -n +"$L" "$LOG" | grep -E 'seekbar R|ERR' | tail -1 | cut -c14-23,54-; }
noctalia msg plugin onur/seekbar:seek all debug on >/dev/null
sleep 2.5; echo "T1 start:"; R
ipc toggle; sleep 0.3; echo "T2 pause (immediate):"; R
sleep 2.5; echo "   after 2.5s, stays on paused player:"; R
ipc toggle; sleep 1.5; echo "T3 resume:"; R
ipc next; sleep 0.5; echo "T4 next (0.5s):"; R
ipc next; sleep 0.5; echo "T5 next (0.5s):"; R
ipc prev; sleep 0.5; echo "T6 prev (0.5s):"; R
ipc drag 140; ipc end; sleep 1.5; echo "T7 seek 140:"; R
echo "--- fake player:"; cat /tmp/seekbar-fake.log
echo "art files: $(ls ~/.cache/noctalia-seekbar/art/ | wc -l)"
kill "$FP"; rm -f ~/.cache/noctalia-seekbar/focus
sleep 1.5; echo "T8 player gone:"; R
echo "errors: $(tail -n +"$L" "$LOG" | grep -c ERR)"
