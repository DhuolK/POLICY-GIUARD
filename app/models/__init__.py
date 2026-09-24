from datetime import datetime, timezone
from flask_login import UserMixin
from sqlalchemy import (
    Column, Integer, String, Boolean, DateTime, Numeric, Text, ForeignKey,
    Index, UniqueConstraint
)
from sqlalchemy.orm import relationship
from app.extensions import db


class ModelMixin:
    def to_dict(self):
        """Convert model instance to a dictionary."""
        return {c.name: getattr(self, c.name) for c in self.__table__.columns}

    def __getitem__(self, key):
        """Allow dict-like access to model attributes."""
        return getattr(self, key)

    def __setitem__(self, key, value):
        """Allow dict-like assignment to model attributes."""
        setattr(self, key, value)

    def get(self, key, default=None):
        """Dict-like get method."""
        return getattr(self, key, default)


class User(UserMixin, db.Model, ModelMixin):
    __tablename__ = 'users'

    id = Column(Integer, primary_key=True, autoincrement=True)
    email = Column(String(255), unique=True, nullable=False, index=True)
    password_hash = Column(String(255), nullable=True)
    full_name = Column(String(255), nullable=False)
    phone = Column(String(50), nullable=True, index=True)
    role = Column(String(50), nullable=False, default='customer', index=True)
    created_by = Column(Integer, ForeignKey('users.id', ondelete='SET NULL'), nullable=True)
    assigned_worker_id = Column(Integer, ForeignKey('users.id', ondelete='SET NULL'), nullable=True)
    failed_login_attempts = Column(Integer, nullable=False, default=0)
    locked_until = Column(DateTime, nullable=True)
    disabled = Column(Boolean, nullable=False, default=False)
    status = Column(String(50), nullable=False, default='active', index=True)

    # Customer specific attributes
    client_id_number = Column(String(50), nullable=True, index=True)  # National ID / Passport
    kra_pin = Column(String(50), nullable=True, index=True)

    created_at = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

    # Relationships
    vehicles = relationship('Vehicle', back_populates='owner', foreign_keys='Vehicle.owner_id', cascade='all, delete-orphan')
    policies = relationship('Policy', back_populates='client', foreign_keys='Policy.client_id')
    created_policies = relationship('Policy', back_populates='creator', foreign_keys='Policy.created_by')
    assigned_policies = relationship('Policy', back_populates='assigned_worker', foreign_keys='Policy.assigned_worker_id')
    claims = relationship('Claim', back_populates='client', foreign_keys='Claim.client_id')
    payments = relationship('Payment', back_populates='client', foreign_keys='Payment.client_id')
    notifications = relationship('Notification', back_populates='user', foreign_keys='Notification.user_id', cascade='all, delete-orphan')
    audit_logs = relationship('AuditLog', back_populates='user', foreign_keys='AuditLog.performed_by')

    def __init__(self, *args, **kwargs):
        # Support dict init for migration & compatibility
        if args and isinstance(args[0], dict):
            user_data = args[0]
            super().__init__()
            if '_id' in user_data:
                try:
                    self.id = int(str(user_data['_id']))
                except (ValueError, TypeError):
                    pass
            self.email = user_data.get('email')
            self.password_hash = user_data.get('password_hash')
            self.full_name = user_data.get('full_name') or user_data.get('name') or ''
            self.phone = user_data.get('phone')
            self.role = user_data.get('role', 'customer')
            self.disabled = bool(user_data.get('disabled', False))
            self.client_id_number = user_data.get('client_id') or user_data.get('client_id_number') or user_data.get('client_uid')
            self.kra_pin = user_data.get('kra_pin')
            self.failed_login_attempts = user_data.get('failed_login_attempts', 0)
            self.locked_until = user_data.get('locked_until')
            self.created_at = user_data.get('created_at') or datetime.utcnow()
            self.updated_at = user_data.get('updated_at') or datetime.utcnow()
        else:
            if 'client_id' in kwargs:
                kwargs['client_id_number'] = kwargs.pop('client_id')
            if 'client_uid' in kwargs:
                kwargs['client_id_number'] = kwargs.pop('client_uid')
            if 'full_name' not in kwargs and 'name' in kwargs:
                kwargs['full_name'] = kwargs.pop('name')
            if not kwargs.get('full_name'):
                kwargs['full_name'] = kwargs.get('email') or 'User'
            super().__init__(*args, **kwargs)

    @property
    def is_active(self):
        return not self.disabled

    def get_id(self):
        return str(self.id)

    @property
    def name(self):
        return self.full_name

    @name.setter
    def name(self, val):
        self.full_name = val

    @property
    def _id(self):
        return str(self.id)


