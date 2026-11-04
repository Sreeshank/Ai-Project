import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient
from backend import auth, main


class StaffAuthTests(unittest.TestCase):
    user_id = '22222222-2222-4222-8222-222222222222'
    origin = 'http://127.0.0.1:3000'

    def setUp(self):
        scratch = Path(__file__).resolve().parents[2] / 'work' / 'auth-test-data'
        scratch.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=scratch)
        self.env = patch.dict(os.environ, {'SUPABASE_URL': 'https://test.supabase.co', 'SUPABASE_PUBLISHABLE_KEY': 'public-key',
            'SUPABASE_ANON_KEY': '', 'APP_ORIGIN': self.origin, 'DOCUMENT_BACKEND': 'sqlite', 'DATABASE_URL': '',
            'GEMINI_API_KEY': '', 'RATE_LIMIT_ENABLED': '0', 'ADMIN_TOKEN': 'obsolete-secret'})
        self.env.start()
        self.db_patch = patch.object(main, 'DB', Path(self.temp.name)/'auth.db'); self.db_patch.start()
        self.data_patch = patch.object(main, 'DATA', Path(self.temp.name)); self.data_patch.start()
        self.client = TestClient(main.app); self.client.__enter__()
        self.calls = []
        self.user_status = 200
        self.refresh_status = 200
        self.login_status = 200
        self.user_metadata = {'role': 'admin'}
        self.provider_patch = patch.object(auth.httpx, 'request', side_effect=self.provider); self.provider_patch.start()
        self.role('staff')

    def tearDown(self):
        self.client.__exit__(None,None,None)
        self.provider_patch.stop(); self.data_patch.stop(); self.db_patch.stop(); self.env.stop(); self.temp.cleanup()

    def role(self, role, enabled=True):
        with main.connect() as db:
            db.execute('INSERT INTO staff_members(user_id,role,enabled) VALUES(?,?,?) ON CONFLICT(user_id) DO UPDATE SET role=excluded.role,enabled=excluded.enabled', (self.user_id,role,enabled))

    def provider(self, method, url, **kwargs):
        self.calls.append((method,url,kwargs))
        if url.endswith('/user') and method=='GET':
            status = self.user_status
            if kwargs['headers'].get('Authorization') == 'Bearer refreshed-access': status=200
            return httpx.Response(status,json={'id':self.user_id,'email':'staff@example.com',
                'email_confirmed_at':'2026-01-01', 'user_metadata': self.user_metadata})
        if 'grant_type=refresh_token' in url:
            return httpx.Response(self.refresh_status,json={'access_token':'refreshed-access','refresh_token':'refreshed-secret'})
        if 'grant_type=password' in url:
            return httpx.Response(self.login_status,json={'access_token':'initial-access','refresh_token':'initial-secret'})
        return httpx.Response(200,json={'id':self.user_id})

    def login(self, origin=None):
        return self.client.post('/api/auth/login', headers={'Origin':origin or self.origin}, json={'email':'staff@example.com','password':'test-password'})

    def test_login_sets_http_only_cookies_without_exposing_tokens(self):
        response=self.login()
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(response.json()['role'],'staff')
        self.assertNotIn('token',response.text)
        self.assertIn('HttpOnly',response.headers['set-cookie'])
        self.assertIn('SameSite=lax',response.headers['set-cookie'])
        self.assertIn('Path=/api',response.headers['set-cookie'])
        self.assertEqual(response.headers['cache-control'],'private, no-store')
        self.assertEqual(self.client.get('/api/admin/documents').status_code,200)

    def test_https_cookies_are_secure_and_host_only(self):
        with patch.dict(os.environ,{'APP_ORIGIN':'https://campus.example.edu'}):
            response=self.login('https://campus.example.edu')
        self.assertIn('Secure',response.headers['set-cookie'])
        self.assertNotIn('Domain=',response.headers['set-cookie'])

    def test_failed_login_does_not_set_a_session(self):
        self.login_status=400
        response=self.login()
        self.assertEqual(response.status_code,401)
        self.assertNotIn('set-cookie',response.headers)

    def test_disabled_or_unassigned_user_cannot_sign_in(self):
        self.role('staff',False)
        self.assertEqual(self.login().status_code,403)
        with main.connect() as db: db.execute('DELETE FROM staff_members')
        self.assertEqual(self.login().status_code,403)

    def test_metadata_cannot_grant_privileges_and_staff_cannot_manage_accounts(self):
        self.assertEqual(self.login().json()['role'],'staff')
        self.assertEqual(self.client.get('/api/admin/staff').status_code,403)
        self.assertEqual(self.client.put('/api/admin/staff',headers={'Origin':self.origin}, json={'email':'other@example.com','role':'admin'}).status_code,403)

    def test_role_revocation_and_demotion_take_effect_on_next_request(self):
        self.role('admin'); self.login()
        self.role('staff')
        self.assertEqual(self.client.get('/api/auth/session').json()['role'],'staff')
        self.assertEqual(self.client.get('/api/admin/staff').status_code,403)
        self.role('staff',False)
        response=self.client.get('/api/admin/documents')
        self.assertEqual(response.status_code,403)
        self.assertIn('Max-Age=0',response.headers['set-cookie'])

    def test_shared_admin_token_is_rejected_even_when_env_remains(self):
        self.assertEqual(self.client.get('/api/admin/documents',headers={'X-Admin-Token':'obsolete-secret'}).status_code,401)
        self.assertFalse(self.calls)

    def test_wrong_origin_and_missing_origin_are_rejected(self):
        self.assertEqual(self.login('https://evil.example').status_code,403)
        self.assertEqual(self.client.post('/api/auth/login',json={'email':'staff@example.com','password':'x'}).status_code,403)
        self.assertFalse(self.calls)
        self.login()
        self.assertEqual(self.client.post('/api/admin/search/reindex',headers={'Origin':'https://evil.example'}).status_code,403)
        self.assertEqual(self.client.post('/api/auth/logout',headers={'Origin':'https://evil.example'}).status_code,403)

    def test_valid_cookie_mutation_and_bearer_script_access(self):
        self.login()
        self.assertEqual(self.client.post('/api/admin/search/reindex',headers={'Origin':self.origin}).status_code,202)
        self.client.cookies.clear()
        self.assertEqual(self.client.post('/api/admin/search/reindex',headers={'Authorization':'Bearer valid-session'}).status_code,202)

    def test_invalid_bearer_never_falls_back_to_valid_cookie(self):
        self.login()
        self.user_status=401
        self.assertEqual(self.client.get('/api/admin/documents',headers={'Authorization':'Bearer forged'}).status_code,401)
        self.assertFalse(any('refresh_token' in call[1] for call in self.calls))

    def test_expired_access_is_refreshed_and_verified(self):
        self.login(); self.user_status=401
        response=self.client.get('/api/auth/session')
        self.assertEqual(response.status_code,200,response.text)
        self.assertIn('refreshed-access',response.headers['set-cookie'])
        self.assertTrue(any(call[2]['headers'].get('Authorization')=='Bearer refreshed-access' for call in self.calls))

    def test_revoked_refresh_clears_browser_session(self):
        self.login(); self.user_status=401; self.refresh_status=400
        response=self.client.get('/api/auth/session')
        self.assertEqual(response.status_code,401)
        self.assertIn('Max-Age=0',response.headers['set-cookie'])

    def test_outage_fails_closed_without_deleting_refresh_cookie(self):
        self.login()
        with patch.object(auth.httpx,'request',side_effect=httpx.ConnectError('private details')):
            response=self.client.get('/api/admin/documents')
        self.assertEqual(response.status_code,503)
        self.assertNotIn('private details',response.text)
        self.assertNotIn('set-cookie',response.headers)

    def test_logout_refreshes_expired_access_then_revokes_remote_session(self):
        self.login(); self.user_status=401
        response=self.client.post('/api/auth/logout',headers={'Origin':self.origin})
        self.assertEqual(response.status_code,200)
        logout=[call for call in self.calls if 'logout?' in call[1]][-1]
        self.assertEqual(logout[2]['headers']['Authorization'],'Bearer refreshed-access')
        self.assertIn('Max-Age=0',response.headers['set-cookie'])
        self.assertEqual(self.client.get('/api/admin/documents').status_code,401)

    def test_invalid_role_is_rejected_before_any_database_grant(self):
        self.role('admin'); self.login()
        response=self.client.put('/api/admin/staff',headers={'Origin':self.origin},json={'email':'other@example.com','role':'owner'})
        self.assertEqual(response.status_code,422)
        with main.connect() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM staff_members').fetchone()[0],1)

    def test_password_change_requires_session_origin_and_length(self):
        self.assertEqual(self.client.put('/api/auth/password',json={'password':'long-test-password'}).status_code,401)
        self.login()
        self.assertEqual(self.client.put('/api/auth/password',headers={'Origin':self.origin},json={'password':'short'}).status_code,422)
        self.assertEqual(self.client.put('/api/auth/password',headers={'Origin':'https://evil.example'},json={'password':'long-test-password'}).status_code,403)
        self.assertEqual(self.client.put('/api/auth/password',headers={'Origin':self.origin},json={'password':'long-test-password'}).status_code,200)

    def test_login_rate_limit_is_independent_of_public_chat(self):
        with patch.dict(os.environ,{'RATE_LIMIT_ENABLED':'1','AUTH_REQUESTS_PER_MINUTE':'1'}):
            from backend import operations
            with patch.object(operations,'limits',operations.RequestLimits()):
                self.assertEqual(self.login().status_code,200)
                response=self.login()
                self.assertEqual(response.status_code,429)
                self.assertIn('Retry-After',response.headers)
                self.assertEqual(self.client.get('/api/documents').status_code,200)


