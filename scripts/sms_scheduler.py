"""SMS engine scheduler — the "auto" in auto-reminders.

Loop mode (preferred on a server)::

    python scripts/sms_scheduler.py

One-shot mode (cron / Windows Task Scheduler, every minute)::

    python scripts/sms_scheduler.py --once
    # or: flask sms-tick

Each tick: queue due renewal reminders → drain all lanes (transactional
first) → reconcile stuck claims and DLR-timeouts. Ticks are idempotent:
re-running never double-sends (idempotency keys + atomic claims).
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv  # noqa: E402

load_dotenv()

from app import create_app  # noqa: E402
from app.services.sms_engine import run_scheduler_tick  # noqa: E402


def main():
    once = '--once' in sys.argv
    app = create_app(os.environ.get('FLASK_ENV', 'development'))
    interval = int(os.environ.get('SMS_DRAIN_INTERVAL_SEC', '60'))
    with app.app_context():
        if once:
            print(run_scheduler_tick(), flush=True)
            return
        print(f'sms scheduler looping every {interval}s (Ctrl+C to stop)',
              flush=True)
        while True:
            try:
                summary = run_scheduler_tick()
                if any([summary.get('staff_sent'), summary.get('sms_sent'),
                        summary.get('sms_failed'),
                        summary['drained'].get('processed'),
                        summary['reconciled'].get('reclaimed_sending'),
                        summary['reconciled'].get('dlr_timed_out')]):
                    print(summary, flush=True)
            except Exception as exc:  # a tick must never kill the loop
                print(f'tick failed: {exc}', flush=True)
            time.sleep(interval)


if __name__ == '__main__':
    main()