class Vehicle(db.Model, ModelMixin):
    __tablename__ = 'vehicles'

    id = Column(Integer, primary_key=True, autoincrement=True)
    owner_id = Column(Integer, ForeignKey('users.id', ondelete='CASCADE'), nullable=False, index=True)
    registration_number = Column(String(50), nullable=False, unique=True, index=True)
    make = Column(String(100), nullable=False)
    model = Column(String(100), nullable=False)
    year = Column(Integer, nullable=True)
    category = Column(String(50), nullable=False, default='motor')
    seating_capacity = Column(Integer, nullable=False, default=5)  # PAX
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    def __init__(self, *args, **kwargs):
        if 'vehicle_type' in kwargs:
            kwargs['category'] = kwargs.pop('vehicle_type')
        if not kwargs.get('make'):
            kwargs['make'] = 'Generic'
        if not kwargs.get('model'):
            kwargs['model'] = 'Generic'
        super().__init__(*args, **kwargs)

    @property
    def vehicle_type(self):
        return self.category

    @vehicle_type.setter
    def vehicle_type(self, val):
        self.category = val

    # Relationships
    owner = relationship('User', back_populates='vehicles', foreign_keys=[owner_id])
    policies = relationship('Policy', back_populates='vehicle', foreign_keys='Policy.vehicle_id')


class InsuranceCompany(db.Model, ModelMixin):
    __tablename__ = 'insurance_companies'

    id = Column(Integer, primary_key=True, autoincrement=True)
    code = Column(String(50), unique=True, nullable=False, index=True)
    name = Column(String(255), nullable=False, index=True)
    short_name = Column(String(100), nullable=True)
    ira_license_number = Column(String(100), unique=True, nullable=True)
    contact_email = Column(String(255), nullable=True)
    contact_phone = Column(String(50), nullable=True)
    contact_person = Column(String(255), nullable=True)
    commission_rate = Column(Numeric(5, 2), nullable=False, default=0.00)
    is_active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

    # Relationships
    policies = relationship('Policy', back_populates='insurance_company', foreign_keys='Policy.insurance_company_id')


class PolicyType(db.Model, ModelMixin):
    __tablename__ = 'policy_types'

    id = Column(Integer, primary_key=True, autoincrement=True)
    slug = Column(String(100), unique=True, nullable=False, index=True)
    name = Column(String(255), nullable=False)
    category = Column(String(50), nullable=False, default='motor')  # motor, non_motor
    description = Column(Text, nullable=True)
    default_premium = Column(Numeric(14, 2), nullable=False, default=0.00)
    is_active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    def __init__(self, *args, **kwargs):
        if 'name' in kwargs and 'slug' not in kwargs:
            kwargs['slug'] = kwargs['name'].lower().replace(' ', '_').replace('-', '_')
        super().__init__(*args, **kwargs)

    # Relationships
    policies = relationship('Policy', back_populates='policy_type', foreign_keys='Policy.policy_type_id')


