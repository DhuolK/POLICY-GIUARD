"""Africa's Talking live-path test, through the real engine.

Usage::

    # Full engine path (enqueue → drain → ledger), simulated unless --live:
    python scripts/test_at_sms.py +254712345678 "hello from PolicyGuard"

    # Live provider call (spends money / sandbox credit):
    python scripts/test_at_sms.py +254712345678 --live

    # Provider only, skip the outbox (quick credential check):
    python scripts/test_at_sms.py +254712345678 --live --direct

Sandbox notes:
- AT_USERNAME=sandbox + a sandbox API key delivers ONLY to phone numbers
  registered as test devices in the AT sandbox dashboard. Unregistered
  numbers return odd recipient statuses — that is AT telling you the
  number is not a test device, not an engine bug.
- Sending 'STOP' to your sender ID exercises the opt-out path; check
  /sms/status afterwards.
- Set the AT dashboard callback URLs to https://<host>/sms/dlr and
  https://<host>/sms/inbound, then reply to a live message to watch the
  ledger flip sent → delivered.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv  # noqa: E402

load_dotenv()


def main():
    args = [a for a in sys.argv[1:] if not a.startswith('--')]
    live = '--live' in sys.argv
    direct = '--direct' in sys.argv
    if not args:
        print(__doc__)
        sys.exit(2)
    destination, message = args[0], (args[1] if len(args) > 1 else
                                     'PolicyGuard SMS engine test.')

    if live:
        os.environ['SMS_SIMULATE'] = '0'
    from app import create_app  # noqa: E402
    app = create_app(os.environ.get('FLASK_ENV', 'development'))
    with app.app_context():
        from app.services import sms_service
        print(f"AT_USERNAME={os.environ.get('AT_USERNAME')!r} "
              f"key_set={bool(os.environ.get('AT_API_KEY'))} "
              f"simulate={os.environ.get('SMS_SIMULATE')!r}")

        if direct:
            result = sms_service.send_sms(destination, message)
            print('direct result:', result.as_dict())
            print('segments:',
                  sms_service.estimate_segments(message))
            return

        from app.services.sms_engine import enqueue_sms, drain_outbox
        from app.extensions import get_db
        doc = enqueue_sms(
            destination, message, 'engine_test',
            priority=1, force=True,
            meta={'test': True, 'live': live})
        print('enqueued:', str(doc['_id']), 'status=', doc.get('status'))
        summary = drain_outbox()
        print('drain:', {k: v for k, v in summary.items()
                         if k != 'details'})
        final = get_db().sms_outbox.find_one({'_id': doc['_id']})
        print('ledger:', {k: final.get(k) for k in (
            'status', 'provider_ref', 'provider_status', 'last_error',
            'segments', 'cost_kes', 'simulated', 'attempts')})
        if final.get('status') == 'sent' and not final.get('simulated'):
            print('→ live send accepted. Watch it flip to delivered at '
                  '/sms/status once AT POSTs the DLR.')


if __name__ == '__main__':
    main()
