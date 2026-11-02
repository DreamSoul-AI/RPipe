"""Deliver SIGINT only inside this owned CLI process after its science worker is ready."""
import json
from pathlib import Path
import signal
import sys
import threading
import time

from rpipe.flow.cli import main

source, root, ready, output = map(Path, sys.argv[1:5])
command = sys.argv[5] if len(sys.argv) > 5 else 'run'
old_calls = []
def previous(signum, frame):
    old_calls.append(signum)
signal.signal(signal.SIGINT, previous)
errors = []
def interrupt():
    deadline = time.monotonic() + 12
    while not ready.is_file():
        if time.monotonic() >= deadline:
            errors.append('science worker never became ready')
            return
        time.sleep(.01)
    signal.raise_signal(signal.SIGINT)
thread = threading.Thread(target=interrupt, daemon=True)
thread.start()
arguments = [command, str(source), '--run-root', str(root)]
if command == 'supervise':
    arguments += ['--evidence-dir', str(output.with_suffix('.supervision')), '--run-seconds', '15',
                  '--batch-seconds', '20', '--rss-bytes', str(2 * 1024**3),
                  '--output-bytes', str(4 * 1024**2), '--cleanup-seconds', '4']
code = main(arguments)
thread.join(2)
output.write_text(json.dumps({'exit_code': code, 'old_handler_restored':
    signal.getsignal(signal.SIGINT) is previous, 'old_handler_calls': old_calls,
    'errors': errors, 'interrupt_thread_stopped': not thread.is_alive()}), encoding='utf-8')
raise SystemExit(code)
