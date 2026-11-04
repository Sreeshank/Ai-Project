"""Supabase password authentication and private, live application roles.

Supabase validates all access tokens via /user. Neither JWT metadata nor any
browser-supplied role grants staff access. No shared-token fallback exists.
"""
import os
import uuid
from urllib.parse import urlsplit
from typing import Literal

import httpx
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

ACCESS_COOKIE = 'campus_access'
REFRESH_COOKIE = 'campus_refresh'


def configured():
    return bool(os.getenv('SUPABASE_URL') and public_key() and os.getenv('APP_ORIGIN'))


def public_key():
    return os.getenv('SUPABASE_PUBLISHABLE_KEY') or os.getenv('SUPABASE_ANON_KEY', '')


def check_origin(request):
    # Do not trust arbitrary forwarded Host headers for CSRF decisions.
    allowed = os.getenv('APP_ORIGIN', '').rstrip('/')
    if not allowed or request.headers.get('origin', '').rstrip('/') != allowed:
        raise HTTPException(403, 'This request must come from the campus website.')


def provider(method, path, *, token=None, body=None):
    if not configured():
        raise HTTPException(503, 'Staff sign-in is not configured. Contact your administrator.')
    url = os.environ['SUPABASE_URL'].rstrip('/')
    parsed = urlsplit(url)
    if parsed.scheme != 'https' or not parsed.netloc or parsed.path not in ('', '/'):
        raise HTTPException(503, 'Staff sign-in configuration is invalid.')
    headers = {'apikey': public_key()}
    if token:
        headers['Authorization'] = 'Bearer ' + token
    try:
        response = httpx.request(method, url + '/auth/v1/' + path, headers=headers,
                                json=body, timeout=10, follow_redirects=False)
    except httpx.HTTPError:
        raise HTTPException(503, 'The sign-in service is temporarily unavailable.') from None
    if response.status_code == 429:
        raise HTTPException(429, 'Too many sign-in attempts. Wait before trying again.', headers={'Retry-After': '60'})
    if response.status_code >= 500:
        raise HTTPException(503, 'The sign-in service is temporarily unavailable.')
    return response


def verified_user(access):
    result = provider('GET', 'user', token=access)
    if result.status_code != 200:
        if result.status_code in (401, 403):
            raise HTTPException(401, 'Your session has ended. Please sign in again.')
        raise HTTPException(503, 'The sign-in service could not verify this session.')
    try:
        user = result.json()
        user['id'] = str(uuid.UUID(user['id']))
        if not user.get('email_confirmed_at') or user.get('is_anonymous') or not user.get('email'):
            raise ValueError('Unconfirmed identity')
    except (ValueError, KeyError, TypeError):
        raise HTTPException(401, 'A confirmed staff email is required.') from None
    return user


def save_cookies(response, tokens):
    secure = os.getenv('APP_ORIGIN', '').startswith('https://')
    # The refresh cookie maintains the session after the access token expires.
    for name, value in ((ACCESS_COOKIE, tokens['access_token']), (REFRESH_COOKIE, tokens['refresh_token'])):
        response.set_cookie(name, value, max_age=28800, httponly=True,
                            secure=secure, samesite='lax', path='/api')


def clear_cookies(response):
    for name in (ACCESS_COOKIE, REFRESH_COOKIE):
        response.delete_cookie(name, path='/api', httponly=True,
                               secure=os.getenv('APP_ORIGIN', '').startswith('https://'), samesite='lax')


def validate_tokens(result):
    try:
        tokens = result.json()
        if not all(isinstance(tokens.get(key), str) and tokens[key] for key in ('access_token', 'refresh_token')):
            raise ValueError('Invalid session')
        return tokens
    except (ValueError, TypeError):
        raise HTTPException(503, 'The sign-in service returned an invalid session.') from None


def identity(request):
    if not configured():
        raise HTTPException(503, 'Staff sign-in is not configured. Contact your administrator.')
    authorization = request.headers.get('authorization')
    if authorization is not None:
        # CLI access uses a real Supabase JWT, never a shared application token.
        parts = authorization.split()
        if len(parts) != 2 or parts[0].lower() != 'bearer':
            raise HTTPException(401, 'Sign in to the staff workspace.')
        return verified_user(parts[1])
    access = request.cookies.get(ACCESS_COOKIE)
    refresh = request.cookies.get(REFRESH_COOKIE)
    if not access and not refresh:
        raise HTTPException(401, 'Sign in to the staff workspace.')
    if request.method not in ('GET', 'HEAD', 'OPTIONS'):
        check_origin(request)
    try:
        if access:
            return verified_user(access)
    except HTTPException as exc:
        if exc.status_code != 401:
            raise
    if not refresh:
        request.state.clear_auth = True
        raise HTTPException(401, 'Your session has ended. Please sign in again.')
    result = provider('POST', 'token?grant_type=refresh_token', body={'refresh_token': refresh})
    if result.status_code != 200:
        if result.status_code in (400, 401, 403):
            request.state.clear_auth = True
            raise HTTPException(401, 'Your session has ended. Please sign in again.')
        raise HTTPException(503, 'The sign-in service could not refresh this session.')
    tokens = validate_tokens(result)
    user = verified_user(tokens['access_token'])
    request.state.auth_tokens = tokens
    return user


def require_staff(request, connect):
    user = identity(request)
    with connect() as db:
        member = db.execute('SELECT role,enabled FROM staff_members WHERE user_id=?', (user['id'],)).fetchone()
    if member is None or not member['enabled']:
        request.state.clear_auth = True
        raise HTTPException(403, 'This account does not have active staff access. Contact an administrator.')
    return {'id': user['id'], 'email': user['email'], 'role': member['role']}


