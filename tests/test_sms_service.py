import unittest
import sys
import os
from unittest.mock import patch, MagicMock

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services import sms_service
from app.services.sms_service import send_sms, customer_reminder_message


class TestSmsService(unittest.TestCase):
    def setUp(self):
        # Force simulation mode for every test in this class.
        self._env = {
            'AT_API_KEY': '', 'AT_USERNAME': '', 'AT_SENDER_ID': '',
            'SMS_SIMULATE': '1',
        }
        patches = [patch.dict(os.environ, self._env, clear=False)]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def test_simulates_when_unconfigured(self):
        result = send_sms('+254712345678', 'hello')
        self.assertTrue(result.ok)
        self.assertTrue(result.simulated)
        self.assertEqual(result.status, 'SIMULATED')

    def test_rejects_missing_destination(self):
        result = send_sms(None, 'hello')
        self.assertFalse(result.ok)
        self.assertIn('destination', result.error)

    def test_live_mode_success(self):
        with patch.dict(os.environ, {'AT_API_KEY': 'k', 'AT_USERNAME': 'u', 'SMS_SIMULATE': ''}):
            resp = MagicMock(status_code=200)
            resp.json.return_value = {'SMSMessageData': {'Recipients': [
                {'status': 'Success', 'messageId': 'ATX_1', 'phoneNumber': '+254712345678'}]}}
            with patch.object(sms_service.requests, 'post', return_value=resp) as post:
                result = send_sms('+254712345678', 'msg')
        self.assertTrue(result.ok)
        self.assertFalse(result.simulated)
        self.assertEqual(result.provider_ref, 'ATX_1')
        self.assertEqual(post.call_args.kwargs['timeout'], 15)

    def test_live_mode_provider_failure_status(self):
        with patch.dict(os.environ, {'AT_API_KEY': 'k', 'AT_USERNAME': 'u', 'SMS_SIMULATE': ''}):
            resp = MagicMock(status_code=200)
            resp.json.return_value = {'SMSMessageData': {'Recipients': [
                {'status': 'InvalidPhoneNumber', 'phoneNumber': '+254712345678'}]}}
            with patch.object(sms_service.requests, 'post', return_value=resp):
                result = send_sms('+254712345678', 'msg')
        self.assertFalse(result.ok)
        self.assertEqual(result.status, 'InvalidPhoneNumber')

    def test_live_mode_network_error_never_raises(self):
        with patch.dict(os.environ, {'AT_API_KEY': 'k', 'AT_USERNAME': 'u', 'SMS_SIMULATE': ''}):
            with patch.object(sms_service.requests, 'post',
                              side_effect=sms_service.requests.Timeout('boom')):
                result = send_sms('+254712345678', 'msg')
        self.assertFalse(result.ok)
        self.assertEqual(result.status, 'HTTP_ERROR')

    def test_customer_message_template(self):
        msg = customer_reminder_message('WL-2026-00124', 3, 'Motor')
        self.assertIn('Westlake Insurance Agency', msg)
        self.assertIn('WL-2026-00124', msg)
        self.assertIn('3 day(s)', msg)


if __name__ == '__main__':
    unittest.main()