class Policy(db.Model, ModelMixin):
    __tablename__ = 'policies'

    id = Column(Integer, primary_key=True, autoincrement=True)
    policy_number = Column(String(100), unique=True, nullable=False, index=True)
    client_id = Column(Integer, ForeignKey('users.id', ondelete='RESTRICT'), nullable=False, index=True)
    vehicle_id = Column(Integer, ForeignKey('vehicles.id', ondelete='SET NULL'), nullable=True, index=True)
    insurance_company_id = Column(Integer, ForeignKey('insurance_companies.id', ondelete='RESTRICT'), nullable=True, index=True)
    policy_type_id = Column(Integer, ForeignKey('policy_types.id', ondelete='RESTRICT'), nullable=True, index=True)
    status = Column(String(50), nullable=False, default='draft', index=True)
    sum_insured = Column(Numeric(14, 2), nullable=False, default=0.00)
    premium = Column(Numeric(14, 2), nullable=False, default=0.00)
    seating_capacity = Column(Integer, nullable=True)  # PAX for motor policies
    start_date = Column(DateTime, nullable=True)
    end_date = Column(DateTime, nullable=True, index=True)
    created_by = Column(Integer, ForeignKey('users.id', ondelete='SET NULL'), nullable=True)
    assigned_worker_id = Column(Integer, ForeignKey('users.id', ondelete='SET NULL'), nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow, index=True)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    def __init__(self, *args, **kwargs):
        if args and isinstance(args[0], dict):
            p_data = args[0]
            super().__init__()
            if '_id' in p_data:
                try:
                    self.id = int(str(p_data['_id']))
                except (ValueError, TypeError):
                    pass
            self.policy_number = p_data.get('policy_number')
            self.client_id = p_data.get('client_id')
            self.vehicle_id = p_data.get('vehicle_id')
            self.insurance_company_id = p_data.get('insurance_company_id')
            self.policy_type_id = p_data.get('policy_type_id')
            self.status = p_data.get('status', 'draft')
            self.sum_insured = p_data.get('sum_insured', 0.00)
            self.premium = p_data.get('premium') or p_data.get('premium_amount', 0.00)
            self.seating_capacity = p_data.get('seating_capacity')
            self.start_date = p_data.get('start_date') or p_data.get('effective_date')
            self.end_date = p_data.get('end_date') or p_data.get('expiry_date')
            self.created_by = p_data.get('created_by')
            self.assigned_worker_id = p_data.get('assigned_worker_id')
            self.created_at = p_data.get('created_at') or datetime.utcnow()
            self.updated_at = p_data.get('updated_at') or datetime.utcnow()
        else:
            # Map alias kwargs
            if 'effective_date' in kwargs and 'start_date' not in kwargs:
                eff = kwargs.pop('effective_date')
                if isinstance(eff, str):
                    try:
                        kwargs['start_date'] = datetime.strptime(eff, '%Y-%m-%d')
                    except Exception:
                        kwargs['start_date'] = None
                else:
                    kwargs['start_date'] = eff
            if 'expiry_date' in kwargs and 'end_date' not in kwargs:
                exp = kwargs.pop('expiry_date')
                if isinstance(exp, str):
                    try:
                        kwargs['end_date'] = datetime.strptime(exp, '%Y-%m-%d')
                    except Exception:
                        kwargs['end_date'] = None
                else:
                    kwargs['end_date'] = exp
            if 'premium_amount' in kwargs and 'premium' not in kwargs:
                kwargs['premium'] = kwargs.pop('premium_amount')
            if 'policy_type' in kwargs:
                pt_val = kwargs.pop('policy_type')
                if isinstance(pt_val, int):
                    kwargs['policy_type_id'] = pt_val
                elif not hasattr(pt_val, '_sa_instance_state') and not isinstance(pt_val, db.Model):
                    # string policy_type, ignore or map if needed
                    pass
                else:
                    kwargs['policy_type'] = pt_val
            super().__init__(*args, **kwargs)

    @property
    def effective_date(self):
        return self.start_date.strftime('%Y-%m-%d') if self.start_date else None

    @effective_date.setter
    def effective_date(self, val):
        if isinstance(val, str):
            try:
                self.start_date = datetime.strptime(val, '%Y-%m-%d')
            except Exception:
                self.start_date = None
        else:
            self.start_date = val

    @property
    def expiry_date(self):
        return self.end_date.strftime('%Y-%m-%d') if self.end_date else None

    @expiry_date.setter
    def expiry_date(self, val):
        if isinstance(val, str):
            try:
                self.end_date = datetime.strptime(val, '%Y-%m-%d')
            except Exception:
                self.end_date = None
        else:
            self.end_date = val

    @property
    def premium_amount(self):
        return float(self.premium) if self.premium is not None else 0.0

    @premium_amount.setter
    def premium_amount(self, val):
        self.premium = val

    # Relationships
    client = relationship('User', back_populates='policies', foreign_keys=[client_id])
    vehicle = relationship('Vehicle', back_populates='policies', foreign_keys=[vehicle_id])
    insurance_company = relationship('InsuranceCompany', back_populates='policies', foreign_keys=[insurance_company_id])
    policy_type = relationship('PolicyType', back_populates='policies', foreign_keys=[policy_type_id])
    creator = relationship('User', back_populates='created_policies', foreign_keys=[created_by])
    assigned_worker = relationship('User', back_populates='assigned_policies', foreign_keys=[assigned_worker_id])

    versions = relationship('PolicyVersion', back_populates='policy', foreign_keys='PolicyVersion.policy_id', cascade='all, delete-orphan')
    claims = relationship('Claim', back_populates='policy', foreign_keys='Claim.policy_id')
    payments = relationship('Payment', back_populates='policy', foreign_keys='Payment.policy_id')
    reminders = relationship('Reminder', back_populates='policy', foreign_keys='Reminder.policy_id', cascade='all, delete-orphan')


