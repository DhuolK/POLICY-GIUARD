import sys
import os
import datetime

# Add parent directory to path so we can import app
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import create_app
from app.services.auth_service import AuthService
from app.extensions import get_db
from bson import ObjectId

app = create_app()

def seed():
    with app.app_context():
        db = get_db()
        
        # Clear existing collections
        db.users.delete_many({})
        db.policies.delete_many({})
        db.claims.delete_many({})
        db.vehicles.delete_many({})
        db.payments.delete_many({})
        
        # ─── Users ──────────────────────────────────────────────
        print("Creating admin user...")
        admin_data, _ = AuthService.register('adminpolicyguard@gmail.com', 'admin123', 'James Mwangi', role='admin', phone='+254 712 000 001')
        admin_id = admin_data['_id'] if isinstance(admin_data['_id'], ObjectId) else ObjectId(admin_data['_id'])
        
        print("Creating worker users...")
        worker_data, _ = AuthService.register('worker@policyguard.co.ke', 'worker123', 'Grace Otieno', role='worker', phone='+254 712 000 002')
        worker_a_id = worker_data['_id'] if isinstance(worker_data['_id'], ObjectId) else ObjectId(worker_data['_id'])

        worker_b, _ = AuthService.register('worker2@policyguard.co.ke', 'worker123', 'Brian Kimani', role='worker', phone='+254 712 000 003')
        worker_b_id = worker_b['_id'] if isinstance(worker_b['_id'], ObjectId) else ObjectId(worker_b['_id'])

        # Clients are business records, NOT login identities.
        # Architectural boundary: only admin/worker accounts hold credentials.
        print("Creating client records (no login credentials)...")
        # Ownership is seeded explicitly. Seeding clients with no owner is what
        # originally made every worker's list empty: the scoping field simply did
        # not exist on any record.
        #   index 0-4 -> Worker A, 5-8 -> Worker B, 9 -> unassigned (admin pool)
        clients_info = [
            ('daniel@example.com',  'Daniel Kiprop',   '+254 722 100 001', 'CL-001', 'A004561234X'),
            ('amina@example.com',   'Amina Hassan',    '+254 733 200 002', 'CL-002', 'A005678910Y'),
            ('peter@example.com',   'Peter Onyango',   '+254 711 300 003', 'CL-003', 'A006789012Z'),
            ('faith@example.com',   'Faith Wanjiku',   '+254 700 400 004', 'CL-004', 'A007890123W'),
            ('omar@example.com',    'Omar Abdalla',    '+254 745 500 005', 'CL-005', 'A008901234V'),
            ('john.mwangi@example.com', 'John Mwangi', '+254 711 001 002', 'CL-006', 'A009123456B'),
            ('mercy.chep@example.com', 'Mercy Chepngetich', '+254 722 002 003', 'CL-007', 'A001234567C'),
            ('david.kamau@example.com', 'David Kamau', '+254 733 003 004', 'CL-008', 'A002345678D'),
            ('sarah.atieno@example.com', 'Sarah Atieno', '+254 700 004 005', 'CL-009', 'A003456789E'),
            ('joseph.wambua@example.com', 'Joseph Wambua', '+254 745 005 006', 'CL-010', 'A004567890F')
        ]
        client_owners = [
            worker_a_id, worker_a_id, worker_a_id, worker_a_id, worker_a_id,
            worker_b_id, worker_b_id, worker_b_id, worker_b_id,
            None,
        ]
        client_ids = []
        for idx, (email, name, phone, client_id, kra) in enumerate(clients_info):
            client_doc = {
                'email': email,
                'role': 'customer',
                'full_name': name,
                'phone': phone,
                'client_id': client_id,
                'kra_pin': kra,
                'created_by': admin_id,
                'assigned_worker_id': client_owners[idx],
                'status': 'active',
                'created_at': datetime.datetime.utcnow() - datetime.timedelta(days=90),
                'updated_at': datetime.datetime.utcnow() - datetime.timedelta(days=90)
                # NOTE: intentionally NO password_hash — customers do not log in.
            }
            result = db.users.insert_one(client_doc)
            client_ids.append(result.inserted_id)

        owner_of_client = {cid: client_owners[i] for i, cid in enumerate(client_ids)}
        
        # ─── Vehicles ──────────────────────────────────────────
        print("Creating vehicles...")
        vehicles_info = [
            (client_ids[0], 'KDA 123A', 'Toyota',   'Corolla',    2022, 'private'),
            (client_ids[0], 'KCB 456B', 'Isuzu',    'FRR',        2020, 'commercial'),
            (client_ids[1], 'KDE 789C', 'Mazda',    'Demio',      2021, 'private'),
            (client_ids[2], 'KAA 012D', 'Nissan',   'Note',       2023, 'private'),
            (client_ids[3], 'KBZ 345E', 'Toyota',   'HiAce',      2019, 'psv'),
            (client_ids[4], 'KCD 678F', 'Subaru',   'Forester',   2022, 'private'),
            (client_ids[5], 'KDC 901G', 'Honda',    'Fit',        2020, 'private'),
            (client_ids[6], 'KDB 234H', 'Toyota',   'Vitz',       2018, 'private'),
            (client_ids[7], 'KDD 567I', 'Mitsubishi', 'L200',     2021, 'commercial'),
            (client_ids[8], 'KDF 890J', 'Volkswagen', 'Golf',     2019, 'private'),
            (client_ids[9], 'KDG 123K', 'Toyota',   'Fielder',    2021, 'private'),
        ]
        vehicle_ids = []
        for owner, reg, make, model, year, vtype in vehicles_info:
            result = db.vehicles.insert_one({
                "owner_id": owner,
                "registration_number": reg,
                "make": make,
                "model": model,
                "year": year,
                "vehicle_type": vtype,
                "created_at": datetime.datetime.utcnow() - datetime.timedelta(days=80)
            })
            vehicle_ids.append(result.inserted_id)
        
        # ─── Policies ──────────────────────────────────────────
        print("Creating policies...")
        policies_info = [
            ('PG-2026-001', client_ids[0], vehicle_ids[0], 'comprehensive', 'Active',    45000, '2026-01-15', '2027-01-14'),
            ('PG-2026-002', client_ids[0], vehicle_ids[1], 'third_party',   'Active',    25000, '2026-03-01', '2027-02-28'),
            ('PG-2026-003', client_ids[1], vehicle_ids[2], 'comprehensive', 'Active',    38000, '2026-02-10', '2027-02-09'),
            ('PG-2026-004', client_ids[2], vehicle_ids[3], 'comprehensive', 'Suspended', 42000, '2025-11-01', '2026-10-31'),
            ('PG-2026-005', client_ids[3], vehicle_ids[4], 'psv',           'Active',    65000, '2026-04-01', '2027-03-31'),
            ('PG-2026-006', client_ids[4], vehicle_ids[5], 'comprehensive', 'Active',    50000, '2026-05-20', '2027-05-19'),
            ('PG-2026-007', client_ids[5], vehicle_ids[6], 'comprehensive', 'Active',    40000, '2026-06-01', '2027-05-31'),
            ('PG-2026-008', client_ids[6], vehicle_ids[7], 'third_party',   'Active',    15000, '2026-07-01', '2027-06-30'),
            ('PG-2026-009', client_ids[7], vehicle_ids[8], 'comprehensive', 'Active',    55000, '2026-01-20', '2027-01-19'),
            ('PG-2026-010', client_ids[8], vehicle_ids[9], 'comprehensive', 'Active',    48000, '2026-02-15', '2027-02-14'),
            ('PG-2026-011', client_ids[9], vehicle_ids[10], 'psv',          'Active',    70000, '2026-03-10', '2027-03-09'),
        ]
        policy_ids = []
        for pnum, cid, vid, ptype, status, premium, eff, exp in policies_info:
            result = db.policies.insert_one({
                "policy_number": pnum,
                "client_id": cid,
                "vehicle_id": vid,
                "policy_type": ptype,
                "status": status,
                "premium_amount": premium,
                "effective_date": eff,
                "expiry_date": exp,
                # A policy inherits the responsible worker from its client.
                "created_by": admin_id,
                "assigned_worker_id": owner_of_client.get(cid),
                "created_at": datetime.datetime.utcnow() - datetime.timedelta(days=60)
            })
            policy_ids.append(result.inserted_id)

        policy_owner = {}
        policy_client = {}
        for pid, info in zip(policy_ids, policies_info):
            policy_client[pid] = info[1]
            policy_owner[pid] = owner_of_client.get(info[1])
        
        # ─── Claims ────────────────────────────────────────────
        print("Creating claims...")
        claims_info = [
            {
                "claim_number": "CLM-2026-001",
                "policy_id": policy_ids[0],
                "client_id": client_ids[0],
                "status": "review",
                "insured_title": "Mr",
                "insured_first_name": "Daniel",
                "insured_last_name": "Kiprop",
                "insured_phone": "+254 722 100 001",
                "accident_vehicle_reg": "KDA 123A",
                "accident_vehicle_make_model": "Toyota Corolla",
                "accident_vehicle_year": 2022,
                "driver_name": "Daniel Kiprop",
                "driver_license_number": "DL-KE-0098765",
                "driver_experience_years": 8,
                "driver_purpose": "personal",
                "incident_date": "2026-06-28",
                "incident_time": "14:30",
                "incident_place": "Mombasa Road, near JKIA junction",
                "incident_speed": 60,
                "incident_visibility": "good",
                "incident_weather": "clear",
                "incident_road_surface": "tarmac_dry",
                "police_station_reported": "Embakasi Police Station",
                "police_report_date": "2026-06-28",
                "driver_statement": "I was driving along Mombasa Road towards the CBD when a matatu suddenly cut in front of me from the left lane. I braked hard but could not avoid contact with the rear of the matatu.",
                "created_at": datetime.datetime.utcnow() - datetime.timedelta(days=17),
                "updated_at": datetime.datetime.utcnow() - datetime.timedelta(days=15),
            },
            {
                "claim_number": "CLM-2026-002",
                "policy_id": policy_ids[2],
                "client_id": client_ids[1],
                "status": "submitted",
                "insured_title": "Mrs",
                "insured_first_name": "Amina",
                "insured_last_name": "Hassan",
                "insured_phone": "+254 733 200 002",
                "accident_vehicle_reg": "KDE 789C",
                "accident_vehicle_make_model": "Mazda Demio",
                "driver_name": "Amina Hassan",
                "driver_license_number": "DL-KE-0087654",
                "incident_date": "2026-07-10",
                "incident_time": "08:15",
                "incident_place": "Waiyaki Way, near Westlands roundabout",
                "incident_weather": "rain",
                "incident_road_surface": "tarmac_wet",
                "driver_statement": "The road was wet from morning rain. While approaching the Westlands roundabout, the car ahead stopped suddenly. I applied brakes but the car skidded and hit the rear bumper of the vehicle ahead.",
                "created_at": datetime.datetime.utcnow() - datetime.timedelta(days=5),
                "updated_at": datetime.datetime.utcnow() - datetime.timedelta(days=5),
            },
            {
                "claim_number": "CLM-2026-003",
                "policy_id": policy_ids[5],
                "client_id": client_ids[4],
                "status": "approved",
                "insured_title": "Mr",
                "insured_first_name": "Omar",
                "insured_last_name": "Abdalla",
                "accident_vehicle_reg": "KCD 678F",
                "accident_vehicle_make_model": "Subaru Forester",
                "driver_name": "Omar Abdalla",
                "incident_date": "2026-05-15",
                "incident_place": "Thika Superhighway, Kenyatta University exit",
                "garage_name": "AutoXpress Ltd",
                "garage_contact_person": "John Kamau",
                "garage_phone": "+254 720 555 123",
                "driver_statement": "A stray animal crossed the highway suddenly. I swerved to avoid it and hit the roadside barrier.",
                "created_at": datetime.datetime.utcnow() - datetime.timedelta(days=60),
                "updated_at": datetime.datetime.utcnow() - datetime.timedelta(days=30),
            }
        ]
        for claim_data in claims_info:
            # Claims inherit ownership from their parent policy.
            claim_data['created_by'] = admin_id
            claim_data['assigned_worker_id'] = policy_owner.get(claim_data['policy_id'])
            db.claims.insert_one(claim_data)
        
        # ─── Payments ──────────────────────────────────────────
        print("Creating payments...")
        payments_info = [
            (policy_ids[0], client_ids[0], 45000, 'paid',       'Annual Premium - PG-2026-001', datetime.datetime.utcnow() - datetime.timedelta(days=50)),
            (policy_ids[1], client_ids[0], 25000, 'paid',       'Annual Premium - PG-2026-002', datetime.datetime.utcnow() - datetime.timedelta(days=45)),
            (policy_ids[2], client_ids[1], 38000, 'paid',       'Annual Premium - PG-2026-003', datetime.datetime.utcnow() - datetime.timedelta(days=40)),
            (policy_ids[3], client_ids[2], 42000, 'overdue',    'Annual Premium - PG-2026-004', datetime.datetime.utcnow() - datetime.timedelta(days=30)),
            (policy_ids[4], client_ids[3], 65000, 'receivable', 'Annual Premium - PG-2026-005', datetime.datetime.utcnow() - datetime.timedelta(days=10)),
            (policy_ids[5], client_ids[4], 50000, 'paid',       'Annual Premium - PG-2026-006', datetime.datetime.utcnow() - datetime.timedelta(days=20)),
            (policy_ids[0], client_ids[0], 5000,  'paid',       'Endorsement Fee',              datetime.datetime.utcnow() - datetime.timedelta(days=5)),
            (policy_ids[6], client_ids[5], 40000, 'paid',       'Annual Premium - PG-2026-007', datetime.datetime.utcnow() - datetime.timedelta(days=55)),
            (policy_ids[6], client_ids[5], 8000,  'receivable', 'Endorsement Fee - PG-2026-007', datetime.datetime.utcnow() - datetime.timedelta(days=5)),
            (policy_ids[7], client_ids[6], 15000, 'overdue',    'Annual Premium - PG-2026-008', datetime.datetime.utcnow() - datetime.timedelta(days=25)),
            (policy_ids[8], client_ids[7], 25000, 'overdue',    'Installment 1 - PG-2026-009',  datetime.datetime.utcnow() - datetime.timedelta(days=15)),
            (policy_ids[8], client_ids[7], 30000, 'receivable', 'Installment 2 - PG-2026-009',  datetime.datetime.utcnow() - datetime.timedelta(days=5)),
            (policy_ids[9], client_ids[8], 48000, 'paid',       'Annual Premium - PG-2026-010', datetime.datetime.utcnow() - datetime.timedelta(days=65)),
            (policy_ids[10], client_ids[9], 35000, 'overdue',   'Installment 1 - PG-2026-011',  datetime.datetime.utcnow() - datetime.timedelta(days=35)),
            (policy_ids[10], client_ids[9], 35000, 'receivable', 'Installment 2 - PG-2026-011',  datetime.datetime.utcnow() - datetime.timedelta(days=20)),
        ]
        for pid, cid, amount, status, desc, pdate in payments_info:
            db.payments.insert_one({
                "policy_id": pid,
                "client_id": cid,
                "amount": amount,
                "status": status,
                "description": desc,
                "payment_date": pdate
            })
        
        print("\n[OK] Database seeded successfully!")
        print("   Login credentials (staff only - customers do not log in):")
        print("   Admin:    adminpolicyguard@gmail.com / admin123")
        print("   Worker A: worker@policyguard.co.ke   / worker123  (5 clients)")
        print("   Worker B: worker2@policyguard.co.ke  / worker123  (4 clients)")
        print("   1 client is left unassigned (admin-only pool).")

if __name__ == '__main__':
    seed()
