import datetime
import re
from bson import ObjectId
from app.extensions import get_db

KENYA_INSURANCE_COMPANIES_SEED = [
    {"name": "AAR Insurance Kenya Limited", "short_name": "AAR", "code": "AAR", "is_active": True},
    {"name": "Africa Merchant Assurance Company Limited (AMACO)", "short_name": "AMACO", "code": "AMA", "is_active": True},
    {"name": "AIG Kenya Insurance Company Limited", "short_name": "AIG", "code": "AIG", "is_active": True},
    {"name": "Allianz Insurance Company of Kenya Limited", "short_name": "Allianz", "code": "ALZ", "is_active": True},
    {"name": "APA Insurance Limited", "short_name": "APA", "code": "APA", "is_active": True},
    {"name": "Britam General Insurance Company (K) Limited", "short_name": "Britam", "code": "BRT", "is_active": True},
    {"name": "Cannon General Insurance Company Limited", "short_name": "Cannon", "code": "CAN", "is_active": True},
    {"name": "CIC General Insurance Limited", "short_name": "CIC", "code": "CIC", "is_active": True},
    {"name": "Corporate Insurance Company Limited", "short_name": "Corporate", "code": "COR", "is_active": True},
    {"name": "Directline Assurance Company Limited", "short_name": "Directline", "code": "DIR", "is_active": True},
    {"name": "Fidelity Shield Insurance Company Limited", "short_name": "Fidelity Shield", "code": "FID", "is_active": True},
    {"name": "First Assurance Company Limited", "short_name": "First Assurance", "code": "FST", "is_active": True},
    {"name": "GA Insurance Limited", "short_name": "GA Insurance", "code": "GAI", "is_active": True},
    {"name": "Geminia Insurance Company Limited", "short_name": "Geminia", "code": "GEM", "is_active": True},
    {"name": "Heritage Insurance Company Limited", "short_name": "Heritage", "code": "HER", "is_active": True},
    {"name": "ICEA LION General Insurance Company Limited", "short_name": "ICEA LION", "code": "ICE", "is_active": True},
    {"name": "Intra Africa Assurance Company Limited", "short_name": "Intra Africa", "code": "INT", "is_active": True},
    {"name": "Invesco Assurance Company Limited", "short_name": "Invesco", "code": "INV", "is_active": True},
    {"name": "Jubilee Allianz General Insurance (K) Limited", "short_name": "Jubilee Allianz", "code": "JUB", "is_active": True},
    {"name": "Jubilee Health Insurance Limited", "short_name": "Jubilee Health", "code": "JHL", "is_active": True},
    {"name": "Jubilee Life Insurance Limited", "short_name": "Jubilee Life", "code": "JLL", "is_active": True},
    {"name": "Kenindia Assurance Company Limited", "short_name": "Kenindia", "code": "KEN", "is_active": True},
    {"name": "Kenya Orient Insurance Limited", "short_name": "Kenya Orient", "code": "KOI", "is_active": True},
    {"name": "Kenya Orient Life Assurance Limited", "short_name": "Kenya Orient Life", "code": "KOL", "is_active": True},
    {"name": "Kuscco Mutual Assurance Limited", "short_name": "Kuscco Mutual", "code": "KUS", "is_active": True},
    {"name": "Liberty Life Assurance Kenya Limited", "short_name": "Liberty Life", "code": "LIB", "is_active": True},
    {"name": "Madison General Insurance Kenya Limited", "short_name": "Madison General", "code": "MAD", "is_active": True},
    {"name": "Madison Life Assurance Kenya Limited", "short_name": "Madison Life", "code": "MLF", "is_active": True},
    {"name": "Mayfair Insurance Company Limited", "short_name": "Mayfair", "code": "MAY", "is_active": True},
    {"name": "Metropolitan Cannon Life Assurance Limited", "short_name": "Metropolitan Cannon", "code": "MET", "is_active": True},
    {"name": "Minet Kenya Insurance Brokers Limited", "short_name": "Minet", "code": "MIN", "is_active": True},
    {"name": "Monarch Insurance Company Limited", "short_name": "Monarch", "code": "MON", "is_active": True},
    {"name": "Occidental Insurance Company Limited", "short_name": "Occidental", "code": "OCC", "is_active": True},
    {"name": "Old Mutual General Insurance Kenya Limited", "short_name": "Old Mutual General", "code": "OMG", "is_active": True},
    {"name": "Old Mutual Life Assurance Company Limited", "short_name": "Old Mutual Life", "code": "OML", "is_active": True},
    {"name": "Pacis Insurance Company Limited", "short_name": "Pacis", "code": "PAC", "is_active": True},
    {"name": "Pathfinder Insurance Brokers Limited", "short_name": "Pathfinder", "code": "PAT", "is_active": True},
    {"name": "Pioneer General Insurance Limited", "short_name": "Pioneer General", "code": "PGI", "is_active": True},
    {"name": "Pioneer Assurance Company Limited", "short_name": "Pioneer Assurance", "code": "PACL", "is_active": True},
    {"name": "Prudential Life Assurance Kenya Limited", "short_name": "Prudential Life", "code": "PRU", "is_active": True},
    {"name": "Resolution Insurance Company Limited", "short_name": "Resolution", "code": "RES", "is_active": True},
    {"name": "Sanlam General Insurance Company Limited", "short_name": "Sanlam General", "code": "SGI", "is_active": True},
    {"name": "Sanlam Life Insurance Company Limited", "short_name": "Sanlam Life", "code": "SLI", "is_active": True},
    {"name": "Star Health Insurance Kenya Limited", "short_name": "Star Health", "code": "STA", "is_active": True},
    {"name": "Takaful Insurance of Africa Limited", "short_name": "Takaful", "code": "TAK", "is_active": True},
    {"name": "Tausi Assurance Company Limited", "short_name": "Tausi", "code": "TAU", "is_active": True},
    {"name": "The Continental Reinsurance Limited", "short_name": "Continental Re", "code": "CON", "is_active": True},
    {"name": "The Kenyan Alliance Insurance Company Limited", "short_name": "Kenyan Alliance", "code": "KAL", "is_active": True},
    {"name": "Trident Insurance Company Limited", "short_name": "Trident", "code": "TRI", "is_active": True},
    {"name": "UAP Insurance Company Limited", "short_name": "UAP Insurance", "code": "UAP", "is_active": True},
    {"name": "Xplico Insurance Company Limited", "short_name": "Xplico", "code": "XPL", "is_active": True},
    {"name": "Absa Life Assurance Kenya Limited", "short_name": "Absa Life", "code": "ABS", "is_active": True},
    {"name": "Capex Life Assurance Company Limited", "short_name": "Capex Life", "code": "CAP", "is_active": True},
    {"name": "Saham Assurance Company Kenya Limited", "short_name": "Saham", "code": "SAH", "is_active": True},
    {"name": "Shield Assurance Company Limited", "short_name": "Shield Assurance", "code": "SHI", "is_active": True},
    {"name": "MUA Insurance (Kenya) Limited", "short_name": "MUA", "code": "MUA", "is_active": True},
]


