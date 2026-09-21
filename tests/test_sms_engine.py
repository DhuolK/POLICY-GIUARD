"""SMS engine: outbox, lanes, retry/DLQ, DLR, politeness gates — real Mongo."""
import copy
import datetime
import sys
import os
import unittest
from unittest.mock import patch

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pymongo

from app.services import sms_engine as engine
from app.services.sms_engine import (
    PRIORITY_TRANSACTIONAL, PRIORITY_STANDARD, PRIORITY_BULK,
    KIND_BROADCAST, KIND_RECEIPT, KIND_RENEWAL,
)
from app.services.sms_service import SmsResult
from app.services import sms_templates

TEST_DB = 'policy_guard_sms_engine_test'

# 22:30 Nairobi (inside default 21:00–07:00 quiet window).
QUIET_UTC = datetime.datetime(2026, 1, 5, 19, 30,
                              tzinfo=datetime.timezone.utc)
# 12:00 Nairobi (outside quiet window).
NOON_UTC = datetime.datetime(2026, 1, 5, 9, 0,
                             tzinfo=datetime.timezone.utc)


def _ok(ref='ATX_1'):
    return SmsResult(True, provider_ref=ref, status='Success')


def _transient():
    return SmsResult(False, status='HTTP_ERROR', error='timeout')