class PolicyVersion(db.Model, ModelMixin):
    __tablename__ = 'policy_versions'

    id = Column(Integer, primary_key=True, autoincrement=True)
    policy_id = Column(Integer, ForeignKey('policies.id', ondelete='CASCADE'), nullable=False, index=True)
    version_number = Column(Integer, nullable=False)
    snapshot_json = Column(Text, nullable=False)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint('policy_id', 'version_number', name='uq_policy_version'),
    )

    policy = relationship('Policy', back_populates='versions', foreign_keys=[policy_id])


class Claim(db.Model, ModelMixin):
    __tablename__ = 'claims'

    id = Column(Integer, primary_key=True, autoincrement=True)
    claim_number = Column(String(100), unique=True, nullable=False, index=True)
    policy_id = Column(Integer, ForeignKey('policies.id', ondelete='RESTRICT'), nullable=False, index=True)
    client_id = Column(Integer, ForeignKey('users.id', ondelete='RESTRICT'), nullable=False, index=True)
    incident_date = Column(DateTime, nullable=True, default=lambda: datetime.now(timezone.utc))
    report_date = Column(DateTime, nullable=False, default=datetime.utcnow)
    status = Column(String(50), nullable=False, default='reported', index=True)
    description = Column(Text, nullable=True)
    estimated_amount = Column(Numeric(14, 2), nullable=False, default=0.00)
    settled_amount = Column(Numeric(14, 2), nullable=True)
    fraud_status = Column(String(50), nullable=False, default='clean')  # clean, flagged, under_review
    created_by = Column(Integer, ForeignKey('users.id', ondelete='SET NULL'), nullable=True)
    assigned_worker_id = Column(Integer, ForeignKey('users.id', ondelete='SET NULL'), nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow, index=True)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    policy = relationship('Policy', back_populates='claims', foreign_keys=[policy_id])
    client = relationship('User', back_populates='claims', foreign_keys=[client_id])


class Payment(db.Model, ModelMixin):
    __tablename__ = 'payments'

    id = Column(Integer, primary_key=True, autoincrement=True)
    receipt_number = Column(String(100), unique=True, nullable=False, index=True)
    policy_id = Column(Integer, ForeignKey('policies.id', ondelete='RESTRICT'), nullable=True, index=True)
    client_id = Column(Integer, ForeignKey('users.id', ondelete='RESTRICT'), nullable=False, index=True)
    amount = Column(Numeric(14, 2), nullable=False)
    amount_paid = Column(Numeric(14, 2), nullable=True, default=0.00)
    payment_date = Column(DateTime, nullable=True)
    status = Column(String(50), nullable=False, default='pending', index=True)
    payment_method = Column(String(50), nullable=False, default='mpesa')  # mpesa, cash, cheque, bank_transfer
    mpesa_trans_id = Column(String(100), nullable=True, index=True)
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc), index=True)
    updated_at = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

    # Support dict init for migration & compatibility
    def __init__(self, *args, **kwargs):
        if args and isinstance(args[0], dict):
            payment_data = args[0]
            super().__init__()
            if '_id' in payment_data:
                try:
                    self.id = int(str(payment_data['_id']))
                except (ValueError, TypeError):
                    pass
            self.receipt_number = payment_data.get('receipt_number')
            self.policy_id = payment_data.get('policy_id')
            self.client_id = payment_data.get('client_id')
            self.amount = payment_data.get('amount')
            self.payment_date = payment_data.get('payment_date')
            self.status = payment_data.get('status', 'pending')
            self.payment_method = payment_data.get('payment_method', 'mpesa')
            self.mpesa_trans_id = payment_data.get('mpesa_trans_id')
            self.notes = payment_data.get('notes')
            self.created_at = payment_data.get('created_at') or datetime.utcnow()
            self.updated_at = payment_data.get('updated_at') or datetime.utcnow()
        else:
            super().__init__(*args, **kwargs)

    policy = relationship('Policy', back_populates='payments', foreign_keys=[policy_id])
    client = relationship('User', back_populates='payments', foreign_keys=[client_id])