def require_admin(request, connect):
    user = require_staff(request, connect)
    if user['role'] != 'admin':
        raise HTTPException(403, 'An administrator account is required.')
    return user


def grant_member(db, email, role, enabled, actor_id=None, *, bootstrap=False):
    if getattr(db, 'dialect', '') != 'postgres':
        raise HTTPException(503, 'Account management requires Supabase PostgreSQL.')
    # Serializes grants/bootstrap and prevents concurrent last-admin demotions.
    db.execute("SELECT pg_advisory_xact_lock(hashtext('campus.staff_members'))")
    if actor_id:
        actor = db.execute('SELECT role,enabled FROM staff_members WHERE user_id=?', (actor_id,)).fetchone()
        if not actor or not actor['enabled'] or actor['role'] != 'admin':
            raise HTTPException(403, 'An administrator account is required.')
    if bootstrap and db.execute("SELECT 1 FROM staff_members WHERE role='admin' AND enabled=true").fetchone():
        raise HTTPException(409, 'An administrator already exists. Use the staff workspace to manage roles.')
    user = db.execute('SELECT id,email_confirmed_at FROM auth.users WHERE lower(email)=lower(?) AND deleted_at IS NULL', (email,)).fetchone()
    if not user or not user['email_confirmed_at']:
        raise HTTPException(422, 'Create and confirm this email in Supabase Authentication first, then grant staff access here.')
    target = str(user['id'])
    previous = db.execute('SELECT role,enabled FROM staff_members WHERE user_id=?', (target,)).fetchone()
    if previous and previous['role'] == 'admin' and previous['enabled'] and (role != 'admin' or not enabled):
        count = db.execute("SELECT count(*) FROM staff_members WHERE role='admin' AND enabled=true").fetchone()[0]
        if count <= 1:
            raise HTTPException(409, 'Keep at least one active administrator.')
    db.execute("""INSERT INTO staff_members(user_id,role,enabled,updated_by) VALUES(?,?,?,?)
        ON CONFLICT(user_id) DO UPDATE SET role=excluded.role,enabled=excluded.enabled,
        updated_by=excluded.updated_by,updated_at=CURRENT_TIMESTAMP""", (target,role,enabled,actor_id))
    return {'id': target, 'role': role, 'enabled': enabled}


class Login(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=1, max_length=1024)


class PasswordChange(BaseModel):
    password: str = Field(min_length=12, max_length=128)


class MemberGrant(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    role: Literal['admin', 'staff']
    enabled: bool = True


def router(connect):
    routes = APIRouter()

    @routes.get('/api/auth/config')
    def configuration():
        return {'configured': configured()}

    @routes.post('/api/auth/login')
    def login(data: Login, request: Request):
        check_origin(request)
        result = provider('POST', 'token?grant_type=password', body={'email': data.email.strip(), 'password': data.password})
        if result.status_code != 200:
            if result.status_code in (400, 401, 403, 422):
                raise HTTPException(401, 'Sign-in failed. Check your email, password and email confirmation.')
            raise HTTPException(503, 'The sign-in service could not complete this request.')
        tokens = validate_tokens(result)
        user = verified_user(tokens['access_token'])
        with connect() as db:
            member = db.execute('SELECT role,enabled FROM staff_members WHERE user_id=?', (user['id'],)).fetchone()
        if not member or not member['enabled']:
            raise HTTPException(403, 'This account does not have active staff access. Contact an administrator.')
        request.state.auth_tokens = tokens
        return {'id': user['id'], 'email': user['email'], 'role': member['role']}

    @routes.get('/api/auth/session')
    def session(request: Request):
        return require_staff(request, connect)

    @routes.post('/api/auth/logout')
    def logout(request: Request):
        check_origin(request)
        request.state.clear_auth = True
        access = request.cookies.get(ACCESS_COOKIE)
        if access or request.cookies.get(REFRESH_COOKIE):
            try:
                identity(request)
            except HTTPException as exc:
                if exc.status_code == 401:
                    return {'signed_out': True}
                raise
            access = getattr(request.state, 'auth_tokens', {}).get('access_token') or access
            result = provider('POST', 'logout?scope=local', token=access)
            if result.status_code not in (200, 204, 401, 403):
                raise HTTPException(503, 'Signed out of this browser; the sign-in service could not revoke the remote session.')
        return {'signed_out': True}

    @routes.put('/api/auth/password')
    def password(data: PasswordChange, request: Request):
        require_staff(request, connect)
        access = getattr(request.state, 'auth_tokens', {}).get('access_token') or request.cookies.get(ACCESS_COOKIE)
        if request.headers.get('authorization'):
            access = request.headers['authorization'].split()[1]
        result = provider('PUT', 'user', token=access, body={'password': data.password})
        if result.status_code != 200:
            raise HTTPException(400, 'Password could not be updated. Check the Supabase password requirements and sign in again if necessary.')
        return {'updated': True}

    @routes.get('/api/admin/staff')
    def members(request: Request):
        require_admin(request, connect)
        with connect() as db:
            if getattr(db, 'dialect', '') != 'postgres':
                raise HTTPException(503, 'Account management requires Supabase PostgreSQL.')
            rows = db.execute("""SELECT s.user_id AS id,u.email,s.role,s.enabled,s.updated_at
                FROM staff_members s JOIN auth.users u ON u.id=s.user_id ORDER BY lower(u.email)""").fetchall()
        return [dict(row) for row in rows]

    @routes.put('/api/admin/staff')
    def grant(data: MemberGrant, request: Request):
        actor = require_admin(request, connect)
        with connect() as db:
            return grant_member(db, data.email.strip(), data.role, data.enabled, actor['id'])

    return routes
