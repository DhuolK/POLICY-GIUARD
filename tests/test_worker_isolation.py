"""
Worker isolation / RBAC end-to-end tests (audit §20 matrix).

Runs against a DEDICATED test database (policy_guard_isolation_test) so the
dev database is never touched. Fixtures are seeded fresh in setUpClass:

    Admin                -> sees everything
    Worker A             -> exactly 5 clients (+ their vehicles/policies/claims)
    Worker B             -> exactly 4 clients (+ theirs)
    1 unassigned client   -> visible to admin only

Every cross-account attempt (F1-F8) must return 403/404 and never leak data,
and authorization failures must never masquerade as an empty 200 list.
"""
import os
import sys
import unittest
from datetime import datetime

from bson import ObjectId

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import create_app
from app.extensions import get_db, init_db
from app.services.auth_service import AuthService

TEST_DB_NAME = 'policy_guard_isolation_test'

ADMIN = {'email': 'iso-admin@test.ke', 'password': 'admin123', 'name': 'Iso Admin'}
WORKER_A = {'email': 'iso-a@test.ke', 'password': 'worker123', 'name': 'Worker A'}
WORKER_B = {'email': 'iso-b@test.ke', 'password': 'worker123', 'name': 'Worker B'}


class WorkerIsolationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = create_app('testing')
        # Rebind the global mongo handle to an isolated throwaway DB.
        cls.app.config['MONGO_DB_NAME'] = TEST_DB_NAME
        init_db(cls.app)
        # NOTE: deliberately NO ambient app_context here. Holding one open makes
        # Flask-Login bind the last request's identity to that context, so later
        # cookie-less test clients would inherit it (identity leakage between
        # simulated users). Services read the DB handle from module state, so
        # fixture seeding below needs no app context.

        db = get_db()
        db.users.delete_many({})
        db.vehicles.delete_many({})
        db.policies.delete_many({})
        db.claims.delete_many({})
        db.payments.delete_many({})

        # Staff
        admin, err = AuthService.register(ADMIN['email'], ADMIN['password'], ADMIN['name'], role='admin')
        assert not err, f"admin fixture failed: {err}"
        worker_a, err = AuthService.register(WORKER_A['email'], WORKER_A['password'], WORKER_A['name'], role='worker')
        assert not err, f"worker A fixture failed: {err}"
        worker_b, err = AuthService.register(WORKER_B['email'], WORKER_B['password'], WORKER_B['name'], role='worker')
        assert not err, f"worker B fixture failed: {err}"

        cls.admin_id = str(admin['_id'])
        cls.worker_a_id = str(worker_a['_id'])
        cls.worker_b_id = str(worker_b['_id'])

        # Clients: 5 -> A, 4 -> B, 1 -> unassigned
        cls.clients_a, cls.clients_b, cls.client_unassigned = [], [], None
        for i in range(10):
            if i < 5:
                owner, bucket = cls.worker_a_id, cls.clients_a
            elif i < 9:
                owner, bucket = cls.worker_b_id, cls.clients_b
            else:
                owner, bucket = None, None
            doc = {
                'email': f'iso-client-{i}@test.ke',
                'role': 'customer',
                'full_name': f'Iso Client {i:02d}',
                'phone': f'+254 700 000 00{i}',
                'client_id': f'ISO-C{i:03d}',
                'kra_pin': f'A{i:09d}Z',
                'created_by': ObjectId(cls.admin_id),
                'assigned_worker_id': ObjectId(owner) if owner else None,
                'status': 'active',
                'created_at': datetime.utcnow(),
            }
            cid = db.users.insert_one(doc).inserted_id
            if i < 5:
                cls.clients_a.append(cid)
            elif i < 9:
                cls.clients_b.append(cid)
            else:
                cls.client_unassigned = cid

        def make_child_fixtures(client_oid):
            vid = db.vehicles.insert_one({
                'owner_id': client_oid,
                'registration_number': f'ISO {str(client_oid)[-6:].upper()}',
                'make': 'Toyota', 'model': 'Corolla', 'year': 2022,
                'vehicle_type': 'private', 'created_at': datetime.utcnow(),
            }).inserted_id
            pid = db.policies.insert_one({
                'policy_number': f'ISO-P-{str(client_oid)[-6:]}',
                'client_id': client_oid,
                'vehicle_id': vid,
                'policy_type': 'comprehensive',
                'status': 'published',
                'premium_amount': 10000,
                'effective_date': '2026-01-01', 'expiry_date': '2027-01-01',
                'created_by': ObjectId(cls.admin_id),
                'assigned_worker_id': client_oid and db.users.find_one({'_id': client_oid}).get('assigned_worker_id'),
                'created_at': datetime.utcnow(),
            }).inserted_id
            return vid, pid

        cls.policies_a, cls.policies_b = {}, {}
        for c in cls.clients_a:
            _, cls.policies_a[c] = make_child_fixtures(c)
        for c in cls.clients_b:
            _, cls.policies_b[c] = make_child_fixtures(c)

        cls.claim_b = db.claims.insert_one({
            'claim_number': 'ISO-CLM-B1',
            'policy_id': cls.policies_b[cls.clients_b[0]],
            'client_id': cls.clients_b[0],
            'status': 'submitted',
            'created_by': ObjectId(cls.admin_id),
            'assigned_worker_id': ObjectId(cls.worker_b_id),
            'created_at': datetime.utcnow(),
        }).inserted_id
        cls.claim_a = db.claims.insert_one({
            'claim_number': 'ISO-CLM-A1',
            'policy_id': cls.policies_a[cls.clients_a[0]],
            'client_id': cls.clients_a[0],
            'status': 'submitted',
            'created_by': ObjectId(cls.admin_id),
            'assigned_worker_id': ObjectId(cls.worker_a_id),
            'created_at': datetime.utcnow(),
        }).inserted_id

    @classmethod
    def tearDownClass(cls):
        db = get_db()
        db.client.drop_database(TEST_DB_NAME)

    # ─── helpers ────────────────────────────────────────────────
    def _login(self, email, password):
        client = self.app.test_client()
        resp = client.post('/login', data={'email': email, 'password': password},
                           follow_redirects=False)
        self.assertIn(resp.status_code, (302, 303), f"login failed for {email}: {resp.status_code}")
        return client

    def _view_detail_count(self, html):
        return html.count('View Details')

    # ─── §20: role visibility ───────────────────────────────────
    def test_admin_sees_all_10_clients(self):
        c = self._login(ADMIN['email'], ADMIN['password'])
        html = c.get('/clients/').get_data(as_text=True)
        self.assertEqual(self._view_detail_count(html), 10)

    def test_worker_a_sees_exactly_own_5(self):
        c = self._login(WORKER_A['email'], WORKER_A['password'])
        html = c.get('/clients/').get_data(as_text=True)
        self.assertEqual(self._view_detail_count(html), 5)
        self.assertIn('Iso Client 00', html)
        self.assertNotIn('Iso Client 05', html)          # B's client
        self.assertNotIn('ISO-C009', html)               # unassigned pool

    def test_worker_b_sees_exactly_own_4(self):
        c = self._login(WORKER_B['email'], WORKER_B['password'])
        html = c.get('/clients/').get_data(as_text=True)
        self.assertEqual(self._view_detail_count(html), 4)
        self.assertNotIn('Iso Client 00', html)

    def test_worker_policies_and_claims_scoped(self):
        ca = self._login(WORKER_A['email'], WORKER_A['password'])
        html = ca.get('/policies/').get_data(as_text=True)
        pol_a = get_db().policies.find_one({'_id': self.policies_a[self.clients_a[0]]})
        pol_b = get_db().policies.find_one({'_id': self.policies_b[self.clients_b[0]]})
        self.assertIn(pol_a['policy_number'], html)      # own policy visible
        self.assertNotIn(pol_b['policy_number'], html)   # B's policy invisible

        html = ca.get('/claims/').get_data(as_text=True)
        self.assertNotIn('ISO-CLM-B1', html)
        self.assertIn('ISO-CLM-A1', html)

    # ─── §20 / F-set: cross-account IDOR attempts ────────────────
    def test_f_read_worker_a_cannot_open_worker_b_client(self):
        c = self._login(WORKER_A['email'], WORKER_A['password'])
        resp = c.get(f"/clients/{self.clients_b[0]}")
        self.assertEqual(resp.status_code, 403)

    def test_f_read_worker_a_cannot_open_worker_b_policy(self):
        c = self._login(WORKER_A['email'], WORKER_A['password'])
        resp = c.get(f"/policies/{self.policies_b[self.clients_b[0]]}")
        self.assertEqual(resp.status_code, 403)

    def test_f_read_worker_a_cannot_open_worker_b_claim(self):
        c = self._login(WORKER_A['email'], WORKER_A['password'])
        resp = c.get(f"/claims/{self.claim_b}")
        self.assertEqual(resp.status_code, 403)

    def test_f_write_worker_a_cannot_add_vehicle_for_b_client(self):
        c = self._login(WORKER_A['email'], WORKER_A['password'])
        before = get_db().vehicles.count_documents({})
        resp = c.post('/vehicles/api/add', json={
            'owner_id': str(self.clients_b[0]),
            'registration_number': 'ISO HACK 1',
            'make': 'X', 'model': 'Y', 'year': 2024, 'vehicle_type': 'private',
        })
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(get_db().vehicles.count_documents({}), before, "vehicle was created cross-scope!")

    def test_f_write_worker_a_cannot_create_policy_for_b_client(self):
        c = self._login(WORKER_A['email'], WORKER_A['password'])
        before = get_db().policies.count_documents({})
        resp = c.post('/policies/new', data={
            'client_id': str(self.clients_b[0]),
            'reg_number': 'ISO HACK 2',
            'make': 'X', 'model': 'Y', 'year': '2024',
            'policy_type': 'comprehensive', 'premium': '5000',
            'effective_date': '2026-01-01', 'expiry_date': '2027-01-01',
        }, follow_redirects=False)
        self.assertEqual(resp.status_code, 403,
                         "policy creation against a foreign client must be rejected")
        self.assertEqual(get_db().policies.count_documents({}), before, "policy was created cross-scope!")
        self.assertEqual(get_db().vehicles.count_documents(
            {'registration_number': 'ISO HACK 2'}), 0, "orphan vehicle created cross-scope!")

    def test_f_worker_cannot_reassign_clients(self):
        c = self._login(WORKER_A['email'], WORKER_A['password'])
        resp = c.post(f"/clients/{self.clients_a[0]}/assign",
                      data={'worker_id': self.worker_b_id})
        self.assertEqual(resp.status_code, 403)
        doc = get_db().users.find_one({'_id': self.clients_a[0]})
        self.assertEqual(str(doc['assigned_worker_id']), self.worker_a_id)

    def test_f_worker_cannot_touch_b_policy_via_actions(self):
        c = self._login(WORKER_A['email'], WORKER_A['password'])
        target = str(self.policies_b[self.clients_b[0]])
        for path in (
            f"/policies/{target}/trigger-manual",
            f"/policies/{target}/extend",
            f"/policies/{target}/submit",
        ):
            resp = c.post(path, data={})
            # 403 = out-of-scope rejection; 404 = scoped lookup hides existence.
            self.assertIn(resp.status_code, (403, 404),
                          f"{path} did not reject cross-scope access")

    def test_f_ownership_fields_in_body_are_ignored(self):
        """A worker cannot forge ownership via request body fields."""
        c = self._login(WORKER_A['email'], WORKER_A['password'])
        resp = c.post('/policies/new', data={
            'client_id': str(self.clients_a[1]),
            'reg_number': 'ISO FORGE 1',
            'make': 'X', 'model': 'Y', 'year': '2024',
            'policy_type': 'comprehensive', 'premium': '5000',
            'effective_date': '2026-01-01', 'expiry_date': '2027-01-01',
            'assigned_worker_id': self.worker_b_id,      # forgery attempt
            'created_by': self.worker_b_id,              # forgery attempt
        })
        self.assertIn(resp.status_code, (200, 302, 303))
        pol = get_db().policies.find_one(
            {'client_id': self.clients_a[1], 'policy_type': 'comprehensive'},
            sort=[('_id', -1)])
        self.assertIsNotNone(pol, "expected the new policy to exist")
        self.assertEqual(
            str(pol['assigned_worker_id']), self.worker_a_id,
            "server must derive ownership from the client record, not the body")
        self.assertEqual(str(pol['created_by']), self.worker_a_id,
                         "created_by must be the true author, not a body-supplied id")

    # ─── vertical escalation & auth failures ─────────────────────
    def test_worker_blocked_from_admin_endpoints(self):
        c = self._login(WORKER_A['email'], WORKER_A['password'])
        self.assertEqual(c.get('/admin/users').status_code, 403)
        self.assertEqual(c.get('/admin/audit').status_code, 403)

    def test_worker_cannot_disable_accounts(self):
        c = self._login(WORKER_A['email'], WORKER_A['password'])
        resp = c.post(f"/admin/users/{self.worker_b_id}/set-active", data={'action': 'disable'})
        self.assertEqual(resp.status_code, 403)
        doc = get_db().users.find_one({'_id': ObjectId(self.worker_b_id)})
        self.assertFalse(doc.get('disabled'), "worker must not be able to disable accounts")

    def test_admin_can_disable_then_worker_login_fails(self):
        admin_c = self._login(ADMIN['email'], ADMIN['password'])
        resp = admin_c.post(f"/admin/users/{self.worker_b_id}/set-active",
                            data={'action': 'disable'})
        self.assertEqual(resp.status_code, 302)
        doc = get_db().users.find_one({'_id': ObjectId(self.worker_b_id)})
        self.assertTrue(doc.get('disabled'))

        # A disabled account cannot start a new session: the login POST
        # re-renders the sign-in form (200) instead of redirecting to /.
        fresh = self.app.test_client()
        resp = fresh.post('/login', data={'email': WORKER_B['email'],
                                          'password': WORKER_B['password']},
                          follow_redirects=False)
        self.assertEqual(resp.status_code, 200)
        self.assertIsNone(resp.headers.get('Location'),
                         "a disabled account must not be logged in / redirected")

        AuthService.set_disabled(self.worker_b_id, False)   # restore fixture

    def test_unauthenticated_never_gets_empty_200_list(self):
        c = self.app.test_client()
        for path in ('/clients/', '/policies/', '/claims/', '/vehicles/', '/'):
            resp = c.get(path, follow_redirects=False)
            self.assertIn(resp.status_code, (301, 302, 401),
                          f"{path} leaked a response without authentication")

    def test_admin_can_reassign_client(self):
        """§20: admins manage assignment — unassign pool -> worker B -> back."""
        c = self._login(ADMIN['email'], ADMIN['password'])
        # Unassigned pool client becomes Worker B's responsibility.
        resp = c.post(f"/clients/{self.client_unassigned}/assign",
                      data={'worker_id': self.worker_b_id})
        self.assertEqual(resp.status_code, 302)
        doc = get_db().users.find_one({'_id': self.client_unassigned})
        self.assertEqual(str(doc.get('assigned_worker_id')), self.worker_b_id)

        # Worker B can now open it; Worker A still cannot.
        cb = self._login(WORKER_B['email'], WORKER_B['password'])
        self.assertEqual(cb.get(f"/clients/{self.client_unassigned}").status_code, 200)
        ca = self._login(WORKER_A['email'], WORKER_A['password'])
        self.assertIn(ca.get(f"/clients/{self.client_unassigned}").status_code, (403, 404))

        # Handing back to the pool makes it invisible to workers again.
        resp = c.post(f"/clients/{self.client_unassigned}/assign", data={'worker_id': ''})
        self.assertEqual(resp.status_code, 302)
        doc = get_db().users.find_one({'_id': self.client_unassigned})
        self.assertIsNone(doc.get('assigned_worker_id'))
        self.assertEqual(
            cb.get(f"/clients/{self.client_unassigned}").status_code in (403, 404), True)

    def test_disabled_flag_blocks_authenticate(self):
        from app.services.auth_service import AuthService
        user, err = AuthService.set_disabled(self.worker_b_id, True)
        self.assertIsNone(err)
        result = AuthService.authenticate(WORKER_B['email'], WORKER_B['password'])
        user_obj = result[0] if isinstance(result, tuple) else result
        self.assertIsNone(user_obj, "authenticate must refuse a disabled account")
        AuthService.set_disabled(self.worker_b_id, False)
        result = AuthService.authenticate(WORKER_B['email'], WORKER_B['password'])
        user_obj = result[0] if isinstance(result, tuple) else result
        self.assertIsNotNone(user_obj)


if __name__ == '__main__':
    unittest.main()