@unittest.skipUnless(os.getenv('TEST_DATABASE_URL'), 'Set TEST_DATABASE_URL for real role/RLS integration checks')
class StaffRolePostgresTests(unittest.TestCase):
    def test_role_grants_bootstrap_revocation_last_admin_and_private_table(self):
        import uuid
        from fastapi import HTTPException
        from backend import database
        class Rollback(Exception): pass
        with patch.dict(os.environ,{'DATABASE_URL':os.environ['TEST_DATABASE_URL']}):
            with self.assertRaises(Rollback), database.postgres_connection() as db:
                self.assertTrue(db.execute("SELECT relrowsecurity FROM pg_class WHERE oid='campus.staff_members'::regclass").fetchone()[0])
                self.assertFalse(db.execute("SELECT has_table_privilege('authenticated','campus.staff_members','SELECT')").fetchone()[0])
                self.assertFalse(db.execute("SELECT has_table_privilege('anon','campus.staff_members','INSERT')").fetchone()[0])
                # The temporary table shadows live roles only inside this transaction.
                db.execute('CREATE TEMP TABLE staff_members (LIKE campus.staff_members INCLUDING ALL) ON COMMIT DROP')
                emails=[]; ids=[]
                for _ in range(2):
                    user_id=str(uuid.uuid4()); email=user_id+'@example.invalid'
                    db.execute('INSERT INTO auth.users(id,email,email_confirmed_at) VALUES(?,?,CURRENT_TIMESTAMP)',(user_id,email))
                    emails.append(email);ids.append(user_id)
                auth.grant_member(db,emails[0],'admin',True,bootstrap=True)
                with self.assertRaises(HTTPException) as exc: auth.grant_member(db,emails[1],'admin',True,bootstrap=True)
                self.assertEqual(exc.exception.status_code,409)
                with self.assertRaises(HTTPException) as exc: auth.grant_member(db,emails[0],'staff',True,ids[0])
                self.assertEqual(exc.exception.status_code,409)
                auth.grant_member(db,emails[1],'admin',True,ids[0])
                auth.grant_member(db,emails[0],'staff',True,ids[1])
                with self.assertRaises(HTTPException) as exc: auth.grant_member(db,emails[1],'staff',True,ids[0])
                self.assertEqual(exc.exception.status_code,403)
                auth.grant_member(db,emails[0],'staff',False,ids[1])
                self.assertFalse(db.execute('SELECT enabled FROM staff_members WHERE user_id=?',(ids[0],)).fetchone()[0])
                with self.assertRaises(HTTPException) as exc: auth.grant_member(db,'missing@example.invalid','staff',True,ids[1])
                self.assertEqual(exc.exception.status_code,422)
                raise Rollback()
