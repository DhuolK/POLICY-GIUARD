import datetime
from datetime import datetime as dt, timezone
from app.extensions import db
from app.models import Payment, Policy, User, Vehicle
from ..utils.visibility import visible_client_ids


class PaymentService:
    @staticmethod
    def _scope(user, base=None):
        """Payments carry no owner of their own — they are scoped by client.

        Returns a query dict. A worker with no clients gets {"$in": []}, which
        matches nothing; it never silently degrades to "everything".
        """
        # In SQLAlchemy, we'll return a WHERE clause instead of a dict
        if base is None:
            base = True  # Start with no restrictions

        client_ids = visible_client_ids(user)
        if client_ids is not None:
            if not client_ids:
                return False  # Match nothing
            base = db.and_(base, Payment.client_id.in_(client_ids))
        return base

    @staticmethod
    def get_financial_stats(user=None):
        # Base query with visibility scoping
        stmt = db.select(Payment)
        scope_clause = PaymentService._scope(user)
        if scope_clause is not False:  # False means match nothing
            if scope_clause is not True:  # True means no restrictions
                stmt = stmt.where(scope_clause)

        def total_for(status):
            status_stmt = stmt.where(Payment.status == status)
            total_stmt = db.select(db.func.sum(Payment.amount)).select_from(status_stmt.subquery())
            result = db.session.execute(total_stmt).scalar()
            return result or 0

        return {
            "total_receivable": total_for("receivable"),
            "total_overdue": total_for("overdue"),
            "total_transactions": total_for("paid")
        }

    @staticmethod
    def get_outstanding_balances(user=None):
        # Base query with visibility scoping
        stmt = db.select(Payment).where(
            Payment.status.in_(["overdue", "receivable"])
        ).order_by(Payment.payment_date.asc())

        scope_clause = PaymentService._scope(user)
        if scope_clause is not False:  # False means match nothing
            if scope_clause is not True:  # True means no restrictions
                stmt = stmt.where(scope_clause)

        payments = db.session.execute(stmt).scalars().all()

        # Convert to dict format for compatibility
        result = []
        for payment in payments:
            payment_dict = payment.to_dict()
            payment_dict['_id'] = str(payment.id)

            if payment.client_id:
                client = db.session.get(User, payment.client_id)
                payment_dict['client_name'] = client.full_name if client else 'Unknown'
            else:
                payment_dict['client_name'] = 'Unknown'

            result.append(payment_dict)

        return result

    @staticmethod
    def get_recent_payments(limit=10, user=None):
        # Base query with visibility scoping
        stmt = db.select(Payment).where(Payment.status == "paid").order_by(Payment.payment_date.desc()).limit(limit)

        scope_clause = PaymentService._scope(user)
        if scope_clause is not False:  # False means match nothing
            if scope_clause is not True:  # True means no restrictions
                stmt = stmt.where(scope_clause)

        payments = db.session.execute(stmt).scalars().all()

        # Convert to dict format for compatibility
        result = []
        for payment in payments:
            payment_dict = payment.to_dict()
            payment_dict['_id'] = str(payment.id)

            if payment.client_id:
                client = db.session.get(User, payment.client_id)
                payment_dict['client_name'] = client.full_name if client else 'Unknown'
            else:
                payment_dict['client_name'] = 'Unknown'

            result.append(payment_dict)

        return result

    @staticmethod
    def get_payments_by_status(status, user=None):
        # Base query with visibility scoping
        stmt = db.select(Payment).where(Payment.status == status).order_by(Payment.payment_date.desc())

        scope_clause = PaymentService._scope(user)
        if scope_clause is not False:  # False means match nothing
            if scope_clause is not True:  # True means no restrictions
                stmt = stmt.where(scope_clause)

        payments = db.session.execute(stmt).scalars().all()

        # Convert to dict format for compatibility
        result = []
        for payment in payments:
            payment_dict = payment.to_dict()
            payment_dict['_id'] = str(payment.id)

            # Fetch client details
            if payment.client_id:
                client = db.session.get(User, payment.client_id)
                if client:
                    payment_dict['client_name'] = client.full_name
                    payment_dict['client_email'] = client.email
                    payment_dict['client_phone'] = client.phone
                    payment_dict['client_uid'] = payment.client_id  # This was client_id_number in Mongo
                else:
                    payment_dict['client_name'] = 'Unknown'
                    payment_dict['client_email'] = 'Unknown'
                    payment_dict['client_phone'] = 'Unknown'
                    payment_dict['client_uid'] = 'Unknown'
            else:
                payment_dict['client_name'] = 'Unknown'
                payment_dict['client_email'] = 'Unknown'
                payment_dict['client_phone'] = 'Unknown'
                payment_dict['client_uid'] = 'Unknown'

            # Fetch policy details
            if payment.policy_id:
                policy = db.session.get(Policy, payment.policy_id)
                if policy:
                    payment_dict['policy_number'] = policy.policy_number
                    payment_dict['policy_type'] = policy.policy_type.name if policy.policy_type else 'Unknown'
                    payment_dict['policy_status'] = policy.status

                    # Fetch vehicle details
                    if policy.vehicle_id:
                        vehicle = db.session.get(Vehicle, policy.vehicle_id)
                        if vehicle:
                            payment_dict['vehicle_reg'] = vehicle.registration_number
                            payment_dict['vehicle_make'] = vehicle.make
                            payment_dict['vehicle_model'] = vehicle.model
                        else:
                            payment_dict['vehicle_reg'] = 'Unknown'
                            payment_dict['vehicle_make'] = 'Unknown'
                            payment_dict['vehicle_model'] = 'Unknown'
                    else:
                        payment_dict['vehicle_reg'] = 'Unknown'
                        payment_dict['vehicle_make'] = 'Unknown'
                        payment_dict['vehicle_model'] = 'Unknown'
                else:
                    payment_dict['policy_number'] = 'Unknown'
                    payment_dict['policy_type'] = 'Unknown'
                    payment_dict['policy_status'] = 'Unknown'
                    payment_dict['vehicle_reg'] = 'Unknown'
                    payment_dict['vehicle_make'] = 'Unknown'
                    payment_dict['vehicle_model'] = 'Unknown'
            else:
                payment_dict['policy_number'] = 'Unknown'
                payment_dict['policy_type'] = 'Unknown'
                payment_dict['policy_status'] = 'Unknown'
                payment_dict['vehicle_reg'] = 'Unknown'
                payment_dict['vehicle_make'] = 'Unknown'
                payment_dict['vehicle_model'] = 'Unknown'

            # Format datetime
            if isinstance(payment.payment_date, datetime.datetime):
                payment_dict['payment_date_str'] = payment.payment_date.strftime('%b %d, %Y')
            else:
                payment_dict['payment_date_str'] = 'N/A'

            payment_dict['policy_id'] = str(payment.policy_id) if payment.policy_id else None
            payment_dict['client_id'] = str(payment.client_id) if payment.client_id else None

            result.append(payment_dict)

        return result

    @staticmethod
    def get_invoices(user=None):
        """Builds invoice registry derived from policies and payment requests."""
        from ..utils.visibility import visible_client_ids

        # Base query for policies with visibility scoping
        stmt = db.select(Policy).join(User, Policy.client_id == User.id)

        client_ids = visible_client_ids(user)
        if client_ids is not None:
            if not client_ids:
                return []  # No visible clients, no invoices
            stmt = stmt.where(Policy.client_id.in_(client_ids))

        policies = db.session.execute(stmt).scalars().all()

        invoices = []
        for policy in policies:
            client = db.session.get(User, policy.client_id)
            vehicle = db.session.get(Vehicle, policy.vehicle_id) if policy.vehicle_id else None

            # Determine payment status for this policy
            payment_stmt = db.select(Payment).where(Payment.policy_id == policy.id)
            payment = db.session.execute(payment_stmt).scalars().first()
            status = 'PAID' if payment and payment.status == 'paid' else (
                'OVERDUE' if policy.status == 'active' and not payment else 'PENDING'
            )

            inv_num = f"INV-{policy.policy_number or str(policy.id)[:8].upper()}"
            invoices.append({
                "invoice_number": inv_num,
                "policy_id": str(policy.id),
                "policy_number": policy.policy_number or 'N/A',
                "client_id": str(client.id) if client else None,
                "client_name": client.full_name if client else (getattr(policy, 'client_name', None) or 'Unknown'),
                "phone": client.phone if client else 'N/A',
                "vehicle_reg": vehicle.registration_number if vehicle else (getattr(policy, 'vehicle_reg', None) or 'N/A'),
                "amount": float(policy.premium or 0),
                "due_date": policy.effective_date or 'N/A',
                "status": status,
                "created_at": policy.created_at
            })

        return invoices

    @staticmethod
    def get_all_transactions(user=None, limit=100):
        """Unified transaction stream covering cash, Paybill, and ledger items."""
        # Base query with visibility scoping
        stmt = db.select(Payment).order_by(Payment.payment_date.desc()).limit(limit)

        scope_clause = PaymentService._scope(user)
        if scope_clause is not False:  # False means match nothing
            if scope_clause is not True:  # True means no restrictions
                stmt = stmt.where(scope_clause)

        payments = db.session.execute(stmt).scalars().all()

        transactions = []
        for payment in payments:
            client = db.session.get(User, payment.client_id) if payment.client_id else None
            policy = db.session.get(Policy, payment.policy_id) if payment.policy_id else None

            transactions.append({
                "_id": str(payment.id),
                "receipt_no": payment.receipt_number or payment.mpesa_trans_id or f"REC-{str(payment.id)[-6:].upper()}",
                "client_name": client.full_name if client else 'Direct Customer',
                "client_phone": client.phone if client else 'N/A',
                "policy_number": policy.policy_number if policy else (payment.description or 'Standard Premium'),
                "payment_method": payment.payment_method or 'Paybill',
                "amount": float(payment.amount or 0),
                "status": (payment.status or 'paid').upper(),
                "created_at": payment.payment_date or payment.created_at
            })

        return transactions

    @staticmethod
    def get_ledger_summary():
        """Company-wide financial ledger summary — strictly for Admins."""
        # Total premium written (active/published/approved policies)
        total_premium_stmt = db.select(db.func.sum(Policy.premium)).where(
            Policy.status.in_(["active", "published", "approved"])
        )
        total_premium = db.session.execute(total_premium_stmt).scalar() or 0.0

        # Total collected (paid payments)
        total_collected_stmt = db.select(db.func.sum(Payment.amount)).where(
            Payment.status == "paid"
        )
        total_collected = db.session.execute(total_collected_stmt).scalar() or 0.0

        # Total receivable (receivable + overdue payments)
        total_receivable_stmt = db.select(db.func.sum(Payment.amount)).where(
            Payment.status.in_(["receivable", "overdue"])
        )
        total_receivable = db.session.execute(total_receivable_stmt).scalar() or 0.0

        agency_commission = total_collected * 0.10  # Standard 10% brokerage commission
        underwriter_payable = total_collected - agency_commission

        # Active policies count
        active_policies_stmt = db.select(db.func.count(Policy.id)).where(
            Policy.status.in_(["active", "published", "approved"])
        )
        active_policies_count = db.session.execute(active_policies_stmt).scalar() or 0

        return {
            "total_premium_written": total_premium,
            "total_collected": total_collected,
            "total_receivable": total_receivable,
            "agency_commission": agency_commission,
            "underwriter_payable": underwriter_payable,
            "active_policies_count": active_policies_count
        }

    @staticmethod
    def add_payment(policy_id, client_id, amount, status, description, payment_date=None):
        # Validate foreign keys
        if policy_id:
            policy = db.session.get(Policy, policy_id)
            if not policy:
                return None, "Invalid policy ID"

        if client_id:
            client = db.session.get(User, client_id)
            if not client or client.role != 'customer':
                return None, "Invalid client ID"

        payment = Payment(
            policy_id=policy_id,
            client_id=client_id,
            amount=float(amount),
            status=status,
            description=description,
            payment_date=payment_date or datetime.now(timezone.utc),
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc)
        )

        db.session.add(payment)
        db.session.commit()

        payment_dict = payment.to_dict()
        payment_dict['_id'] = str(payment.id)
        # Convert ID fields to strings for compatibility
        for key in ('policy_id', 'client_id'):
            val = getattr(payment, key)
            payment_dict[key] = str(val) if val is not None else ''
        return payment_dict

    # ================================================== receipt allocation ==
    # Dues are `payments` docs with status receivable/overdue. `amount` on a
    # due is ALWAYS the remaining balance (mutated downward by allocation);
    # `original_amount` / `amount_paid` preserve the history. This keeps every
    # existing reader (balances tab, stats, ledger) correct with no changes.
    DUE_STATUSES = ("receivable", "overdue")
    _EPSILON = 0.01

    @staticmethod
    def _generate_receipt_number(prefix="RCPT"):
        """Unique human receipt number: RCPT-YYYYMMDD-XXXX."""
        import random
        stamp = dt.now(timezone.utc).strftime("%Y%m%d")
        for _ in range(10):
            number = f"{prefix}-{stamp}-{random.randint(1000, 9999)}"
            # Check if receipt number already exists
            existing = db.session.execute(
                db.select(Payment).where(Payment.receipt_number == number)
            ).scalar_one_or_none()
            if not existing:
                return number
        return f"{prefix}-{stamp}-{random.randint(10000, 99999)}"

    @staticmethod
    def get_client_dues(client_id, policy_id=None):
        """Oldest-first outstanding dues for a client, optionally one policy."""
        stmt = db.select(Payment).where(
            Payment.client_id == client_id,
            Payment.status.in_(list(PaymentService.DUE_STATUSES))
        ).order_by(Payment.payment_date.asc())

        if policy_id is not None:
            stmt = stmt.where(Payment.policy_id == policy_id)

        return db.session.execute(stmt).scalars().all()

    @staticmethod
    def client_balance_due(client_id, policy_id=None):
        """Total remaining due across a client's outstanding dues."""
        dues = PaymentService.get_client_dues(client_id, policy_id)
        return round(sum(float(due.amount or 0) for due in dues), 2)

    @staticmethod
    def allocate_payment(client_id, amount, method, policy_id=None,
                         receipt_number=None, phone_number=None,
                         description=None, recorded_by=None,
                         payment_date=None, extra=None):
        """Record one receipt and spread it across oldest dues first (FIFO).

        Partial payments shrink each due's remaining `amount`; a due flips to
        `paid` only when fully covered. Anything beyond total dues is kept on
        the receipt as `unallocated_amount` (customer credit on record).

        Returns the receipt document (with `_id` stringified and an
        `allocations` list of {due_id, policy_id, amount}).
        """
        if amount is None or float(amount) <= 0:
            raise ValueError("Payment amount must be greater than zero.")
        tendered = round(float(amount), 2)
        now = payment_date or dt.now(timezone.utc)

        dues = PaymentService.get_client_dues(client_id, policy_id)
        allocations = []
        left = tendered
        for due in dues:
            if left < PaymentService._EPSILON:
                break
            remaining = round(float(due.amount or 0), 2)
            if remaining < PaymentService._EPSILON:
                continue
            take = round(min(remaining, left), 2)
            new_remaining = round(remaining - take, 2)
            new_paid = round(float(due.amount_paid or 0) + take, 2)

            # Update the due
            due.amount = new_remaining
            due.amount_paid = new_paid
            if new_remaining < PaymentService._EPSILON:
                due.amount = 0.0
                due.status = "paid"
            due.updated_at = now

            allocations.append({
                "due_id": due.id,
                "policy_id": due.policy_id,
                "amount": take,
            })
            left = round(left - take, 2)

        db.session.commit()  # Commit the due updates

        # Create the receipt payment
        receipt = Payment(
            policy_id=policy_id,
            client_id=client_id,
            amount=tendered,
            status="paid",
            payment_method=method,
            receipt_number=receipt_number or PaymentService._generate_receipt_number(),
            notes=description or f"{method} receipt",
            payment_date=now,
            created_at=now,
            updated_at=now
        )

        # Add allocations as a JSON-like field for compatibility
        # In a real implementation, we might have a separate allocations table
        # For now, we'll store it in a description or extra field, but to maintain
        # compatibility we'll add it to the returned dict

        db.session.add(receipt)
        db.session.commit()

        # Stamp last-payment counters on uniquely-identified policies.
        touched_policies = {str(a["policy_id"]) for a in allocations if a["policy_id"]}
        if policy_id:
            touched_policies.add(str(policy_id))
        for pid_str in touched_policies:
            try:
                pid = int(pid_str)
                policy = db.session.get(Policy, pid)
                if policy:
                    policy.last_payment_receipt = receipt.receipt_number
                    policy.last_payment_date = now
                    policy.last_payment_amount = tendered
                    policy.updated_at = now
            except (ValueError, TypeError):
                continue
        db.session.commit()

        # Return receipt document (with `_id` stringified and an `allocations` list)
        receipt_dict = receipt.to_dict()
        receipt_dict['_id'] = str(receipt.id)
        # Convert ID fields to strings for compatibility
        for key in ('policy_id', 'client_id'):
            val = getattr(receipt, key, None)
            receipt_dict[key] = str(val) if val is not None else ''
        # Add allocations list
        receipt_dict['allocations'] = [
            {**a, "due_id": str(a["due_id"]),
             "policy_id": str(a["policy_id"]) if a["policy_id"] else None}
            for a in allocations
        ]
        receipt_dict['allocated_amount'] = round(tendered - left, 2)
        receipt_dict['unallocated_amount'] = round(left, 2)

        return receipt_dict