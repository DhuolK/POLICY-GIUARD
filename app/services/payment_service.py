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
    def add_payment(policy_id, client_id, amount, status, description, payment_date=None):
        db = get_db()
        
        payment_data = {
            "policy_id": ObjectId(policy_id) if policy_id else None,
            "client_id": ObjectId(client_id) if client_id else None,
            "amount": float(amount),
            "status": status,
            "description": description,
            "payment_date": payment_date or datetime.datetime.utcnow()
        }
        
        result = db.payments.insert_one(payment_data)
        payment_data['_id'] = str(result.inserted_id)
        return payment_data