class Harness(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = pymongo.MongoClient('mongodb://localhost:27017')
        cls.db = cls.client[TEST_DB]
        p = patch('app.extensions.get_db', return_value=cls.db)
        p.start()
        cls.addClassCleanup(p.stop)

    def setUp(self):
        for coll in ('sms_outbox', 'sms_suppressions', 'sms_templates',
                     'app_settings', 'notifications', 'audit_logs'):
            self.db[coll].delete_many({})
        # Deterministic gates: quiet hours OFF unless a test says otherwise,
        # generous frequency cap unless a test says otherwise.
        self.db.app_settings.insert_one({
            'key': 'sms_engine',
            'quiet_hours': {'enabled': False, 'start': '21:00', 'end': '07:00'},
            'max_sms_per_customer_per_day': 10,
            'sms_cost_per_segment_kes': 1.0,
            'max_attempts': 5,
            'retry_base_delay_seconds': 60,
            'drain_batch_size': 100,
            'reconcile_sending_timeout_minutes': 10,
            'reconcile_sent_stale_hours': 24,
        })

    def _settings(self, **over):
        doc = self.db.app_settings.find_one({'key': 'sms_engine'})
        merged = dict(doc)
        merged.update(over)
        self.db.app_settings.replace_one({'key': 'sms_engine'}, merged)
        return merged


class TestPolitenessGates(Harness):
    def test_quiet_hours_defers_standard(self):
        self._settings(quiet_hours={'enabled': True, 'start': '21:00',
                                    'end': '07:00'})
        with patch.object(engine, '_utcnow', return_value=QUIET_UTC):
            self.assertTrue(engine.in_quiet_hours())
            doc = engine.enqueue_sms('+254712345678', 'hi', KIND_RENEWAL,
                                     priority=PRIORITY_STANDARD)
        self.assertEqual(doc['status'], 'queued')
        self.assertIsNotNone(doc['next_attempt_at'])
        naive = doc['next_attempt_at'].replace(tzinfo=None)
        self.assertGreater(naive, QUIET_UTC.replace(tzinfo=None))
        # Deferred to 07:00 Nairobi = 04:00 UTC.
        self.assertEqual(doc['next_attempt_at'].hour, 4)

    def test_quiet_hours_noon_sends_immediately(self):
        self._settings(quiet_hours={'enabled': True, 'start': '21:00',
                                    'end': '07:00'})
        with patch.object(engine, '_utcnow', return_value=NOON_UTC):
            self.assertFalse(engine.in_quiet_hours())
            doc = engine.enqueue_sms('+254712345678', 'hi', KIND_RENEWAL,
                                     priority=PRIORITY_STANDARD)
        self.assertEqual(doc['next_attempt_at'], NOON_UTC)

    def test_transactional_bypasses_quiet_hours(self):
        self._settings(quiet_hours={'enabled': True, 'start': '21:00',
                                    'end': '07:00'})
        with patch.object(engine, '_utcnow', return_value=QUIET_UTC):
            doc = engine.enqueue_sms('+254712345678', 'receipt',
                                     KIND_RECEIPT,
                                     priority=PRIORITY_TRANSACTIONAL)
        self.assertEqual(doc['next_attempt_at'], QUIET_UTC)

    def test_opt_out_suppresses_standard_not_transactional(self):
        engine.opt_out('+254712345678', source='test')
        std = engine.enqueue_sms('+254712345678', 'reminder', KIND_RENEWAL)
        self.assertEqual(std['status'], 'suppressed')
        self.assertEqual(std['suppress_reason'], 'opt_out')
        txn = engine.enqueue_sms('+254712345678', 'receipt', KIND_RECEIPT,
                                 priority=PRIORITY_TRANSACTIONAL)
        self.assertEqual(txn['status'], 'queued')
        self.assertTrue(txn['meta'].get('opted_out_bypass'))

    def test_inbound_stop_and_start(self):
        res = engine.handle_inbound('+254712345678', 'STOP')
        self.assertEqual(res['action'], 'opt_out')
        self.assertTrue(engine.is_opted_out('+254712345678'))
        res = engine.handle_inbound('+254712345678', 'START')
        self.assertEqual(res['action'], 'opt_in')
        self.assertFalse(engine.is_opted_out('+254712345678'))

    def test_frequency_cap_defers_to_morning(self):
        self._settings(max_sms_per_customer_per_day=1)
        with patch.object(engine, '_utcnow', return_value=NOON_UTC):
            first = engine.enqueue_sms('+254712345678', 'one', KIND_RENEWAL)
            second = engine.enqueue_sms('+254712345678', 'two', KIND_RENEWAL)
        self.assertEqual(first['next_attempt_at'], NOON_UTC)
        self.assertEqual(second.get('deferred_reason'), 'frequency_cap')
        self.assertGreater(second['next_attempt_at'], first['next_attempt_at'])
        # Manual staff action jumps the queue.
        with patch.object(engine, '_utcnow', return_value=NOON_UTC):
            manual = engine.enqueue_sms('+254712345678', 'three',
                                        KIND_RENEWAL, force=True)
        self.assertEqual(manual['next_attempt_at'], NOON_UTC)

    def test_invalid_number_never_queues(self):
        doc = engine.enqueue_sms('not-a-number', 'hi', KIND_RENEWAL)
        self.assertEqual(doc['status'], 'suppressed')
        self.assertEqual(doc['suppress_reason'], 'invalid')

    def test_idempotent_enqueue(self):
        a = engine.enqueue_sms('+254712345678', 'hi', KIND_RENEWAL,
                               idempotency_key='dup:1')
        b = engine.enqueue_sms('+254712345678', 'hi again', KIND_RENEWAL,
                               idempotency_key='dup:1')
        self.assertEqual(a['_id'], b['_id'])
        self.assertEqual(
            self.db.sms_outbox.count_documents(
                {'idempotency_key': 'dup:1'}), 1)


class TestDrainRetryDlq(Harness):
    def test_priority_lane_order(self):
        order = []
        with patch('app.services.sms_service.send_sms',
                   side_effect=lambda d, m: order.append(d) or _ok()) as m:
            engine.enqueue_sms('+254700000001', 'bulk', KIND_BROADCAST,
                               priority=PRIORITY_BULK, force=True)
            engine.enqueue_sms('+254700000002', 'standard', KIND_RENEWAL,
                               priority=PRIORITY_STANDARD, force=True)
            engine.enqueue_sms('+254700000003', 'receipt', KIND_RECEIPT,
                               priority=PRIORITY_TRANSACTIONAL)
            summary = engine.drain_outbox()
        self.assertEqual(summary['processed'], 3)
        self.assertEqual(order, ['+254700000003', '+254700000002',
                                 '+254700000001'])
        self.assertEqual(m.call_count, 3)

    def test_transient_retries_then_dead_letters(self):
        self._settings(max_attempts=2)
        with patch('app.services.sms_service.send_sms',
                   return_value=_transient()):
            doc = engine.enqueue_sms('+254712345678', 'hi', KIND_RENEWAL,
                                     force=True)
            s1 = engine.drain_outbox()
            mid = self.db.sms_outbox.find_one({'_id': doc['_id']})
            # Fast-forward past the backoff, drain again → DLQ.
            self.db.sms_outbox.update_one(
                {'_id': doc['_id']},
                {'$set': {'next_attempt_at': NOON_UTC}})
            with patch.object(engine, '_utcnow',
                              return_value=NOON_UTC + datetime.timedelta(hours=1)):
                s2 = engine.drain_outbox()
            final = self.db.sms_outbox.find_one({'_id': doc['_id']})
        self.assertEqual(s1['retried'], 1)
        self.assertEqual(mid['status'], 'failed')
        self.assertEqual(mid['attempts'], 1)
        self.assertEqual(s2['dead'], 1)
        self.assertEqual(final['status'], 'dead')
        self.assertEqual(final['dead_reason'], 'retries_exhausted')
        # DLQ arrivals page staff.
        self.assertTrue(self.db.notifications.count_documents(
            {'category': 'sms_failed'}) >= 1)

    def test_permanent_failure_never_retried(self):
        bad = SmsResult(False, status='InvalidPhoneNumber',
                        error='InvalidPhoneNumber')
        with patch('app.services.sms_service.send_sms', return_value=bad):
            doc = engine.enqueue_sms('+254712345678', 'hi', KIND_RENEWAL,
                                     force=True)
            summary = engine.drain_outbox()
            final = self.db.sms_outbox.find_one({'_id': doc['_id']})
        self.assertEqual(summary['dead'], 1)
        self.assertEqual(final['attempts'], 1)
        self.assertEqual(final['dead_reason'], 'permanent')

    def test_cost_recorded_on_send(self):
        self._settings(sms_cost_per_segment_kes=2.0)
        with patch('app.services.sms_service.send_sms', return_value=_ok()):
            doc = engine.enqueue_sms('+254712345678', 'x' * 161,
                                     KIND_RECEIPT,
                                     priority=PRIORITY_TRANSACTIONAL)
            engine.drain_outbox()
            final = self.db.sms_outbox.find_one({'_id': doc['_id']})
        self.assertEqual(final['segments'], 2)
        self.assertEqual(final['cost_kes'], 4.0)
        summary = engine.sms_cost_summary(days=1)
        self.assertGreaterEqual(summary['total']['spend_kes'], 4.0)

    def test_simulated_completes_immediately(self):
        sim = SmsResult(True, simulated=True)
        with patch('app.services.sms_service.send_sms', return_value=sim):
            doc = engine.enqueue_sms('+254712345678', 'hi', KIND_RENEWAL,
                                     force=True)
            summary = engine.drain_outbox()
            final = self.db.sms_outbox.find_one({'_id': doc['_id']})
        self.assertEqual(summary['simulated'], 1)
        self.assertEqual(final['status'], 'delivered')


class TestDlrAndReconcile(Harness):
    def _sent_doc(self, ref, **over):
        base = dict(
            idempotency_key=f'dlr:{ref}', kind=KIND_RENEWAL,
            priority=PRIORITY_STANDARD, destination='+254712345678',
            message='hi', status='sent', attempts=1,
            provider_ref=ref, provider_status='Sent', simulated=False,
            sent_at=NOON_UTC, created_at=NOON_UTC, updated_at=NOON_UTC,
            next_attempt_at=None)
        base.update(over)
        return self.db.sms_outbox.insert_one(base).inserted_id

    def test_dlr_delivered(self):
        oid = self._sent_doc('ATX_D1')
        res = engine.handle_delivery_report('ATX_D1', 'Delivered',
                                            phone='+254712345678')
        self.assertEqual(res, {'matched': True, 'outcome': 'delivered'})
        doc = self.db.sms_outbox.find_one({'_id': oid})
        self.assertEqual(doc['status'], 'delivered')
        self.assertIsNotNone(doc['delivered_at'])
        # Repeat DLR is a no-op.
        res2 = engine.handle_delivery_report('ATX_D1', 'Delivered')
        self.assertEqual(res2['outcome'], 'already_delivered')

    def test_dlr_failed_dead_letters(self):
        oid = self._sent_doc('ATX_F1')
        res = engine.handle_delivery_report('ATX_F1', 'Failed',
                                            failure_reason='No route')
        self.assertEqual(res['outcome'], 'dead')
        doc = self.db.sms_outbox.find_one({'_id': oid})
        self.assertEqual(doc['status'], 'dead')
        self.assertEqual(doc['dead_reason'], 'dlr:Failed')

    def test_dlr_unknown_ref(self):
        self.assertEqual(engine.handle_delivery_report('NOPE', 'Delivered'),
                         {'matched': False})

    def test_reconcile_reclaims_stuck_sending(self):
        old = NOON_UTC - datetime.timedelta(minutes=30)
        oid = self.db.sms_outbox.insert_one(dict(
            idempotency_key='stuck:1', kind=KIND_RENEWAL,
            priority=PRIORITY_STANDARD, destination='+254712345678',
            message='hi', status='sending', attempts=1,
            created_at=old, updated_at=old,
            next_attempt_at=old)).inserted_id
        with patch.object(engine, '_utcnow', return_value=NOON_UTC):
            res = engine.reconcile_stuck()
        self.assertEqual(res['reclaimed_sending'], 1)
        doc = self.db.sms_outbox.find_one({'_id': oid})
        self.assertEqual(doc['status'], 'failed')

    def test_reconcile_dead_letters_stale_sent(self):
        old = NOON_UTC - datetime.timedelta(hours=30)
        oid = self._sent_doc('ATX_OLD', sent_at=old, updated_at=old)
        sim_oid = self._sent_doc('ATX_SIM', sent_at=old, updated_at=old,
                                 simulated=True, status='delivered')
        with patch.object(engine, '_utcnow', return_value=NOON_UTC):
            res = engine.reconcile_stuck()
        self.assertEqual(res['dlr_timed_out'], 1)
        doc = self.db.sms_outbox.find_one({'_id': oid})
        self.assertEqual(doc['status'], 'dead')
        self.assertEqual(doc['dead_reason'], 'dlr-timeout')
        sim = self.db.sms_outbox.find_one({'_id': sim_oid})
        self.assertEqual(sim['status'], 'delivered')

    def test_requeue_dead(self):
        oid = self.db.sms_outbox.insert_one(dict(
            idempotency_key='dead:1', kind=KIND_BROADCAST,
            priority=PRIORITY_BULK, destination='+254712345678',
            message='hi', status='dead', attempts=5,
            dead_reason='retries_exhausted',
            created_at=NOON_UTC, updated_at=NOON_UTC,
            next_attempt_at=None)).inserted_id
        moved = engine.requeue_dead([str(oid)])
        self.assertEqual(moved, 1)
        doc = self.db.sms_outbox.find_one({'_id': oid})
        self.assertEqual(doc['status'], 'queued')
        self.assertEqual(doc['attempts'], 0)


class TestTemplates(Harness):
    def test_render_default(self):
        body, version = sms_templates.render(
            sms_templates.KEY_RENEWAL, type_bit='Motor ',
            policy_number='WL-1', days_remaining=3)
        self.assertIn('WL-1', body)
        self.assertIn('3 day(s)', body)
        self.assertGreaterEqual(version, 1)

    def test_save_bumps_version_and_sticks(self):
        v1 = sms_templates.get_template(sms_templates.KEY_BALANCE)[1]
        v2 = sms_templates.save_template(sms_templates.KEY_BALANCE,
                                         'Hi {first_name}, owe {amount_due}.')
        self.assertEqual(v2, v1 + 1)
        body, _ = sms_templates.render(
            sms_templates.KEY_BALANCE, first_name='Ann', amount_due='10.00',
            bill_bit='', ref_bit='', due_bit='')
        self.assertIn('Ann', body)

    def test_outbox_records_template_version(self):
        body, version = sms_templates.get_template(sms_templates.KEY_RENEWAL)
        doc = engine.enqueue_sms(
            '+254712345678', body, KIND_RENEWAL,
            template_key=sms_templates.KEY_RENEWAL,
            template_version=version, force=True)
        self.assertEqual(doc['template_key'], sms_templates.KEY_RENEWAL)
        self.assertEqual(doc['template_version'], version)


class TestSchedulerTick(Harness):
    def test_tick_on_empty_db_is_quiet_zeros(self):
        from app.services import reminder_service as rs
        from app.services.sms_engine import run_scheduler_tick
        with patch.object(rs, 'get_db', return_value=self.db):
            summary = run_scheduler_tick()
        self.assertEqual(summary['staff_sent'], 0)
        self.assertEqual(summary['sms_sent'], 0)
        self.assertEqual(summary['drained']['processed'], 0)
        self.assertEqual(summary['reconciled']['dlr_timed_out'], 0)

    def test_cost_summary_shape(self):
        summary = engine.sms_cost_summary(days=7)
        self.assertEqual(summary['days'], 7)
        self.assertIn('messages', summary['total'])
        self.assertIn('queue_depth', summary)
        self.assertIn('dead_lettered', summary)


if __name__ == '__main__':
    unittest.main()