class MpesaTransaction(db.Model, ModelMixin):
    __tablename__ = 'mpesa_transactions'

    id = Column(Integer, primary_key=True, autoincrement=True)
    trans_id = Column(String(100), unique=True, nullable=False, index=True)
    trans_time = Column(DateTime, nullable=False)
    trans_amount = Column(Numeric(14, 2), nullable=False)
    business_short_code = Column(String(50), nullable=False)
    bill_ref_number = Column(String(100), nullable=True, index=True)
    invoice_number = Column(String(100), nullable=True)
    org_account_balance = Column(Numeric(14, 2), nullable=True)
    third_party_trans_id = Column(String(100), nullable=True)
    msisdn = Column(String(50), nullable=False, index=True)
    first_name = Column(String(100), nullable=True)
    middle_name = Column(String(100), nullable=True)
    last_name = Column(String(100), nullable=True)
    status = Column(String(50), nullable=False, default='pending', index=True)
    raw_payload = Column(Text, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow, index=True)


class Reminder(db.Model, ModelMixin):
    __tablename__ = 'reminders'

    id = Column(Integer, primary_key=True, autoincrement=True)
    policy_id = Column(Integer, ForeignKey('policies.id', ondelete='CASCADE'), nullable=True, index=True)
    client_id = Column(Integer, ForeignKey('users.id', ondelete='CASCADE'), nullable=True, index=True)
    reminder_type = Column(String(50), nullable=False, default='renewal')  # renewal, payment_due, expiry
    due_date = Column(DateTime, nullable=True, index=True)
    status = Column(String(50), nullable=False, default='pending', index=True)
    sent_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))

    # Engine v2 / v3 extensions
    kind = Column(String(50), nullable=True, index=True)  # staff_notice, customer_sms
    offset_days = Column(Integer, nullable=True)
    policy_number = Column(String(100), nullable=True, index=True)
    manual = Column(Boolean, nullable=False, default=False)
    channel_name = Column(String(50), nullable=True)

    def __init__(self, *args, **kwargs):
        if 'kind' in kwargs and 'reminder_type' not in kwargs:
            kwargs['reminder_type'] = kwargs['kind']
        elif 'reminder_type' in kwargs and 'kind' not in kwargs:
            kwargs['kind'] = kwargs['reminder_type']
        if 'channel' in kwargs:
            kwargs['channel_name'] = kwargs.pop('channel')
        if 'days_remaining' in kwargs:
            dr = kwargs.pop('days_remaining')
            if 'offset_days' not in kwargs:
                kwargs['offset_days'] = dr
        if 'due_date' not in kwargs:
            kwargs['due_date'] = datetime.now(timezone.utc)
        valid_cols = {c.name for c in self.__table__.columns}
        filtered_kwargs = {k: v for k, v in kwargs.items() if k in valid_cols or hasattr(self.__class__, k)}
        super().__init__(*args, **filtered_kwargs)

    @property
    def channel(self):
        return self.channel_name

    @channel.setter
    def channel(self, val):
        self.channel_name = val

    @property
    def days_remaining(self):
        return self.offset_days

    @days_remaining.setter
    def days_remaining(self, val):
        self.offset_days = val

    policy = relationship('Policy', back_populates='reminders', foreign_keys=[policy_id])
    client = relationship('User', foreign_keys=[client_id])


