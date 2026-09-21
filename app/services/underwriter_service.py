import datetime
from bson import ObjectId
from app.extensions import get_db

KENYAN_UNDERWRITERS = [
    {"name": "AAR Insurance Kenya Limited", "short_name": "AAR", "code": "AAR"},
    {"name": "Africa Merchant Assurance Company (AMACO)", "short_name": "AMACO", "code": "AMACO"},
    {"name": "AIG Kenya Insurance Company", "short_name": "AIG", "code": "AIG"},
    {"name": "Allianz General Insurance Kenya", "short_name": "Allianz", "code": "ALLIANZ"},
    {"name": "APA Insurance Limited", "short_name": "APA", "code": "APA"},
    {"name": "Britam General Insurance Company", "short_name": "Britam", "code": "BRITAM"},
    {"name": "Cannon General Insurance", "short_name": "Cannon", "code": "CANNON"},
    {"name": "CIC General Insurance Limited", "short_name": "CIC", "code": "CIC"},
    {"name": "Corporate Insurance Company", "short_name": "Corporate", "code": "CORP"},
    {"name": "Directline Assurance Company", "short_name": "Directline", "code": "DIRECTLINE"},
    {"name": "Fidelity Shield Insurance Company", "short_name": "Fidelity", "code": "FIDELITY"},
    {"name": "First Assurance Company Limited", "short_name": "First Assurance", "code": "FIRST"},
    {"name": "GA Insurance Limited", "short_name": "GA", "code": "GA"},
    {"name": "Gemsuites Insurance Company", "short_name": "Gemsuites", "code": "GEM"},
    {"name": "Heritage Insurance Company", "short_name": "Heritage", "code": "HERITAGE"},
    {"name": "ICEA LION General Insurance", "short_name": "ICEA LION", "code": "ICEA"},
    {"name": "Intra Africa Assurance Company", "short_name": "Intra Africa", "code": "INTRA"},
    {"name": "Invesco Assurance Company", "short_name": "Invesco", "code": "INVESCO"},
    {"name": "Jubilee General Insurance Limited", "short_name": "Jubilee", "code": "JUBILEE"},
    {"name": "Kenindia Assurance Company", "short_name": "Kenindia", "code": "KENINDIA"},
    {"name": "Kenya Orient Insurance Limited", "short_name": "Kenya Orient", "code": "ORIENT"},
    {"name": "Madison General Insurance Kenya", "short_name": "Madison", "code": "MADISON"},
    {"name": "Mayfair Insurance Company Limited", "short_name": "Mayfair", "code": "MAYFAIR"},
    {"name": "Monarch Insurance Company", "short_name": "Monarch", "code": "MONARCH"},
    {"name": "Occidental Insurance Company", "short_name": "Occidental", "code": "OCCIDENTAL"},
    {"name": "Old Mutual General Insurance Kenya", "short_name": "Old Mutual", "code": "OLDMUTUAL"},
    {"name": "PACIS Insurance Company Limited", "short_name": "PACIS", "code": "PACIS"},
    {"name": "Pioneer General Insurance Limited", "short_name": "Pioneer", "code": "PIONEER"},
    {"name": "Resolution Insurance Company", "short_name": "Resolution", "code": "RESOLUTION"},
    {"name": "Sanlam General Insurance Kenya", "short_name": "Sanlam", "code": "SANLAM"},
    {"name": "Takaful Insurance of Africa Limited", "short_name": "Takaful", "code": "TAKAFUL"},
    {"name": "Tausi Assurance Company Limited", "short_name": "Tausi", "code": "TAUSI"},
    {"name": "The Kenyan Alliance Insurance Company", "short_name": "Kenyan Alliance", "code": "KA"},
    {"name": "Trident Insurance Company Limited", "short_name": "Trident", "code": "TRIDENT"},
    {"name": "UAP Old Mutual Insurance", "short_name": "UAP", "code": "UAP"},
    {"name": "Xplico Insurance Company Limited", "short_name": "Xplico", "code": "XPLICO"}
]

class UnderwriterService:
    @staticmethod
    def seed_default_underwriters():
        db = get_db()
        count = db.underwriters.count_documents({})
        if count == 0:
            now = datetime.datetime.now(datetime.timezone.utc)
            docs = []
            for u in KENYAN_UNDERWRITERS:
                docs.append({
                    "name": u["name"],
                    "short_name": u["short_name"],
                    "code": u["code"],
                    "status": "active",
                    "created_at": now,
                    "updated_at": now
                })
            db.underwriters.insert_many(docs)

    @staticmethod
    def get_underwriters(active_only=False):
        db = get_db()
        UnderwriterService.seed_default_underwriters()
        query = {"status": "active"} if active_only else {}
        items = list(db.underwriters.find(query).sort("name", 1))
        for item in items:
            item['_id'] = str(item['_id'])
            item['vehicle_count'] = db.policies.count_documents({"insurance_company": item['name']})
        return items

    @staticmethod
    def add_underwriter(name, short_name, code=None):
        db = get_db()
        now = datetime.datetime.now(datetime.timezone.utc)
        doc = {
            "name": name.strip(),
            "short_name": short_name.strip(),
            "code": (code or short_name).strip().upper(),
            "status": "active",
            "created_at": now,
            "updated_at": now
        }
        res = db.underwriters.insert_one(doc)
        doc['_id'] = str(res.inserted_id)
        return doc

    @staticmethod
    def toggle_status(underwriter_id):
        db = get_db()
        if not ObjectId.is_valid(underwriter_id):
            return False
        uw = db.underwriters.find_one({"_id": ObjectId(underwriter_id)})
        if not uw:
            return False
        new_status = "inactive" if uw.get("status") == "active" else "active"
        db.underwriters.update_one(
            {"_id": ObjectId(underwriter_id)},
            {"$set": {"status": new_status, "updated_at": datetime.datetime.now(datetime.timezone.utc)}}
        )
        return True