class InsuranceCompanyService:
    @staticmethod
    def ensure_seeded():
        """Ensure the 56 licensed underwriters are seeded in the database."""
        db = get_db()
        count = db.insurance_companies.count_documents({})
        if count == 0:
            now = datetime.datetime.now(datetime.timezone.utc)
            docs = []
            for item in KENYA_INSURANCE_COMPANIES_SEED:
                doc = dict(item)
                doc["commission_rate"] = 10.0
                doc["contact_person"] = ""
                doc["created_at"] = now
                doc["updated_at"] = now
                docs.append(doc)
            db.insurance_companies.insert_many(docs)

    @staticmethod
    def get_companies(search_query=None, status_filter=None):
        """Fetch all insurance companies with their linked policy & vehicle counts."""
        db = get_db()
        InsuranceCompanyService.ensure_seeded()

        query = {}
        if search_query:
            query["$or"] = [
                {"name": {"$regex": re.escape(search_query), "$options": "i"}},
                {"short_name": {"$regex": re.escape(search_query), "$options": "i"}},
                {"code": {"$regex": re.escape(search_query), "$options": "i"}}
            ]

        if status_filter in ["active", "inactive"]:
            query["is_active"] = (status_filter == "active")

        companies = list(db.insurance_companies.find(query).sort("name", 1))

        # Aggregate vehicle and policy counts per insurance company
        for comp in companies:
            cid = comp["_id"]
            comp["_id"] = str(cid)

            # Count policies linking to this company
            policy_count = db.policies.count_documents({
                "$or": [
                    {"insurance_company_id": cid},
                    {"insurance_company_id": str(cid)},
                    {"insurance_company": comp.get("name")},
                    {"insurance_company": comp.get("short_name")}
                ]
            })
            comp["policy_count"] = policy_count

            # Count distinct vehicles linking to policies with this company
            vehicle_ids = db.policies.distinct("vehicle_id", {
                "$or": [
                    {"insurance_company_id": cid},
                    {"insurance_company_id": str(cid)},
                    {"insurance_company": comp.get("name")},
                    {"insurance_company": comp.get("short_name")}
                ]
            })
            comp["vehicle_count"] = len([v for v in vehicle_ids if v is not None])

        return companies

    @staticmethod
    def get_active_companies():
        """Returns active underwriters for select dropdowns."""
        db = get_db()
        InsuranceCompanyService.ensure_seeded()
        companies = list(db.insurance_companies.find({"is_active": True}).sort("name", 1))
        for comp in companies:
            comp["_id"] = str(comp["_id"])
        return companies

    @staticmethod
    def get_company_by_id(company_id):
        db = get_db()
        if not ObjectId.is_valid(company_id):
            return None
        comp = db.insurance_companies.find_one({"_id": ObjectId(company_id)})
        if comp:
            comp["_id"] = str(comp["_id"])
        return comp

    @staticmethod
    def add_company(name, short_name="", code="", phone="", email="", commission_rate=10.0, contact_person="", is_active=True):
        db = get_db()
        now = datetime.datetime.now(datetime.timezone.utc)
        try:
            comm_val = float(commission_rate)
        except (ValueError, TypeError):
            comm_val = 10.0

        doc = {
            "name": name.strip(),
            "short_name": short_name.strip() if short_name else name.strip()[:10],
            "code": code.strip().upper() if code else name.strip()[:3].upper(),
            "phone": phone.strip(),
            "email": email.strip(),
            "commission_rate": comm_val,
            "contact_person": contact_person.strip(),
            "is_active": bool(is_active),
            "created_at": now,
            "updated_at": now
        }
        res = db.insurance_companies.insert_one(doc)
        doc["_id"] = str(res.inserted_id)
        return doc

    @staticmethod
    def update_company(company_id, name, short_name="", code="", phone="", email="", commission_rate=10.0, contact_person="", is_active=True):
        db = get_db()
        if not ObjectId.is_valid(company_id):
            return False, "Invalid company ID"
        now = datetime.datetime.now(datetime.timezone.utc)
        try:
            comm_val = float(commission_rate)
        except (ValueError, TypeError):
            comm_val = 10.0

        update_doc = {
            "name": name.strip(),
            "short_name": short_name.strip(),
            "code": code.strip().upper(),
            "phone": phone.strip(),
            "email": email.strip(),
            "commission_rate": comm_val,
            "contact_person": contact_person.strip(),
            "is_active": bool(is_active),
            "updated_at": now
        }
        db.insurance_companies.update_one({"_id": ObjectId(company_id)}, {"$set": update_doc})
        return True, None

    @staticmethod
    def toggle_status(company_id, is_active=None):
        db = get_db()
        if not ObjectId.is_valid(company_id):
            return False, "Invalid company ID"
        comp = db.insurance_companies.find_one({"_id": ObjectId(company_id)})
        if not comp:
            return False, "Company not found"
        new_status = not comp.get("is_active", True) if is_active is None else bool(is_active)
        now = datetime.datetime.now(datetime.timezone.utc)
        db.insurance_companies.update_one(
            {"_id": ObjectId(company_id)},
            {"$set": {"is_active": new_status, "updated_at": now}}
        )
        return True, new_status
