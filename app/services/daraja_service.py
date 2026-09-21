import os
import re
import base64
import time
import datetime
import requests
from bson import ObjectId
from app.extensions import get_db


class DarajaService:
    """Safaricom Daraja integration — C2B Paybill only (STK push retired).

    Money arrives when the customer pays the agency Paybill from their own
    M-Pesa menu. Safaricom then POSTs the confirmation to our registered
    ConfirmationURL. Matching to a customer/policy happens on the
    BillRefNumber the customer typed (policy number by instruction), with
    phone fallback and a suspense queue for anything unrecognized.
    """

    _access_token = None
    _token_expiry = 0

    # Upper bound for a single Paybill payment (KES). Guards the unauthenticated
    # /payments/c2b/confirm endpoint against forged absurd amounts.
    MAX_C2B_AMOUNT = 10_000_000.0

    @classmethod
    def get_config(cls):
        env = os.environ.get("MPESA_ENV", "sandbox").lower()
        is_prod = (env == "production")
        base_url = "https://api.safaricom.co.ke" if is_prod else "https://sandbox.safaricom.co.ke"
        app_base = os.environ.get("APP_BASE_URL", "https://policyguard.co.ke").rstrip("/")

        return {
            "env": env,
            "base_url": base_url,
            "consumer_key": os.environ.get("MPESA_CONSUMER_KEY", ""),
            "consumer_secret": os.environ.get("MPESA_CONSUMER_SECRET", ""),
            "shortcode": os.environ.get("MPESA_SHORTCODE", "174379"),
            # Neutral paths on purpose: Safaricom silently filters callback
            # URLs containing "mpesa" / "m-pesa" / "safaricom".
            "confirmation_url": os.environ.get(
                "MPESA_C2B_CONFIRM_URL", f"{app_base}/payments/c2b/confirm"),
            "validation_url": os.environ.get(
                "MPESA_C2B_VALIDATE_URL", f"{app_base}/payments/c2b/validate"),
        }

    @classmethod
    def format_phone_number(cls, phone: str) -> str:
        """Normalizes Kenyan phone numbers to format: 254XXXXXXXXX"""
        if not phone:
            return ""
        # Strip spaces, dashes, plus signs
        cleaned = re.sub(r'[\s\-\+\(\)]', '', str(phone))
        if cleaned.startswith("0") and len(cleaned) == 10:
            return "254" + cleaned[1:]
        elif cleaned.startswith("7") and len(cleaned) == 9:
            return "254" + cleaned
        elif cleaned.startswith("1") and len(cleaned) == 9:
            return "254" + cleaned
        elif cleaned.startswith("254") and len(cleaned) == 12:
            return cleaned
        return cleaned

    @classmethod
    def get_access_token(cls) -> str:
        now = time.time()
        if cls._access_token and now < (cls._token_expiry - 60):
            return cls._access_token

        cfg = cls.get_config()
        if not cfg["consumer_key"] or not cfg["consumer_secret"]:
            return ""

        auth_url = f"{cfg['base_url']}/oauth/v1/generate?grant_type=client_credentials"
        auth_string = f"{cfg['consumer_key']}:{cfg['consumer_secret']}"
        encoded_auth = base64.b64encode(auth_string.encode('utf-8')).decode('utf-8')

        headers = {
            "Authorization": f"Basic {encoded_auth}",
            "Content-Type": "application/json"
        }

        try:
            resp = requests.get(auth_url, headers=headers, timeout=(3.0, 6.0))
            if resp.status_code == 200:
                data = resp.json()
                cls._access_token = data.get("access_token")
                expires_in = int(data.get("expires_in", 3599))
                cls._token_expiry = now + expires_in
                return cls._access_token
            else:
                return ""
        except requests.exceptions.Timeout:
            return ""
        except Exception:
            return ""

    # ------------------------------------------------------- C2B register --
    @classmethod
    def register_c2b_urls(cls) -> dict:
        """One-time (per environment) registration of confirm/validate URLs."""
        cfg = cls.get_config()
        token = cls.get_access_token()
        if not token:
            return {"success": False,
                    "message": "Daraja credentials missing — set MPESA_CONSUMER_KEY/SECRET."}
        url = f"{cfg['base_url']}/mpesa/c2b/v2/registerurl"
        payload = {
            "ShortCode": cfg["shortcode"],
            "ResponseType": "Completed",
            "ConfirmationURL": cfg["confirmation_url"],
            "ValidationURL": cfg["validation_url"],
        }
        try:
            resp = requests.post(
                url, json=payload,
                headers={"Authorization": f"Bearer {token}",
                         "Content-Type": "application/json"},
                timeout=(3.0, 10.0))
            data = resp.json()
            ok = resp.status_code == 200 and str(data.get("ResponseCode")) == "0"
            return {"success": ok, "response": data,
                    "message": data.get("ResponseDescription", str(data))}
        except Exception as e:
            return {"success": False, "message": f"C2B registration failed: {e}"}

    @classmethod
    def simulate_c2b(cls, amount, phone, bill_ref) -> dict:
        """Sandbox-only fake customer payment (no real money moves)."""
        cfg = cls.get_config()
        token = cls.get_access_token()
        if not token:
            return {"success": False, "message": "Daraja credentials missing."}
        url = f"{cfg['base_url']}/mpesa/c2b/v2/simulate"
        payload = {
            "ShortCode": cfg["shortcode"],
            "CommandID": "CustomerPayBillOnline",
            "Amount": str(int(amount)),
            "Msisdn": cls.format_phone_number(phone),
            "BillRefNumber": str(bill_ref),
        }
        try:
            resp = requests.post(
                url, json=payload,
                headers={"Authorization": f"Bearer {token}",
                         "Content-Type": "application/json"},
                timeout=(3.0, 10.0))
            return {"success": resp.status_code == 200, "response": resp.json()}
        except Exception as e:
            return {"success": False, "message": f"C2B simulate failed: {e}"}

    # ------------------------------------------------------- C2B match ----
    @classmethod
    def match_reference(cls, bill_ref, msisdn=""):
        """Resolve a typed BillRefNumber to (client_id, policy_id).

        Cascade: exact policy number (case-insensitive) -> client phone
        match on the sender MSISDN -> None (suspense queue).
        """
        db = get_db()
        ref = (bill_ref or "").strip()
        if ref:
            policy = db.policies.find_one(
                {"policy_number": {"$regex": f"^{re.escape(ref)}$", "$options": "i"}})
            if policy and policy.get("client_id"):
                return policy["client_id"], policy["_id"]
        phone = cls.format_phone_number(msisdn)
        if phone:
            short = phone[3:]  # last 9 digits; stored phones vary in format
            client = db.users.find_one({
                "role": "customer",
                "$or": [
                    {"phone": phone},
                    {"phone": {"$regex": f"{short}$"}},
                ],
            })
            if client:
                return client["_id"], None
        return None, None

    # ------------------------------------------------------- C2B confirm --
    @classmethod
    def _ensure_indexes(cls):
        db = get_db()
        try:
            db.mpesa_transactions.create_index("trans_id", unique=True, sparse=True)
        except Exception:
            pass

    @classmethod
    def process_c2b_confirmation(cls, payload: dict) -> dict:
        """Handle Safaricom's C2B confirmation POST. Idempotent on TransID."""
        from app.services.payment_service import PaymentService

        cls._ensure_indexes()
        db = get_db()
        now = datetime.datetime.now(datetime.timezone.utc)

        trans_id = str(payload.get("TransID", "") or "").strip()
        if not trans_id:
            return {"status": "ignored", "reason": "No TransID in payload"}
        try:
            trans_amount = round(float(str(payload.get("TransAmount", "0"))), 2)
        except (TypeError, ValueError):
            trans_amount = 0.0
        # Safaricom only ever sends positive amounts. A zero/negative/huge
        # TransAmount means the callback did not come from Safaricom (or is
        # corrupt): allocating it would mint money or credit the customer, so
        # reject and surface it to staff instead of trusting the body.
        if trans_amount <= 0 or trans_amount > cls.MAX_C2B_AMOUNT:
            from app.services.notification_service import (
                NotificationService, CATEGORY_SYSTEM, SEVERITY_WARNING)
            NotificationService.create_staff(
                CATEGORY_SYSTEM, SEVERITY_WARNING,
                "Rejected C2B confirmation",
                f"Implausible TransAmount {trans_amount!r} for TransID "
                f"'{trans_id}' from {payload.get('MSISDN', 'unknown')}; "
                "no payment was recorded.")
            return {"status": "rejected", "reason": "Implausible TransAmount"}
        bill_ref = str(payload.get("BillRefNumber", "") or "").strip()
        msisdn = cls.format_phone_number(payload.get("MSISDN", ""))
        trans_time = str(payload.get("TransTime", "") or "")

        existing = db.mpesa_transactions.find_one({"trans_id": trans_id})
        if existing and existing.get("status") in ("CONFIRMED", "ALLOCATED"):
            return {"status": "already_processed", "receipt": trans_id}

        client_oid, policy_oid = cls.match_reference(bill_ref, msisdn)

        tx_doc = {
            "channel": "C2B",
            "trans_id": trans_id,
            "trans_amount": trans_amount,
            "bill_ref_number": bill_ref,
            "msisdn": msisdn,
            "trans_time": trans_time,
            "business_shortcode": str(payload.get("BusinessShortCode", "")),
            "org_balance": str(payload.get("OrgAccountBalance", "")),
            "raw_callback": payload,
            "created_at": now,
            "updated_at": now,
        }

        if client_oid is None:
            tx_doc.update({"status": "UNALLOCATED", "client_id": None,
                           "policy_id": None})
            db.mpesa_transactions.update_one(
                {"trans_id": trans_id}, {"$set": tx_doc}, upsert=True)
            from app.services.notification_service import (
                NotificationService, CATEGORY_SYSTEM, SEVERITY_WARNING)
            NotificationService.create_staff(
                CATEGORY_SYSTEM, SEVERITY_WARNING,
                "Unallocated Paybill payment",
                f"KES {trans_amount:,.2f} (ref '{bill_ref or 'blank'}', "
                f"{msisdn or 'unknown phone'}) needs a customer allocation.")
            return {"status": "unallocated", "receipt": trans_id}

        receipt = PaymentService.allocate_payment(
            client_id=client_oid,
            amount=trans_amount,
            method="Paybill C2B",
            policy_id=policy_oid,
            phone_number=msisdn,
            description=f"Paybill {trans_id} ref {bill_ref}",
            extra={"c2b_trans_id": trans_id, "bill_ref_number": bill_ref},
        )
        tx_doc.update({"status": "ALLOCATED", "client_id": client_oid,
                       "policy_id": policy_oid,
                       "receipt_id": ObjectId(receipt["_id"]),
                       "receipt_number": receipt["receipt_number"]})
        db.mpesa_transactions.update_one(
            {"trans_id": trans_id}, {"$set": tx_doc}, upsert=True)

        from .audit_service import AuditService
        AuditService.log_action(
            entity_type="payment",
            entity_id=receipt["_id"],
            action="paybill_c2b_confirmed",
            performed_by="system",
            details={"trans_id": trans_id, "amount": trans_amount,
                     "bill_ref": bill_ref,
                     "receipt": receipt["receipt_number"]},
        )
        try:
            # Transactional lane: enqueue only (this webhook must answer
            # Safaricom fast). The drainer sends it ahead of reminders and
            # bulk, bypassing quiet hours and frequency caps — money just
            # moved, the customer expects proof now.
            from app.services.sms_engine import enqueue_sms, PRIORITY_TRANSACTIONAL, KIND_RECEIPT
            from app.services.sms_service import payment_receipt_message
            from app.services.sms_templates import render as render_template, KEY_RECEIPT
            from app.utils.phone import normalize_ke_phone
            client = db.users.find_one({"_id": ObjectId(client_oid)})
            dest = normalize_ke_phone(client.get("phone")) if client else None
            if dest:
                balance = PaymentService.client_balance_due(client_oid)
                try:
                    balance_due = round(float(balance or 0), 2)
                except (TypeError, ValueError):
                    balance_due = 0
                if balance_due and balance_due > 0:
                    balance_bit = (f' Outstanding balance: '
                                   f'KES {balance_due:,.2f}.')
                else:
                    balance_bit = ' Your account is fully settled. Thank you.'
                try:
                    amount = f'{float(trans_amount):,.2f}'
                except (TypeError, ValueError):
                    amount = str(trans_amount)
                message, template_version = render_template(
                    KEY_RECEIPT, amount=amount, trans_id=trans_id,
                    balance_bit=balance_bit)
                enqueue_sms(
                    dest, message, KIND_RECEIPT,
                    priority=PRIORITY_TRANSACTIONAL,
                    idempotency_key=f"receipt:{trans_id}",
                    template_key=KEY_RECEIPT,
                    template_version=template_version,
                    client_id=client_oid,
                    policy_id=policy_oid,
                    meta={'trans_id': trans_id, 'amount': trans_amount,
                          'balance_due': balance_due})
        except Exception:
            pass

        return {"status": "allocated", "receipt": receipt["receipt_number"],
                "amount": trans_amount}

    @classmethod
    def allocate_suspense(cls, trans_id, client_id, policy_id=None,
                         recorded_by=None) -> dict:
        """Manually attach an UNALLOCATED confirmation to a customer."""
        from app.services.payment_service import PaymentService

        db = get_db()
        tx = db.mpesa_transactions.find_one({"trans_id": trans_id})
        if not tx:
            return {"success": False, "message": "Paybill transaction not found."}
        if tx.get("status") == "ALLOCATED":
            return {"success": False, "message": "Already allocated."}
        try:
            client_oid = ObjectId(client_id)
        except Exception:
            return {"success": False, "message": "Invalid customer."}
        if not db.users.find_one({"_id": client_oid, "role": "customer"}):
            return {"success": False, "message": "Customer not found."}
        policy_oid = None
        if policy_id:
            try:
                policy_oid = ObjectId(policy_id)
            except Exception:
                return {"success": False, "message": "Invalid policy."}

        receipt = PaymentService.allocate_payment(
            client_id=client_oid,
            amount=float(tx.get("trans_amount") or 0),
            method="Paybill C2B",
            policy_id=policy_oid,
            phone_number=tx.get("msisdn"),
            description=f"Paybill {trans_id} ref {tx.get('bill_ref_number')} (manual allocation)",
            recorded_by=recorded_by,
            extra={"c2b_trans_id": trans_id,
                   "bill_ref_number": tx.get("bill_ref_number")},
        )
        db.mpesa_transactions.update_one(
            {"trans_id": trans_id},
            {"$set": {"status": "ALLOCATED", "client_id": client_oid,
                      "policy_id": policy_oid,
                      "receipt_id": ObjectId(receipt["_id"]),
                      "receipt_number": receipt["receipt_number"],
                      "allocated_by": str(recorded_by) if recorded_by else None,
                      "updated_at": datetime.datetime.now(datetime.timezone.utc)}})

        from .audit_service import AuditService
        AuditService.log_action(
            entity_type="payment", entity_id=receipt["_id"],
            action="paybill_suspense_allocated", performed_by=str(recorded_by or "system"),
            details={"trans_id": trans_id,
                     "receipt": receipt["receipt_number"]})
        return {"success": True, "receipt": receipt["receipt_number"],
                "message": f"KES {receipt['amount']:,.2f} allocated. Receipt {receipt['receipt_number']}."}

    @classmethod
    def get_transactions(cls, user=None, limit=50):
        db = get_db()
        from ..utils.visibility import is_admin, visible_client_ids

        query = {"channel": "C2B"}
        if not is_admin(user):
            c_ids = visible_client_ids(user)
            if c_ids is not None:
                query = {"$and": [
                    {"channel": "C2B"},
                    {"$or": [
                        {"client_id": {"$in": c_ids}},
                        {"status": "UNALLOCATED"},
                    ]},
                ]}

        txs = list(db.mpesa_transactions.find(query).sort("created_at", -1).limit(limit))

        for t in txs:
            t["_id"] = str(t["_id"])
            if t.get("policy_id"):
                try:
                    pol = db.policies.find_one({"_id": ObjectId(t["policy_id"])})
                except Exception:
                    pol = None
                t["policy_number"] = pol.get("policy_number", "Unknown") if pol else "Unknown"
            else:
                t["policy_number"] = t.get("bill_ref_number") or "Direct / N/A"

            if t.get("client_id"):
                try:
                    cli = db.users.find_one({"_id": ObjectId(t["client_id"])})
                except Exception:
                    cli = None
                t["client_name"] = cli.get("full_name", "Unknown") if cli else "Unknown"
            else:
                t["client_name"] = "Unallocated"

        return txs

    @classmethod
    def get_suspense(cls, user=None, limit=100):
        """UNALLOCATED confirmations visible to `user` (admin: all)."""
        db = get_db()
        from ..utils.visibility import is_admin, visible_client_ids

        query = {"channel": "C2B", "status": "UNALLOCATED"}
        if not is_admin(user):
            c_ids = set(visible_client_ids(user) or [])
            phones = set()
            for c in db.users.find({"_id": {"$in": list(c_ids)}}, {"phone": 1}):
                full = cls.format_phone_number(c.get("phone", ""))
                if full:
                    phones.add(full)
                    phones.add(full[3:])
            query = {"$and": [query, {"$or": [
                {"msisdn": {"$in": list(phones)}},
            ]}]} if phones else {"_id": None}
        return list(db.mpesa_transactions.find(query).sort("created_at", -1).limit(limit))
