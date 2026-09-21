import datetime
from bson import ObjectId
from app.extensions import get_db
from ..utils.visibility import visible_client_ids

class PaymentService:
    @staticmethod
    def _scope(user, base=None):
        """Payments carry no owner of their own — they are scoped by client.

        Returns a query dict. A worker with no clients gets {"$in": []}, which
        matches nothing; it never silently degrades to "everything".
        """
        query = dict(base or {})
        client_ids = visible_client_ids(user)
        if client_ids is not None:
            query["client_id"] = {"$in": client_ids}
        return query

    @staticmethod
    def get_financial_stats(user=None):
        db = get_db()

        def total_for(status):
            rows = list(db.payments.aggregate([
                {"$match": PaymentService._scope(user, {"status": status})},
                {"$group": {"_id": None, "total": {"$sum": "$amount"}}}
            ]))
            return rows[0]['total'] if rows else 0

        return {
            "total_receivable": total_for("receivable"),
            "total_overdue": total_for("overdue"),
            "total_transactions": total_for("paid")
        }

    @staticmethod
    def get_outstanding_balances(user=None):
        db = get_db()
        # Find payments that are overdue or receivable
        outstanding = list(
            db.payments.find(
                PaymentService._scope(user, {"status": {"$in": ["overdue", "receivable"]}})
            ).sort("payment_date", 1)
        )

        for p in outstanding:
            p['_id'] = str(p['_id'])
            if p.get('client_id'):
                client = db.users.find_one({"_id": ObjectId(p['client_id'])})
                p['client_name'] = client.get('full_name') if client else 'Unknown'
            else:
                p['client_name'] = 'Unknown'

        return outstanding

    @staticmethod
    def get_recent_payments(limit=10, user=None):
        db = get_db()
        recent = list(
            db.payments.find(PaymentService._scope(user, {"status": "paid"}))
            .sort("payment_date", -1)
            .limit(limit)
        )

        for p in recent:
            p['_id'] = str(p['_id'])
            if p.get('client_id'):
                client = db.users.find_one({"_id": ObjectId(p['client_id'])})
                p['client_name'] = client.get('full_name') if client else 'Unknown'
            else:
                p['client_name'] = 'Unknown'

        return recent

    @staticmethod
    def get_payments_by_status(status, user=None):
        db = get_db()
        payments = list(
            db.payments.find(PaymentService._scope(user, {"status": status}))
            .sort("payment_date", -1)
        )
        
        for p in payments:
            p['_id'] = str(p['_id'])
            
            # Fetch client details
            if p.get('client_id'):
                client = db.users.find_one({"_id": ObjectId(p['client_id'])})
                if client:
                    p['client_name'] = client.get('full_name', 'Unknown')
                    p['client_email'] = client.get('email', 'Unknown')
                    p['client_phone'] = client.get('phone', 'Unknown')
                    p['client_uid'] = client.get('client_id', 'Unknown')
                else:
                    p['client_name'] = 'Unknown'
                    p['client_email'] = 'Unknown'
                    p['client_phone'] = 'Unknown'
                    p['client_uid'] = 'Unknown'
            else:
                p['client_name'] = 'Unknown'
                p['client_email'] = 'Unknown'
                p['client_phone'] = 'Unknown'
                p['client_uid'] = 'Unknown'
                
            # Fetch policy details
            if p.get('policy_id'):
                policy = db.policies.find_one({"_id": ObjectId(p['policy_id'])})
                if policy:
                    p['policy_number'] = policy.get('policy_number', 'Unknown')
                    p['policy_type'] = policy.get('policy_type', 'Unknown')
                    p['policy_status'] = policy.get('status', 'Unknown')
                    
                    # Fetch vehicle details
                    if policy.get('vehicle_id'):
                        vehicle = db.vehicles.find_one({"_id": ObjectId(policy['vehicle_id'])})
                        if vehicle:
                            p['vehicle_reg'] = vehicle.get('registration_number', 'Unknown')
                            p['vehicle_make'] = vehicle.get('make', 'Unknown')
                            p['vehicle_model'] = vehicle.get('model', 'Unknown')
                        else:
                            p['vehicle_reg'] = 'Unknown'
                            p['vehicle_make'] = 'Unknown'
                            p['vehicle_model'] = 'Unknown'
                    else:
                        p['vehicle_reg'] = 'Unknown'
                        p['vehicle_make'] = 'Unknown'
                        p['vehicle_model'] = 'Unknown'
                else:
                    p['policy_number'] = 'Unknown'
                    p['policy_type'] = 'Unknown'
                    p['policy_status'] = 'Unknown'
                    p['vehicle_reg'] = 'Unknown'
                    p['vehicle_make'] = 'Unknown'
                    p['vehicle_model'] = 'Unknown'
            else:
                p['policy_number'] = 'Unknown'
                p['policy_type'] = 'Unknown'
                p['policy_status'] = 'Unknown'
                p['vehicle_reg'] = 'Unknown'
                p['vehicle_make'] = 'Unknown'
                p['vehicle_model'] = 'Unknown'
                
            # Format datetime
            if isinstance(p.get('payment_date'), datetime.datetime):
                p['payment_date_str'] = p['payment_date'].strftime('%b %d, %Y')
            else:
                p['payment_date_str'] = 'N/A'
                
            p['policy_id'] = str(p['policy_id']) if p.get('policy_id') else None
            p['client_id'] = str(p['client_id']) if p.get('client_id') else None

        return payments

    @staticmethod
    def get_invoices(user=None):
        """Builds invoice registry derived from policies and payment requests."""
        db = get_db()
        from ..utils.visibility import visible_client_ids
        client_ids = visible_client_ids(user)

        policy_query = {}
        if client_ids is not None:
            policy_query["client_id"] = {"$in": client_ids}

        policies = list(db.policies.find(policy_query).sort("created_at", -1))
        invoices = []
        for p in policies:
            c_id = p.get('client_id')
            client = db.users.find_one({"_id": c_id}) if c_id else None
            vehicle = db.vehicles.find_one({"_id": p.get('vehicle_id')}) if p.get('vehicle_id') else None

            # Determine payment status for this policy
            payment = db.payments.find_one({"policy_id": p['_id']})
            status = 'PAID' if payment and payment.get('status') == 'paid' else (
                'OVERDUE' if p.get('status') == 'active' and not payment else 'PENDING'
            )

            inv_num = f"INV-{p.get('policy_number', str(p['_id'])[:8]).upper()}"
            invoices.append({
                "invoice_number": inv_num,
                "policy_id": str(p['_id']),
                "policy_number": p.get('policy_number', 'N/A'),
                "client_id": str(c_id) if c_id else None,
                "client_name": client.get('full_name') if client else (p.get('client_name') or 'Unknown'),
                "phone": client.get('phone') if client else 'N/A',
                "vehicle_reg": vehicle.get('registration_number') if vehicle else (p.get('vehicle_reg') or 'N/A'),
                "amount": float(p.get('premium_amount') or 0),
                "due_date": p.get('effective_date') or 'N/A',
                "status": status,
                "created_at": p.get('created_at')
            })

        return invoices

    @staticmethod
    def get_all_transactions(user=None, limit=100):
        """Unified transaction stream covering cash, Paybill, and ledger items."""
        db = get_db()
        payments = list(
            db.payments.find(PaymentService._scope(user))
            .sort("payment_date", -1)
            .limit(limit)
        )

        transactions = []
        for p in payments:
            c_id = p.get('client_id')
            client = db.users.find_one({"_id": ObjectId(c_id)}) if c_id else None
            policy = db.policies.find_one({"_id": ObjectId(p['policy_id'])}) if p.get('policy_id') else None

            transactions.append({
                "_id": str(p['_id']),
                "receipt_no": p.get('receipt_number') or p.get('mpesa_receipt_number') or f"REC-{str(p['_id'])[-6:].upper()}",
                "client_name": client.get('full_name') if client else 'Direct Customer',
                "client_phone": client.get('phone') if client else 'N/A',
                "policy_number": policy.get('policy_number') if policy else (p.get('description') or 'Standard Premium'),
                "payment_method": p.get('method', p.get('payment_method', 'Paybill')),
                "amount": float(p.get('amount') or 0),
                "status": p.get('status', 'paid').upper(),
                "created_at": p.get('payment_date') or p.get('created_at')
            })

        return transactions

    @staticmethod
    def get_ledger_summary():
        """Company-wide financial ledger summary — strictly for Admins."""
        db = get_db()
        all_policies = list(db.policies.find({"status": {"$in": ["active", "published", "approved"]}}))
        total_premium = sum(float(p.get('premium_amount') or 0) for p in all_policies)

        paid_rows = list(db.payments.aggregate([
            {"$match": {"status": "paid"}},
            {"$group": {"_id": None, "total": {"$sum": "$amount"}}}
        ]))
        total_collected = paid_rows[0]['total'] if paid_rows else 0.0

        receivable_rows = list(db.payments.aggregate([
            {"$match": {"status": {"$in": ["receivable", "overdue"]}}},
            {"$group": {"_id": None, "total": {"$sum": "$amount"}}}
        ]))
        total_receivable = receivable_rows[0]['total'] if receivable_rows else 0.0

        agency_commission = total_collected * 0.10  # Standard 10% brokerage commission
        underwriter_payable = total_collected - agency_commission

        return {
            "total_premium_written": total_premium,
            "total_collected": total_collected,
            "total_receivable": total_receivable,
            "agency_commission": agency_commission,
            "underwriter_payable": underwriter_payable,
            "active_policies_count": len(all_policies)
        }

    @staticmethod
    def add_payment(policy_id, client_id, amount, status, description, payment_date=None):
        db = get_db()

        payment_data = {
            "policy_id": ObjectId(policy_id) if policy_id else None,
            "client_id": ObjectId(client_id) if client_id else None,
            "amount": float(amount),
            "status": status,
            "description": description,
            "payment_date": payment_date or datetime.datetime.now(datetime.timezone.utc)
        }

        result = db.payments.insert_one(payment_data)
        payment_data['_id'] = str(result.inserted_id)
        return payment_data

    # ================================================== receipt allocation ==
    # Dues are `payments` docs with status receivable/overdue. `amount` on a
    # due is ALWAYS the remaining balance (mutated downward by allocation);
    # `original_amount` / `amount_paid` preserve the history. This keeps every
    # existing reader (balances tab, stats, ledger) correct with no changes.
    DUE_STATUSES = ("receivable", "overdue")
    _EPSILON = 0.01

    @staticmethod
    def _generate_receipt_number(db, prefix="RCPT"):
        """Unique human receipt number: RCPT-YYYYMMDD-XXXX."""
        import random
        stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d")
        for _ in range(10):
            number = f"{prefix}-{stamp}-{random.randint(1000, 9999)}"
            if not db.payments.find_one({"receipt_number": number}):
                return number
        return f"{prefix}-{stamp}-{random.randint(10000, 99999)}"

    @staticmethod
    def get_client_dues(client_id, policy_id=None):
        """Oldest-first outstanding dues for a client, optionally one policy."""
        db = get_db()
        query = {
            "client_id": ObjectId(client_id),
            "status": {"$in": list(PaymentService.DUE_STATUSES)},
        }
        if policy_id:
            query["policy_id"] = ObjectId(policy_id)
        return list(db.payments.find(query).sort("payment_date", 1))

    @staticmethod
    def client_balance_due(client_id, policy_id=None):
        """Total remaining due across a client's outstanding dues."""
        dues = PaymentService.get_client_dues(client_id, policy_id)
        return round(sum(float(d.get("amount") or 0) for d in dues), 2)

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
        db = get_db()
        if amount is None or float(amount) <= 0:
            raise ValueError("Payment amount must be greater than zero.")
        tendered = round(float(amount), 2)
        now = payment_date or datetime.datetime.now(datetime.timezone.utc)

        dues = PaymentService.get_client_dues(client_id, policy_id)
        allocations = []
        left = tendered
        for due in dues:
            if left < PaymentService._EPSILON:
                break
            remaining = round(float(due.get("amount") or 0), 2)
            if remaining < PaymentService._EPSILON:
                continue
            take = round(min(remaining, left), 2)
            new_remaining = round(remaining - take, 2)
            new_paid = round(float(due.get("amount_paid") or 0) + take, 2)
            update = {"amount": new_remaining, "amount_paid": new_paid}
            if new_remaining < PaymentService._EPSILON:
                update["amount"] = 0.0
                update["status"] = "paid"
                update["settled_at"] = now
            db.payments.update_one({"_id": due["_id"]}, {"$set": update})
            allocations.append({
                "due_id": due["_id"],
                "policy_id": due.get("policy_id"),
                "amount": take,
            })
            left = round(left - take, 2)

        receipt = {
            "policy_id": ObjectId(policy_id) if policy_id else (
                allocations[0]["policy_id"] if len(allocations) == 1 else None
            ),
            "client_id": ObjectId(client_id),
            "amount": tendered,
            "status": "paid",
            "method": method,
            "payment_method": method,
            "receipt_number": receipt_number or PaymentService._generate_receipt_number(db),
            "phone_number": phone_number,
            "description": description or f"{method} receipt",
            "allocations": [
                {**a, "due_id": str(a["due_id"]),
                 "policy_id": str(a["policy_id"]) if a["policy_id"] else None}
                for a in allocations
            ],
            "allocated_amount": round(tendered - left, 2),
            "unallocated_amount": round(left, 2),
            "recorded_by": str(recorded_by) if recorded_by else None,
            "payment_date": now,
            "created_at": now,
        }
        if extra:
            receipt.update(extra)
        result = db.payments.insert_one(receipt)
        receipt["_id"] = str(result.inserted_id)

        # Stamp last-payment counters on uniquely-identified policies.
        touched_policies = {str(a["policy_id"]) for a in allocations if a["policy_id"]}
        if policy_id:
            touched_policies.add(str(policy_id))
        for pid in touched_policies:
            try:
                db.policies.update_one(
                    {"_id": ObjectId(pid)},
                    {"$set": {
                        "last_payment_receipt": receipt["receipt_number"],
                        "last_payment_date": now,
                        "last_payment_amount": tendered,
                    }},
                )
            except Exception:
                continue

        return receipt
