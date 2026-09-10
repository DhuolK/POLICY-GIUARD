import unittest
import sys
import os

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.utils.phone import normalize_ke_phone, is_valid_ke_phone, sms_destination_for_client


class TestPhoneUtils(unittest.TestCase):
    def test_local_safaricom_format(self):
        self.assertEqual(normalize_ke_phone('0712345678'), '+254712345678')

    def test_local_airtel_format(self):
        self.assertEqual(normalize_ke_phone('0123456789'), '+254123456789')

    def test_plus_country_code(self):
        self.assertEqual(normalize_ke_phone('+254712345678'), '+254712345678')

    def test_country_code_no_plus(self):
        self.assertEqual(normalize_ke_phone('254712345678'), '+254712345678')

    def test_spaces_dashes_parens(self):
        self.assertEqual(normalize_ke_phone('+254 (712) 345-678'), '+254712345678')
        self.assertEqual(normalize_ke_phone('0712 345 678'), '+254712345678')

    def test_rejects_landline(self):
        # Nairobi landline 020-xxxxxxx has only 7 subscriber digits after trunk.
        self.assertIsNone(normalize_ke_phone('+254201234567'))

    def test_rejects_too_short_or_garbage(self):
        for bad in ('', None, '12345', 'abcdefghij', '07123456789', '+254812345678'):
            self.assertIsNone(normalize_ke_phone(bad), f'{bad!r} should be invalid')

    def test_nine_digits_after_prefix_required(self):
        # 0712345678 has exactly 9 digits after the 0 -> valid
        self.assertTrue(is_valid_ke_phone('0712345678'))
        # extra digit -> invalid
        self.assertFalse(is_valid_ke_phone('07123456789'))

    def test_destination_prefers_phone_over_alt(self):
        client = {'phone': 'bad-number', 'alt_phone': '0712345678'}
        self.assertEqual(sms_destination_for_client(client), '+254712345678')

    def test_destination_none_when_missing(self):
        self.assertIsNone(sms_destination_for_client(None))
        self.assertIsNone(sms_destination_for_client({}))
        self.assertIsNone(sms_destination_for_client({'phone': ''}))


if __name__ == '__main__':
    unittest.main()
