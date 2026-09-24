import datetime
import logging
from app.extensions import db
from app.models import InsuranceCompany, Policy, Vehicle

# Seed data for the 56 licensed underwriters in Kenya
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
    {"name": "Absa Life Assurance Kenya Limited", "short_name": "Absa Life", "code": "ABS", "is_active": True},
    {"name": "Capex Life Assurance Company Limited", "short_name": "Capex Life", "code": "CAP", "is_active": True},
    {"name": "Saham Assurance Company Kenya Limited", "short_name": "Saham", "code": "SAH", "is_active": True},
    {"name": "Shield Assurance Company Limited", "short_name": "Shield Assurance", "code": "SHI", "is_active": True},
    {"name": "MUA Insurance (Kenya) Limited", "short_name": "MUA", "code": "MUA", "is_active": True},
]

logger = logging.getLogger(__name__)


def _utcnow():
    return datetime.datetime.now(datetime.timezone.utc)


class InsuranceCompanyService:
    @staticmethod
    def ensure_seeded():
        """Ensure the 56 licensed underwriters are seeded in the database."""
        # Check if any insurance companies exist
        count = db.session.execute(db.select(db.func.count(InsuranceCompany.id))).scalar()
        if count == 0:
            now = _utcnow()
            companies = []
            for item in KENYA_INSURANCE_COMPANIES_SEED:
                company = InsuranceCompany(
                    name=item["name"],
                    short_name=item["short_name"],
                    code=item["code"],
                    commission_rate=10.0,
                    contact_person="",
                    is_active=item["is_active"],
                    created_at=now,
                    updated_at=now
                )
                companies.append(company)

            db.session.add_all(companies)
            try:
                db.session.commit()
                logger.info(f"Seeded {len(companies)} insurance companies")
            except Exception as e:
                logger.error(f"Failed to seed insurance companies: {e}")
                db.session.rollback()

    @staticmethod
    def _utcnow():
        return datetime.datetime.now(datetime.timezone.utc)

    @staticmethod
    def get_companies(search_query=None, status_filter=None):
        """Fetch all insurance companies with their linked policy & vehicle counts."""
        InsuranceCompanyService.ensure_seeded()

        # Base query
        stmt = db.select(InsuranceCompany)

        # Apply search query
        if search_query:
            search_term = f"%{search_query}%"
            stmt = stmt.where(
                db.or_(
                    InsuranceCompany.name.ilike(search_term),
                    InsuranceCompany.short_name.ilike(search_term),
                    InsuranceCompany.code.ilike(search_term)
                )
            )

        # Apply status filter
        if status_filter in ["active", "inactive"]:
            is_active = (status_filter == "active")
            stmt = stmt.where(InsuranceCompany.is_active == is_active)

        # Order by name
        stmt = stmt.order_by(InsuranceCompany.name.asc())

        companies = db.session.execute(stmt).scalars().all()

        # Convert to dict format and add counts
        result = []
        for company in companies:
            company_dict = company.to_dict()

            # Count policies linking to this company
            policy_count = db.session.execute(
                db.select(db.func.count(Policy.id)).where(
                    db.or_(
                        Policy.insurance_company_id == company.id,
                        # For backward compatibility with string storage
                        db.and_(
                            InsuranceCompany.name.isnot(None),
                            db.func.lower(Policy.insurance_company) == db.func.lower(company.name)
                        ),
                        db.and_(
                            InsuranceCompany.short_name.isnot(None),
                            db.func.lower(Policy.insurance_company) == db.func.lower(company.short_name)
                        )
                    )
                )
            ).scalar()
            company_dict['policy_count'] = policy_count

            # Count distinct vehicles linking to policies with this company
            vehicle_count = db.session.execute(
                db.select(db.func.count(db.distinct(Vehicle.id))).where(
                    Vehicle.id.in_(
                        db.select(Policy.vehicle_id).where(
                            db.or_(
                                Policy.insurance_company_id == company.id,
                                db.and_(
                                    InsuranceCompany.name.isnot(None),
                                    db.func.lower(Policy.insurance_company) == db.func.lower(company.name)
                                ),
                                db.and_(
                                    InsuranceCompany.short_name.isnot(None),
                                    db.func.lower(Policy.insurance_company) == db.func.lower(company.short_name)
                                )
                            )
                        )
                    )
                )
            ).scalar()
            company_dict['vehicle_count'] = vehicle_count or 0

            result.append(company_dict)

        return result

    @staticmethod
    def get_active_companies():
        """Returns active underwriters for select dropdowns."""
        InsuranceCompanyService.ensure_seeded()
        stmt = db.select(InsuranceCompany).where(
            InsuranceCompany.is_active == True
        ).order_by(InsuranceCompany.name.asc())
        companies = db.session.execute(stmt).scalars().all()
        return [company.to_dict() for company in companies]

    @staticmethod
    def get_company_by_id(company_id):
        try:
            company_id_int = int(company_id)
        except (ValueError, TypeError):
            return None
        company = db.session.get(InsuranceCompany, company_id_int)
        if company:
            return company.to_dict()
        return None

    @staticmethod
    def add_company(name, short_name="", code="", phone="", email="", commission_rate=10.0, contact_person="", is_active=True):
        try:
            comm_val = float(commission_rate)
        except (ValueError, TypeError):
            comm_val = 10.0

        company = InsuranceCompany(
            name=name.strip(),
            short_name=short_name.strip() if short_name else name.strip()[:10],
            code=code.strip().upper() if code else name.strip()[:3].upper(),
            phone=phone.strip(),
            email=email.strip(),
            commission_rate=comm_val,
            contact_person=contact_person.strip(),
            is_active=bool(is_active),
            created_at=_utcnow(),
            updated_at=_utcnow()
        )

        db.session.add(company)
        try:
            db.session.commit()
            return company.to_dict()
        except Exception:
            db.session.rollback()
            return None

    @staticmethod
    def update_company(company_id, name, short_name="", code="", phone="", email="", commission_rate=10.0, contact_person="", is_active=True):
        try:
            company_id_int = int(company_id)
        except (ValueError, TypeError):
            return False, "Invalid company ID"

        try:
            comm_val = float(commission_rate)
        except (ValueError, TypeError):
            comm_val = 10.0

        company = db.session.get(InsuranceCompany, company_id_int)
        if not company:
            return False, "Company not found"

        company.name = name.strip()
        company.short_name = short_name.strip() if short_name else company.name[:10]
        company.code = code.strip().upper() if code else company.name[:3].upper()
        company.phone = phone.strip()
        company.email = email.strip()
        company.commission_rate = comm_val
        company.contact_person = contact_person.strip()
        company.is_active = bool(is_active)
        company.updated_at = _utcnow()

        try:
            db.session.commit()
            return True, None
        except Exception:
            db.session.rollback()
            return False, "Failed to update company"

    @staticmethod
    def toggle_status(company_id, is_active=None):
        try:
            company_id_int = int(company_id)
        except (ValueError, TypeError):
            return False, "Invalid company ID"

        company = db.session.get(InsuranceCompany, company_id_int)
        if not company:
            return False, "Company not found"

        new_status = not company.is_active if is_active is None else bool(is_active)
        company.is_active = new_status
        company.updated_at = _utcnow()

        try:
            db.session.commit()
            return True, new_status
        except Exception:
            db.session.rollback()
            return False, "Failed to toggle status"