class SmsOutbox(db.Model, ModelMixin):
    __tablename__ = 'sms_outbox'

    id = Column(Integer, primary_key=True, autoincrement=True)
    phone_number = Column(String(50), nullable=False, index=True)
    destination = Column(String(50), nullable=False, index=True)  # E.164 format
    message = Column(Text, nullable=False)
    kind = Column(String(50), nullable=False)  # renewal_reminder, payment_receipt, etc.
    priority = Column(Integer, nullable=False, default=1)  # 0=transactional, 1=standard, 2=bulk
    status = Column(String(50), nullable=False, default='queued', index=True)  # queued, sending, sent, failed, dead, suppressed
    idempotency_key = Column(String(128), unique=True, nullable=False, index=True)
    template_key = Column(String(100), nullable=True)
    template_version = Column(Integer, nullable=False, default=1)
    policy_id = Column(Integer, ForeignKey('policies.id'), nullable=True, index=True)
    client_id = Column(Integer, ForeignKey('users.id'), nullable=True, index=True)
    manual = Column(Boolean, nullable=False, default=False)
    meta = Column(Text, nullable=True)  # JSON string
    attempts = Column(Integer, nullable=False, default=0)
    max_attempts = Column(Integer, nullable=False, default=5)
    simulated = Column(Boolean, nullable=False, default=False)
    segments = Column(Integer, nullable=False, default=0)
    cost_kes = Column(Numeric(10, 2), nullable=False, default=0.0)
    provider_ref = Column(String(100), nullable=True)
    provider_status = Column(String(50), nullable=True)
    last_error = Column(Text, nullable=True)
    next_attempt_at = Column(DateTime, nullable=True)
    deferred_reason = Column(String(100), nullable=True)
    suppress_reason = Column(String(100), nullable=True)
    worker = Column(String(100), nullable=True)  # worker ID or name
    sent_at = Column(DateTime, nullable=True)
    delivered_at = Column(DateTime, nullable=True)
    dead_at = Column(DateTime, nullable=True)
    dead_reason = Column(String(100), nullable=True)
    created_at = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc), index=True)
    updated_at = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))


class SmsSuppression(db.Model, ModelMixin):
    __tablename__ = 'sms_suppressions'

    id = Column(Integer, primary_key=True, autoincrement=True)
    phone_number = Column(String(50), unique=True, nullable=False, index=True)
    reason = Column(String(255), nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    def __init__(self, *args, **kwargs):
        if 'source' in kwargs and 'reason' not in kwargs:
            kwargs['reason'] = kwargs.pop('source')
        super().__init__(*args, **kwargs)


class Notification(db.Model, ModelMixin):
    __tablename__ = 'notifications'

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey('users.id', ondelete='CASCADE'), nullable=True, index=True)
    audience = Column(String(50), nullable=False, index=True)  # staff
    category = Column(String(50), nullable=False, index=True)  # reminder, sms_success, etc.
    severity = Column(String(20), nullable=False, index=True)  # info, success, warning, error
    title = Column(String(255), nullable=False)
    body = Column(Text, nullable=False)  # Changed from message to body
    is_read = Column(Boolean, nullable=False, default=False, index=True)
    read_by = Column(Text, nullable=True)  # Comma-separated string of user IDs who have read this
    policy_id = Column(Integer, ForeignKey('policies.id'), nullable=True, index=True)
    policy_number = Column(String(100), nullable=True, index=True)
    created_at = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc), index=True)
    updated_at = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

    user = relationship('User', back_populates='notifications', foreign_keys=[user_id])


class AuditLog(db.Model, ModelMixin):
    __tablename__ = 'audit_logs'

    id = Column(Integer, primary_key=True, autoincrement=True)
    performed_by = Column(Integer, ForeignKey('users.id', ondelete='SET NULL'), nullable=True, index=True)
    action = Column(String(100), nullable=False, index=True)
    entity_type = Column(String(100), nullable=False, index=True)
    entity_id = Column(String(100), nullable=True, index=True)
    details = Column(Text, nullable=True)
    ip_address = Column(String(50), nullable=True)
    created_at = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc), index=True)

    @property
    def timestamp(self):
        return self.created_at

    @timestamp.setter
    def timestamp(self, val):
        self.created_at = val

    user = relationship('User', back_populates='audit_logs', foreign_keys=[performed_by])


class AppSetting(db.Model, ModelMixin):
    __tablename__ = 'app_settings'

    id = Column(Integer, primary_key=True, autoincrement=True)
    key = Column(String(100), unique=True, nullable=False, index=True)
    value = Column(Text, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)


class Counter(db.Model, ModelMixin):
    __tablename__ = 'counters'

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(100), unique=True, nullable=False, index=True)
    current_val = Column(Integer, nullable=False, default=0)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)


class Smstemplate(db.Model, ModelMixin):
    __tablename__ = 'sms_templates'

    id = Column(Integer, primary_key=True, autoincrement=True)
    key = Column(String(100), unique=True, nullable=False, index=True)
    body = Column(Text, nullable=False)
    version = Column(Integer, nullable=False, default=1)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)
