#!/usr/bin/env python3
"""
MongoDB to MySQL migration script for POLICYGUARD system.
Handles data transfer across all 17 collections/tables with foreign key mapping,
datetime conversion, error trapping, and data reconciliation statistics.
"""
import os
import sys
import json
import logging
from datetime import datetime, timezone

project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, project_root)

from app import create_app
from app.extensions import db, mongo_db
from app.models import (
    User, Vehicle, InsuranceCompany, PolicyType, Policy, PolicyVersion,
    Claim, Payment, MpesaTransaction, Reminder, SmsOutbox, SmsSuppression,
    Notification, AuditLog, AppSetting, Counter, Smstemplate
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


def _to_utc_datetime(val):
    if val is None:
        return None
    if isinstance(val, datetime):
        return val.replace(tzinfo=timezone.utc) if val.tzinfo is None else val
    if isinstance(val, str):
        try:
            return datetime.fromisoformat(val.replace("Z", "+00:00"))
        except Exception:
            try:
                return datetime.strptime(val[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
            except Exception:
                return None
    return None


class MongoToMySQLMigrator:
    def __init__(self):
        self.app = create_app('migration')
        self.app.app_context().push()
        self.mongo_db = mongo_db
        if self.mongo_db is None:
            from app.extensions import init_db
            init_db(self.app)
            self.mongo_db = mongo_db
        self.mysql_db = db

        self.stats = {
            'users': {'mongo_count': 0, 'mysql_count': 0, 'errors': 0},
            'vehicles': {'mongo_count': 0, 'mysql_count': 0, 'errors': 0},
            'insurance_companies': {'mongo_count': 0, 'mysql_count': 0, 'errors': 0},
            'policy_types': {'mongo_count': 0, 'mysql_count': 0, 'errors': 0},
            'policies': {'mongo_count': 0, 'mysql_count': 0, 'errors': 0},
            'policy_versions': {'mongo_count': 0, 'mysql_count': 0, 'errors': 0},
            'claims': {'mongo_count': 0, 'mysql_count': 0, 'errors': 0},
            'payments': {'mongo_count': 0, 'mysql_count': 0, 'errors': 0},
            'mpesa_transactions': {'mongo_count': 0, 'mysql_count': 0, 'errors': 0},
            'reminders': {'mongo_count': 0, 'mysql_count': 0, 'errors': 0},
            'sms_outbox': {'mongo_count': 0, 'mysql_count': 0, 'errors': 0},
            'sms_suppressions': {'mongo_count': 0, 'mysql_count': 0, 'errors': 0},
            'notifications': {'mongo_count': 0, 'mysql_count': 0, 'errors': 0},
            'audit_logs': {'mongo_count': 0, 'mysql_count': 0, 'errors': 0},
            'app_settings': {'mongo_count': 0, 'mysql_count': 0, 'errors': 0},
            'counters': {'mongo_count': 0, 'mysql_count': 0, 'errors': 0},
            'smstemplates': {'mongo_count': 0, 'mysql_count': 0, 'errors': 0}
        }

        self.id_maps = {
            'users': {},
            'vehicles': {},
            'insurance_companies': {},
            'policy_types': {},
            'policies': {},
            'policy_versions': {},
            'claims': {},
            'payments': {},
            'mpesa_transactions': {},
            'reminders': {},
            'sms_outbox': {},
            'sms_suppressions': {},
            'notifications': {},
            'audit_logs': {},
            'app_settings': {},
            'counters': {},
            'smstemplates': {}
        }

    def clear_mysql_tables(self):
        logger.info("Clearing MySQL tables in dependency order...")
        tables_to_clear = [
            'sms_templates', 'counters', 'app_settings', 'audit_logs',
            'notifications', 'sms_suppressions', 'sms_outbox', 'reminders',
            'mpesa_transactions', 'payments', 'claims', 'policy_versions',
            'policies', 'policy_types', 'insurance_companies', 'vehicles', 'users'
        ]
        for table_name in tables_to_clear:
            try:
                db.session.execute(db.text(f"DELETE FROM {table_name}"))
                db.session.commit()
            except Exception as e:
                logger.warning(f"Could not clear table {table_name}: {e}")
                db.session.rollback()

    # 1. Users
    def migrate_users(self):
        logger.info("Migrating users...")
        if self.mongo_db is None or 'users' not in self.mongo_db.list_collection_names():
            logger.info("MongoDB users collection not found, skipping.")
            return

        collection = self.mongo_db.users
        mongo_count = collection.count_documents({})
        self.stats['users']['mongo_count'] = mongo_count

        for doc in collection.find():
            try:
                mongo_id = str(doc.get('_id'))
                created_at = _to_utc_datetime(doc.get('created_at')) or datetime.now(timezone.utc)
                updated_at = _to_utc_datetime(doc.get('updated_at')) or datetime.now(timezone.utc)
                locked_until = _to_utc_datetime(doc.get('locked_until'))

                user = User(
                    email=doc.get('email', f"user_{mongo_id}@policyguard.local"),
                    password_hash=doc.get('password_hash'),
                    full_name=doc.get('full_name') or doc.get('name') or 'Unnamed User',
                    phone=doc.get('phone'),
                    role=doc.get('role', 'customer'),
                    failed_login_attempts=doc.get('failed_login_attempts', 0),
                    locked_until=locked_until,
                    disabled=bool(doc.get('disabled', False)),
                    client_id_number=doc.get('client_id_number') or doc.get('client_id'),
                    kra_pin=doc.get('kra_pin'),
                    created_at=created_at,
                    updated_at=updated_at
                )
                if mongo_id.isdigit():
                    user.id = int(mongo_id)

                db.session.add(user)
                db.session.flush()

                self.id_maps['users'][mongo_id] = user.id
                self.stats['users']['mysql_count'] += 1
            except Exception as e:
                logger.error(f"Error migrating user {doc.get('_id')}: {e}")
                self.stats['users']['errors'] += 1
                db.session.rollback()

        db.session.commit()
        logger.info(f"Users migrated: {self.stats['users']['mysql_count']}/{mongo_count}")

    # 2. Vehicles
    def migrate_vehicles(self):
        logger.info("Migrating vehicles...")
        if self.mongo_db is None or 'vehicles' not in self.mongo_db.list_collection_names():
            return

        collection = self.mongo_db.vehicles
        mongo_count = collection.count_documents({})
        self.stats['vehicles']['mongo_count'] = mongo_count

        for doc in collection.find():
            try:
                mongo_id = str(doc.get('_id'))
                owner_id_mongo = str(doc.get('owner_id')) if doc.get('owner_id') is not None else None
                owner_id = self.id_maps['users'].get(owner_id_mongo)
                if not owner_id:
                    # fallback to any existing user or skip if invalid
                    continue

                created_at = _to_utc_datetime(doc.get('created_at')) or datetime.now(timezone.utc)
                updated_at = _to_utc_datetime(doc.get('updated_at')) or datetime.now(timezone.utc)

                vehicle = Vehicle(
                    owner_id=owner_id,
                    registration_number=doc.get('registration_number', f"REG-{mongo_id}"),
                    make=doc.get('make', 'Unknown'),
                    model=doc.get('model', 'Unknown'),
                    year=doc.get('year'),
                    category=doc.get('category', 'motor'),
                    seating_capacity=doc.get('seating_capacity', 5),
                    created_at=created_at,
                    updated_at=updated_at
                )
                if mongo_id.isdigit():
                    vehicle.id = int(mongo_id)

                db.session.add(vehicle)
                db.session.flush()
                self.id_maps['vehicles'][mongo_id] = vehicle.id
                self.stats['vehicles']['mysql_count'] += 1
            except Exception as e:
                logger.error(f"Error migrating vehicle {doc.get('_id')}: {e}")
                self.stats['vehicles']['errors'] += 1
                db.session.rollback()

        db.session.commit()
        logger.info(f"Vehicles migrated: {self.stats['vehicles']['mysql_count']}/{mongo_count}")

    # 3. Insurance Companies
    def migrate_insurance_companies(self):
        logger.info("Migrating insurance companies...")
        if self.mongo_db is None or 'insurance_companies' not in self.mongo_db.list_collection_names():
            return

        collection = self.mongo_db.insurance_companies
        mongo_count = collection.count_documents({})
        self.stats['insurance_companies']['mongo_count'] = mongo_count

        for doc in collection.find():
            try:
                mongo_id = str(doc.get('_id'))
                created_at = _to_utc_datetime(doc.get('created_at')) or datetime.now(timezone.utc)
                updated_at = _to_utc_datetime(doc.get('updated_at')) or datetime.now(timezone.utc)

                company = InsuranceCompany(
                    code=doc.get('code', f"UW-{mongo_id}"),
                    name=doc.get('name', 'Underwriter Company'),
                    ira_license_number=doc.get('ira_license_number'),
                    contact_email=doc.get('contact_email') or doc.get('email'),
                    contact_phone=doc.get('contact_phone') or doc.get('phone'),
                    commission_rate=float(doc.get('commission_rate', 0.00)),
                    created_at=created_at,
                    updated_at=updated_at
                )
                if mongo_id.isdigit():
                    company.id = int(mongo_id)

                db.session.add(company)
                db.session.flush()
                self.id_maps['insurance_companies'][mongo_id] = company.id
                self.stats['insurance_companies']['mysql_count'] += 1
            except Exception as e:
                logger.error(f"Error migrating company {doc.get('_id')}: {e}")
                self.stats['insurance_companies']['errors'] += 1
                db.session.rollback()

        db.session.commit()
        logger.info(f"Companies migrated: {self.stats['insurance_companies']['mysql_count']}/{mongo_count}")

    # 4. Policy Types
    def migrate_policy_types(self):
        logger.info("Migrating policy types...")
        if self.mongo_db is None or 'policy_types' not in self.mongo_db.list_collection_names():
            return

        collection = self.mongo_db.policy_types
        mongo_count = collection.count_documents({})
        self.stats['policy_types']['mongo_count'] = mongo_count

        for doc in collection.find():
            try:
                mongo_id = str(doc.get('_id'))
                created_at = _to_utc_datetime(doc.get('created_at')) or datetime.now(timezone.utc)

                pt = PolicyType(
                    slug=doc.get('slug', f"type-{mongo_id}"),
                    name=doc.get('name', 'Policy Type'),
                    category=doc.get('category', 'motor'),
                    description=doc.get('description'),
                    is_active=bool(doc.get('is_active', True)),
                    created_at=created_at
                )
                if mongo_id.isdigit():
                    pt.id = int(mongo_id)

                db.session.add(pt)
                db.session.flush()
                self.id_maps['policy_types'][mongo_id] = pt.id
                self.stats['policy_types']['mysql_count'] += 1
            except Exception as e:
                logger.error(f"Error migrating policy type {doc.get('_id')}: {e}")
                self.stats['policy_types']['errors'] += 1
                db.session.rollback()

        db.session.commit()
        logger.info(f"Policy types migrated: {self.stats['policy_types']['mysql_count']}/{mongo_count}")

    # 5. Policies
    def migrate_policies(self):
        logger.info("Migrating policies...")
        if self.mongo_db is None or 'policies' not in self.mongo_db.list_collection_names():
            return

        collection = self.mongo_db.policies
        mongo_count = collection.count_documents({})
        self.stats['policies']['mongo_count'] = mongo_count

        for doc in collection.find():
            try:
                mongo_id = str(doc.get('_id'))
                client_id = self.id_maps['users'].get(str(doc.get('client_id')))
                if not client_id:
                    continue

                vehicle_id = self.id_maps['vehicles'].get(str(doc.get('vehicle_id')))
                company_id = self.id_maps['insurance_companies'].get(str(doc.get('insurance_company_id')))
                type_id = self.id_maps['policy_types'].get(str(doc.get('policy_type_id')))
                created_by = self.id_maps['users'].get(str(doc.get('created_by')))
                assigned_worker_id = self.id_maps['users'].get(str(doc.get('assigned_worker_id')))

                # Ensure non-null required foreign keys have safe defaults if missing
                if not company_id:
                    first_comp = db.session.execute(db.select(InsuranceCompany.id)).scalar()
                    company_id = first_comp or 1
                if not type_id:
                    first_type = db.session.execute(db.select(PolicyType.id)).scalar()
                    type_id = first_type or 1

                created_at = _to_utc_datetime(doc.get('created_at')) or datetime.now(timezone.utc)
                updated_at = _to_utc_datetime(doc.get('updated_at')) or datetime.now(timezone.utc)
                start_date = _to_utc_datetime(doc.get('start_date'))
                end_date = _to_utc_datetime(doc.get('end_date')) or _to_utc_datetime(doc.get('expiry_date'))

                policy = Policy(
                    policy_number=doc.get('policy_number', f"POL-{mongo_id}"),
                    client_id=client_id,
                    vehicle_id=vehicle_id,
                    insurance_company_id=company_id,
                    policy_type_id=type_id,
                    status=doc.get('status', 'draft'),
                    sum_insured=float(doc.get('sum_insured', 0.00)),
                    premium=float(doc.get('premium', 0.00)),
                    seating_capacity=doc.get('seating_capacity') or doc.get('pax'),
                    start_date=start_date,
                    end_date=end_date,
                    created_by=created_by,
                    assigned_worker_id=assigned_worker_id,
                    created_at=created_at,
                    updated_at=updated_at
                )
                if mongo_id.isdigit():
                    policy.id = int(mongo_id)

                db.session.add(policy)
                db.session.flush()
                self.id_maps['policies'][mongo_id] = policy.id
                self.stats['policies']['mysql_count'] += 1
            except Exception as e:
                logger.error(f"Error migrating policy {doc.get('_id')}: {e}")
                self.stats['policies']['errors'] += 1
                db.session.rollback()

        db.session.commit()
        logger.info(f"Policies migrated: {self.stats['policies']['mysql_count']}/{mongo_count}")

    # 6. Policy Versions
    def migrate_policy_versions(self):
        logger.info("Migrating policy versions...")
        if self.mongo_db is None or 'policy_versions' not in self.mongo_db.list_collection_names():
            return

        collection = self.mongo_db.policy_versions
        mongo_count = collection.count_documents({})
        self.stats['policy_versions']['mongo_count'] = mongo_count

        for doc in collection.find():
            try:
                policy_id = self.id_maps['policies'].get(str(doc.get('policy_id')))
                if not policy_id:
                    continue

                snapshot = doc.get('snapshot_json') or doc.get('snapshot') or {}
                if isinstance(snapshot, dict):
                    snapshot_json = json.dumps(snapshot, default=str)
                else:
                    snapshot_json = str(snapshot)

                created_at = _to_utc_datetime(doc.get('created_at')) or datetime.now(timezone.utc)

                pv = PolicyVersion(
                    policy_id=policy_id,
                    version_number=int(doc.get('version_number', 1)),
                    snapshot_json=snapshot_json,
                    created_at=created_at
                )
                db.session.add(pv)
                db.session.flush()
                self.stats['policy_versions']['mysql_count'] += 1
            except Exception as e:
                logger.error(f"Error migrating policy version: {e}")
                self.stats['policy_versions']['errors'] += 1
                db.session.rollback()

        db.session.commit()

    # 7. Claims
    def migrate_claims(self):
        logger.info("Migrating claims...")
        if self.mongo_db is None or 'claims' not in self.mongo_db.list_collection_names():
            return

        collection = self.mongo_db.claims
        mongo_count = collection.count_documents({})
        self.stats['claims']['mongo_count'] = mongo_count

        for doc in collection.find():
            try:
                mongo_id = str(doc.get('_id'))
                policy_id = self.id_maps['policies'].get(str(doc.get('policy_id')))
                client_id = self.id_maps['users'].get(str(doc.get('client_id')))
                if not policy_id or not client_id:
                    continue

                incident_date = _to_utc_datetime(doc.get('incident_date')) or datetime.now(timezone.utc)
                report_date = _to_utc_datetime(doc.get('report_date')) or datetime.now(timezone.utc)
                created_at = _to_utc_datetime(doc.get('created_at')) or datetime.now(timezone.utc)
                updated_at = _to_utc_datetime(doc.get('updated_at')) or datetime.now(timezone.utc)

                claim = Claim(
                    claim_number=doc.get('claim_number', f"CLM-{mongo_id}"),
                    policy_id=policy_id,
                    client_id=client_id,
                    incident_date=incident_date,
                    report_date=report_date,
                    status=doc.get('status', 'reported'),
                    description=doc.get('description'),
                    estimated_amount=float(doc.get('estimated_amount', 0.00)),
                    settled_amount=float(doc.get('settled_amount')) if doc.get('settled_amount') is not None else None,
                    fraud_status=doc.get('fraud_status', 'clean'),
                    created_at=created_at,
                    updated_at=updated_at
                )
                if mongo_id.isdigit():
                    claim.id = int(mongo_id)

                db.session.add(claim)
                db.session.flush()
                self.id_maps['claims'][mongo_id] = claim.id
                self.stats['claims']['mysql_count'] += 1
            except Exception as e:
                logger.error(f"Error migrating claim {doc.get('_id')}: {e}")
                self.stats['claims']['errors'] += 1
                db.session.rollback()

        db.session.commit()

    # 8. Payments
    def migrate_payments(self):
        logger.info("Migrating payments...")
        if self.mongo_db is None or 'payments' not in self.mongo_db.list_collection_names():
            return

        collection = self.mongo_db.payments
        mongo_count = collection.count_documents({})
        self.stats['payments']['mongo_count'] = mongo_count

        for doc in collection.find():
            try:
                mongo_id = str(doc.get('_id'))
                policy_id = self.id_maps['policies'].get(str(doc.get('policy_id')))
                client_id = self.id_maps['users'].get(str(doc.get('client_id')))
                if not policy_id or not client_id:
                    continue

                payment_date = _to_utc_datetime(doc.get('payment_date')) or datetime.now(timezone.utc)
                created_at = _to_utc_datetime(doc.get('created_at')) or datetime.now(timezone.utc)
                updated_at = _to_utc_datetime(doc.get('updated_at')) or datetime.now(timezone.utc)

                payment = Payment(
                    receipt_number=doc.get('receipt_number', f"REC-{mongo_id}"),
                    policy_id=policy_id,
                    client_id=client_id,
                    amount=float(doc.get('amount', 0.00)),
                    payment_date=payment_date,
                    status=doc.get('status', 'pending'),
                    payment_method=doc.get('payment_method', 'mpesa'),
                    mpesa_trans_id=doc.get('mpesa_trans_id'),
                    notes=doc.get('notes'),
                    created_at=created_at,
                    updated_at=updated_at
                )
                if mongo_id.isdigit():
                    payment.id = int(mongo_id)

                db.session.add(payment)
                db.session.flush()
                self.id_maps['payments'][mongo_id] = payment.id
                self.stats['payments']['mysql_count'] += 1
            except Exception as e:
                logger.error(f"Error migrating payment {doc.get('_id')}: {e}")
                self.stats['payments']['errors'] += 1
                db.session.rollback()

        db.session.commit()

    # 9. M-Pesa Transactions
    def migrate_mpesa_transactions(self):
        logger.info("Migrating mpesa transactions...")
        if self.mongo_db is None or 'mpesa_transactions' not in self.mongo_db.list_collection_names():
            return

        collection = self.mongo_db.mpesa_transactions
        mongo_count = collection.count_documents({})
        self.stats['mpesa_transactions']['mongo_count'] = mongo_count

        for doc in collection.find():
            try:
                mongo_id = str(doc.get('_id'))
                trans_time = _to_utc_datetime(doc.get('trans_time')) or datetime.now(timezone.utc)
                created_at = _to_utc_datetime(doc.get('created_at')) or datetime.now(timezone.utc)

                raw_payload = doc.get('raw_payload')
                if isinstance(raw_payload, dict):
                    raw_payload = json.dumps(raw_payload, default=str)

                tx = MpesaTransaction(
                    trans_id=doc.get('trans_id', f"TX-{mongo_id}"),
                    trans_time=trans_time,
                    trans_amount=float(doc.get('trans_amount', 0.00)),
                    business_short_code=doc.get('business_short_code', '000000'),
                    bill_ref_number=doc.get('bill_ref_number'),
                    invoice_number=doc.get('invoice_number'),
                    org_account_balance=float(doc.get('org_account_balance', 0.00)) if doc.get('org_account_balance') is not None else None,
                    third_party_trans_id=doc.get('third_party_trans_id'),
                    msisdn=doc.get('msisdn', ''),
                    first_name=doc.get('first_name'),
                    middle_name=doc.get('middle_name'),
                    last_name=doc.get('last_name'),
                    status=doc.get('status', 'pending'),
                    raw_payload=raw_payload,
                    created_at=created_at
                )
                db.session.add(tx)
                db.session.flush()
                self.stats['mpesa_transactions']['mysql_count'] += 1
            except Exception as e:
                logger.error(f"Error migrating mpesa tx {doc.get('_id')}: {e}")
                self.stats['mpesa_transactions']['errors'] += 1
                db.session.rollback()

        db.session.commit()

    # 10. Reminders
    def migrate_reminders(self):
        logger.info("Migrating reminders...")
        if self.mongo_db is None or 'reminders' not in self.mongo_db.list_collection_names():
            return

        collection = self.mongo_db.reminders
        mongo_count = collection.count_documents({})
        self.stats['reminders']['mongo_count'] = mongo_count

        for doc in collection.find():
            try:
                policy_id = self.id_maps['policies'].get(str(doc.get('policy_id')))
                client_id = self.id_maps['users'].get(str(doc.get('client_id')))
                if not policy_id or not client_id:
                    continue

                due_date = _to_utc_datetime(doc.get('due_date')) or datetime.now(timezone.utc)
                sent_at = _to_utc_datetime(doc.get('sent_at'))
                created_at = _to_utc_datetime(doc.get('created_at')) or datetime.now(timezone.utc)

                reminder = Reminder(
                    policy_id=policy_id,
                    client_id=client_id,
                    reminder_type=doc.get('reminder_type', 'expiry'),
                    due_date=due_date,
                    status=doc.get('status', 'pending'),
                    sent_at=sent_at,
                    created_at=created_at
                )
                db.session.add(reminder)
                db.session.flush()
                self.stats['reminders']['mysql_count'] += 1
            except Exception as e:
                logger.error(f"Error migrating reminder: {e}")
                self.stats['reminders']['errors'] += 1
                db.session.rollback()

        db.session.commit()

    # 11. SMS Outbox
    def migrate_sms_outbox(self):
        logger.info("Migrating sms outbox...")
        if self.mongo_db is None or 'sms_outbox' not in self.mongo_db.list_collection_names():
            return

        collection = self.mongo_db.sms_outbox
        mongo_count = collection.count_documents({})
        self.stats['sms_outbox']['mongo_count'] = mongo_count

        for doc in collection.find():
            try:
                mongo_id = str(doc.get('_id'))
                policy_id = self.id_maps['policies'].get(str(doc.get('policy_id')))
                client_id = self.id_maps['users'].get(str(doc.get('client_id')))

                meta = doc.get('meta')
                if isinstance(meta, dict):
                    meta = json.dumps(meta, default=str)

                sms = SmsOutbox(
                    phone_number=doc.get('phone_number') or doc.get('destination', ''),
                    destination=doc.get('destination') or doc.get('phone_number', ''),
                    message=doc.get('message', ''),
                    kind=doc.get('kind', 'notification'),
                    priority=int(doc.get('priority', 1)),
                    status=doc.get('status', 'queued'),
                    idempotency_key=doc.get('idempotency_key', f"sms-{mongo_id}"),
                    template_key=doc.get('template_key'),
                    template_version=int(doc.get('template_version', 1)),
                    policy_id=policy_id,
                    client_id=client_id,
                    manual=bool(doc.get('manual', False)),
                    meta=meta,
                    attempts=int(doc.get('attempts', 0)),
                    max_attempts=int(doc.get('max_attempts', 5)),
                    simulated=bool(doc.get('simulated', False)),
                    segments=int(doc.get('segments', 1)),
                    cost_kes=float(doc.get('cost_kes', 0.00)),
                    provider_ref=doc.get('provider_ref'),
                    provider_status=doc.get('provider_status'),
                    last_error=doc.get('last_error'),
                    next_attempt_at=_to_utc_datetime(doc.get('next_attempt_at')),
                    worker=doc.get('worker'),
                    sent_at=_to_utc_datetime(doc.get('sent_at')),
                    delivered_at=_to_utc_datetime(doc.get('delivered_at')),
                    dead_at=_to_utc_datetime(doc.get('dead_at')),
                    dead_reason=doc.get('dead_reason'),
                    created_at=_to_utc_datetime(doc.get('created_at')) or datetime.now(timezone.utc),
                    updated_at=_to_utc_datetime(doc.get('updated_at')) or datetime.now(timezone.utc)
                )
                db.session.add(sms)
                db.session.flush()
                self.stats['sms_outbox']['mysql_count'] += 1
            except Exception as e:
                logger.error(f"Error migrating sms outbox: {e}")
                self.stats['sms_outbox']['errors'] += 1
                db.session.rollback()

        db.session.commit()

    # 12. SMS Suppressions
    def migrate_sms_suppressions(self):
        logger.info("Migrating sms suppressions...")
        if self.mongo_db is None or 'sms_suppressions' not in self.mongo_db.list_collection_names():
            return

        collection = self.mongo_db.sms_suppressions
        mongo_count = collection.count_documents({})
        self.stats['sms_suppressions']['mongo_count'] = mongo_count

        for doc in collection.find():
            try:
                suppression = SmsSuppression(
                    phone_number=doc.get('phone_number', ''),
                    reason=doc.get('reason'),
                    created_at=_to_utc_datetime(doc.get('created_at')) or datetime.now(timezone.utc)
                )
                db.session.add(suppression)
                db.session.flush()
                self.stats['sms_suppressions']['mysql_count'] += 1
            except Exception as e:
                logger.error(f"Error migrating sms suppression: {e}")
                self.stats['sms_suppressions']['errors'] += 1
                db.session.rollback()

        db.session.commit()

    # 13. Notifications
    def migrate_notifications(self):
        logger.info("Migrating notifications...")
        if self.mongo_db is None or 'notifications' not in self.mongo_db.list_collection_names():
            return

        collection = self.mongo_db.notifications
        mongo_count = collection.count_documents({})
        self.stats['notifications']['mongo_count'] = mongo_count

        for doc in collection.find():
            try:
                user_id = self.id_maps['users'].get(str(doc.get('user_id')))
                if not user_id:
                    first_user = db.session.execute(db.select(User.id)).scalar()
                    user_id = first_user or 1

                policy_id = self.id_maps['policies'].get(str(doc.get('policy_id')))

                read_by = doc.get('read_by')
                if isinstance(read_by, list):
                    read_by_str = ','.join(str(x) for x in read_by)
                else:
                    read_by_str = str(read_by or '')

                notification = Notification(
                    user_id=user_id,
                    audience=doc.get('audience', 'staff'),
                    category=doc.get('category', 'system'),
                    severity=doc.get('severity', 'info'),
                    title=doc.get('title', 'System Notification'),
                    body=doc.get('body') or doc.get('message', ''),
                    is_read=bool(doc.get('is_read', False)),
                    read_by=read_by_str,
                    policy_id=policy_id,
                    policy_number=doc.get('policy_number'),
                    created_at=_to_utc_datetime(doc.get('created_at')) or datetime.now(timezone.utc),
                    updated_at=_to_utc_datetime(doc.get('updated_at')) or datetime.now(timezone.utc)
                )
                db.session.add(notification)
                db.session.flush()
                self.stats['notifications']['mysql_count'] += 1
            except Exception as e:
                logger.error(f"Error migrating notification: {e}")
                self.stats['notifications']['errors'] += 1
                db.session.rollback()

        db.session.commit()

    # 14. Audit Logs
    def migrate_audit_logs(self):
        logger.info("Migrating audit logs...")
        if self.mongo_db is None or 'audit_logs' not in self.mongo_db.list_collection_names():
            return

        collection = self.mongo_db.audit_logs
        mongo_count = collection.count_documents({})
        self.stats['audit_logs']['mongo_count'] = mongo_count

        for doc in collection.find():
            try:
                performed_by = self.id_maps['users'].get(str(doc.get('performed_by')))
                details = doc.get('details')
                if isinstance(details, dict):
                    details = json.dumps(details, default=str)

                log = AuditLog(
                    performed_by=performed_by,
                    action=doc.get('action', 'unknown'),
                    entity_type=doc.get('entity_type', 'system'),
                    entity_id=str(doc.get('entity_id') or ''),
                    details=details,
                    ip_address=doc.get('ip_address'),
                    created_at=_to_utc_datetime(doc.get('created_at')) or datetime.now(timezone.utc)
                )
                db.session.add(log)
                db.session.flush()
                self.stats['audit_logs']['mysql_count'] += 1
            except Exception as e:
                logger.error(f"Error migrating audit log: {e}")
                self.stats['audit_logs']['errors'] += 1
                db.session.rollback()

        db.session.commit()

    # 15. App Settings
    def migrate_app_settings(self):
        logger.info("Migrating app settings...")
        if self.mongo_db is None or 'app_settings' not in self.mongo_db.list_collection_names():
            return

        collection = self.mongo_db.app_settings
        mongo_count = collection.count_documents({})
        self.stats['app_settings']['mongo_count'] = mongo_count

        for doc in collection.find():
            try:
                val = doc.get('value')
                if isinstance(val, (dict, list)):
                    val = json.dumps(val, default=str)

                setting = AppSetting(
                    key=doc.get('key', ''),
                    value=str(val) if val is not None else None,
                    created_at=_to_utc_datetime(doc.get('created_at')) or datetime.now(timezone.utc),
                    updated_at=_to_utc_datetime(doc.get('updated_at')) or datetime.now(timezone.utc)
                )
                db.session.add(setting)
                db.session.flush()
                self.stats['app_settings']['mysql_count'] += 1
            except Exception as e:
                logger.error(f"Error migrating app setting: {e}")
                self.stats['app_settings']['errors'] += 1
                db.session.rollback()

        db.session.commit()

    # 16. Counters
    def migrate_counters(self):
        logger.info("Migrating counters...")
        if self.mongo_db is None or 'counters' not in self.mongo_db.list_collection_names():
            return

        collection = self.mongo_db.counters
        mongo_count = collection.count_documents({})
        self.stats['counters']['mongo_count'] = mongo_count

        for doc in collection.find():
            try:
                counter = Counter(
                    name=doc.get('name') or doc.get('_id', ''),
                    current_val=int(doc.get('current_val') or doc.get('seq', 0)),
                    updated_at=_to_utc_datetime(doc.get('updated_at')) or datetime.now(timezone.utc)
                )
                db.session.add(counter)
                db.session.flush()
                self.stats['counters']['mysql_count'] += 1
            except Exception as e:
                logger.error(f"Error migrating counter: {e}")
                self.stats['counters']['errors'] += 1
                db.session.rollback()

        db.session.commit()

    # 17. SMS Templates
    def migrate_smstemplates(self):
        logger.info("Migrating SMS templates...")
        if self.mongo_db is None or 'sms_templates' not in self.mongo_db.list_collection_names():
            return

        collection = self.mongo_db.sms_templates
        mongo_count = collection.count_documents({})
        self.stats['smstemplates']['mongo_count'] = mongo_count

        for doc in collection.find():
            try:
                template = Smstemplate(
                    key=doc.get('key', ''),
                    body=doc.get('body', ''),
                    version=int(doc.get('version', 1)),
                    created_at=_to_utc_datetime(doc.get('created_at')) or datetime.now(timezone.utc),
                    updated_at=_to_utc_datetime(doc.get('updated_at')) or datetime.now(timezone.utc)
                )
                db.session.add(template)
                db.session.flush()
                self.stats['smstemplates']['mysql_count'] += 1
            except Exception as e:
                logger.error(f"Error migrating sms template: {e}")
                self.stats['smstemplates']['errors'] += 1
                db.session.rollback()

        db.session.commit()

    def run_migration(self):
        logger.info("Starting complete MongoDB -> MySQL migration...")
        self.clear_mysql_tables()

        migration_methods = [
            self.migrate_users,
            self.migrate_vehicles,
            self.migrate_insurance_companies,
            self.migrate_policy_types,
            self.migrate_policies,
            self.migrate_policy_versions,
            self.migrate_claims,
            self.migrate_payments,
            self.migrate_mpesa_transactions,
            self.migrate_reminders,
            self.migrate_sms_outbox,
            self.migrate_sms_suppressions,
            self.migrate_notifications,
            self.migrate_audit_logs,
            self.migrate_app_settings,
            self.migrate_counters,
            self.migrate_smstemplates
        ]

        for method in migration_methods:
            try:
                method()
            except Exception as e:
                logger.error(f"Fatal error in {method.__name__}: {e}")
                return False

        self.print_migration_summary()
        return True

    def print_migration_summary(self):
        logger.info("=" * 70)
        logger.info(f"{'COLLECTION / TABLE':25} | {'MONGO':8} | {'MYSQL':8} | {'ERRORS':6}")
        logger.info("=" * 70)

        total_mongo = 0
        total_mysql = 0
        total_errors = 0

        for table_name, stats in self.stats.items():
            mongo_count = stats['mongo_count']
            mysql_count = stats['mysql_count']
            errors = stats['errors']

            total_mongo += mongo_count
            total_mysql += mysql_count
            total_errors += errors

            logger.info(f"{table_name:25} | {mongo_count:8} | {mysql_count:8} | {errors:6}")

        logger.info("-" * 70)
        logger.info(f"{'TOTAL':25} | {total_mongo:8} | {total_mysql:8} | {total_errors:6}")
        logger.info("=" * 70)


def main():
    print("POLICYGUARD MongoDB to MySQL Data Migration Tool")
    print("=" * 55)
    migrator = MongoToMySQLMigrator()
    success = migrator.run_migration()
    if success:
        print("\nMigration finished successfully.")
    else:
        print("\nMigration failed.")
        sys.exit(1)


if __name__ == "__main__":
    main()
