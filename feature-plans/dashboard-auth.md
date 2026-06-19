# Feature: Dashboard Password-Auth Gate
Status: ready
Created: 2026-06-19

## Summary
A single-password login gate protecting the ENTIRE Planet Stopper Flask surface — dashboard, AI Advisor SPA, and all `/api/*` routes — behind a Flask signed-session check, so the daemon can run on the public DigitalOcean droplet (`104.248.7.101`) without exposing anything to anyone who scrapes the IP. The password is sourced from an env attribute (plaintext `DASHBOARD_PASSWORD` or, preferred, a hashed `DASHBOARD_PASSWORD_HASH`), compared constant-time. Unauthenticated requests see ONLY the login page. The app **fails closed** on misconfiguration (missing password/secret → deny all, never serve open). Transport security (TLS reverse-proxy or localhost-bind + SSH tunnel) is the deployment companion, handled in the droplet deploy — NOT this feature.

## Acceptance Criteria
- [ ] AC-1: An unauthenticated request to any protected HTML route (`/`, `/ai-advisor`, …) returns **302 → `/login`**, never the protected content.
- [ ] AC-2: An unauthenticated request to any protected `/api/*` route (or any XHR/`fetch`) returns **401** (JSON), so the SPA JS can react — not the protected payload.
- [ ] AC-3: `GET /login` renders a minimal password form and is reachable WITHOUT auth; it is the ONLY page visible pre-auth (plus the static assets it references + a `/health` endpoint if one exists).
- [ ] AC-4: `POST /login` with the correct password clears+regenerates the session (anti-fixation), sets `session['authenticated']=True`, and redirects to the dashboard; subsequent requests in that session reach protected routes.
- [ ] AC-5: `POST /login` with a wrong password re-renders the login page with a GENERIC error ("Incorrect password"), sets no session, and increments the failed-attempt counter.
- [ ] AC-6: Password comparison is constant-time via `hmac.compare_digest`. If `DASHBOARD_PASSWORD_HASH` is set it takes precedence over `DASHBOARD_PASSWORD` (compare against the hash); the password is NEVER written to logs.
- [ ] AC-7: `POST /login` is CSRF-protected via the existing CSRF infra (`_validate_csrf`/`_csrf_before_request`); a login POST with missing/invalid CSRF token is rejected. The login GET issues a usable CSRF token pre-auth.
- [ ] AC-8: **Fail-closed on misconfig** — if the resolved password (plaintext or hash) is missing/empty OR `SECRET_KEY`/`FLASK_SECRET_KEY` is missing/empty, the app denies ALL requests and login can NEVER succeed; logged loudly at startup. Never fail open.
- [ ] AC-9: Failed-attempt throttle — after `_AUTH_MAX_ATTEMPTS` (default 5) consecutive failures from a client, further attempts are blocked for `_AUTH_LOCKOUT_SECONDS` (default 60), returning the login page with a "too many attempts" message (or 429). Reset on success.
- [ ] AC-10: Session cookie flags: `HttpOnly=True`, `SameSite='Lax'`, and `Secure=True` when `SESSION_COOKIE_SECURE` env is truthy (set behind TLS).
- [ ] AC-11: `GET /logout` clears the session and redirects to `/login`.
- [ ] AC-12: The two existing guarded write paths (`POST /api/settings`, `POST /api/symphony-settings/<name>`) retain their CSRF protection AND now also require auth (sit behind the gate). `LIVE_EXECUTION`/credentials remain excluded from the settings allowlist (unchanged).
- [ ] AC-13: An already-authenticated request to `/login` redirects to the dashboard (no re-prompt).

## Architecture
- **`app.py`:**
  - `app.secret_key` set at startup from `SECRET_KEY` (fallback `FLASK_SECRET_KEY`) env; if absent/empty → misconfig (fail-closed).
  - `_resolve_dashboard_credential()` → returns the configured hash or plaintext (hash preferred); if neither set → misconfig.
  - `_AUTH_EXEMPT_ENDPOINTS` — explicit minimal set: the login GET/POST endpoint(s), `static` (gated to only the login page's assets if feasible, else `static` broadly but the login page references nothing sensitive), and `health` if present.
  - `@app.before_request _auth_before_request()` — registered to run BEFORE `_csrf_before_request`. If misconfigured → deny all (500/maintenance for HTML, 503 for API) with a loud log. Else if `not session.get('authenticated')` and `request.endpoint not in _AUTH_EXEMPT_ENDPOINTS` → 302 `/login` (HTML) or 401 JSON (`/api/*`/XHR).
  - `login()` (`GET /login`): render `templates/login.html` with a CSRF token; if already authenticated → redirect to dashboard. `POST /login`: throttle-check → constant-time compare → on success `session.clear()`+`session['authenticated']=True`; on failure increment throttle + re-render with generic error.
  - `logout()` (`GET /logout`): `session.clear()` → redirect `/login`.
  - In-memory throttle: `dict[client_key] -> (fail_count, lockout_until_ts)`; `client_key` = remote addr (behind a proxy, honor a configured trusted `X-Forwarded-For` only if `TRUST_PROXY` set — else remote addr). Resets on success; resets on daemon restart (acceptable speed-bump).
  - `app.config`: `SESSION_COOKIE_HTTPONLY=True`, `SESSION_COOKIE_SAMESITE='Lax'`, `SESSION_COOKIE_SECURE=<env truthy>`.
- **`templates/login.html`:** minimal centered password form (POST `/login`, hidden CSRF field, error slot), styled with the EXISTING light card-UI CSS — do NOT introduce a dark/foreign theme (past UI-regression lesson). The only page rendered pre-auth.
- **`static/`:** reuse existing dashboard CSS; any asset the login page needs must be reachable pre-auth.
- **Env additions (documented for the droplet `.env`):** `DASHBOARD_PASSWORD` or `DASHBOARD_PASSWORD_HASH`; `SECRET_KEY` (random, required); optional `SESSION_COOKIE_SECURE`, `TRUST_PROXY`, `_AUTH_MAX_ATTEMPTS`, `_AUTH_LOCKOUT_SECONDS`.

## Design-System Mapping
The project declares NO formal design system — the dashboard is plain Flask + CSS, a **light card UI**. The login page reuses the existing dashboard stylesheet/conventions (card container, existing color/spacing tokens in the current CSS). Hard rule from a prior incident: the page MUST render in the established light theme — never inject a dark-themed or off-style element. A PM/ux visual gate confirms the rendered login page matches the dashboard's look.

## Edge Cases
- Missing/empty `DASHBOARD_PASSWORD`(+hash) OR `SECRET_KEY` → fail closed (deny all; loud startup log). Never open.
- Already-authenticated user hits `/login` → redirect to dashboard.
- Login page's own static assets must load pre-auth (else the form is broken/unstyled).
- `/api/*` + XHR unauthenticated → 401 JSON (SPA-friendly); HTML routes → 302 `/login`.
- CSRF token must be issuable to the UNAUTHENTICATED login GET (verify the CSRF infra issues a token pre-auth).
- Throttle must not lock out the legitimate operator unreasonably (sane default 5/60s); in-memory reset on restart is acceptable.
- Session cookie over plain HTTP is signed (integrity) but cleartext (confidentiality) → eavesdrop risk → mitigated by the TLS/tunnel deployment companion; `Secure` flag gated on `SESSION_COOKIE_SECURE`.
- before_request ordering: auth gate must not break the existing CSRF before_request; both must coexist (auth first, then CSRF on the login POST).

## Security Considerations
- **Constant-time compare** (`hmac.compare_digest`) — no timing leak.
- **No password in logs** — never log the submitted or configured secret.
- **Fail-closed** on misconfig — the dominant risk is accidentally serving the dashboard OPEN on a public IP; missing env MUST deny all.
- **Strong random `SECRET_KEY`** for session signing; fail-closed if absent; warn if it equals a known weak/default value.
- **Session fixation** prevented (clear+regenerate session on login).
- **Brute force** — failed-attempt throttle/lockout (AC-9).
- **CSRF** on login POST (reuse infra); **cookie flags** HttpOnly/SameSite/Secure.
- **No enumeration** — generic error; single shared password minimizes enumeration surface.
- **Complete coverage** — verify NO protected route is accidentally exempt; the exempt set is explicit + minimal; the existing write paths now require auth (AC-12).
- **Transport (out of scope, but MANDATORY companion):** password + session cookie travel cleartext over HTTP → the public droplet deploy MUST add TLS (Caddy/nginx + Let's Encrypt) or bind to localhost + SSH tunnel. Tracked in the droplet-deploy, not this feature.
- **DoS** — oversized login payloads bounded by Flask; throttle blunts rapid attempts.

## Testing Strategy
- **Unit/route tests** (`tests/app/test_dashboard_auth.py`), each via the Flask test client with `DB_PATH`/CSRF test fixtures:
  - unauthenticated protected HTML route → 302 `/login`; protected `/api/*` → 401.
  - `GET /login` unauthenticated → 200 + form.
  - correct password → session set + redirect + subsequent protected access granted.
  - wrong password → denied + generic error + no session + throttle increments.
  - constant-time path used (assert `hmac.compare_digest` is the comparison; spy/structure check).
  - CSRF missing/invalid on login POST → rejected.
  - fail-closed: missing `DASHBOARD_PASSWORD`(+hash) → all routes deny + login can't succeed; missing `SECRET_KEY` → same.
  - hashed-password form (`DASHBOARD_PASSWORD_HASH`) authenticates correctly; plaintext path also works; hash takes precedence.
  - throttle: N failures → lockout for cooldown; reset on success.
  - `/logout` clears session.
  - cookie flags HttpOnly + SameSite (Secure when env set).
  - already-authenticated `/login` → redirect.
  - login static assets reachable pre-auth.
  - existing write paths (`POST /api/settings`) unauthenticated → denied.
  - no password in logs (capture log output, assert absence).
- **`describe('security')` grouping** for the fail-closed/CSRF/constant-time/throttle/no-log tests.
- **Behavioral / PM live test** (the real gate): on the running daemon, GET `/` while logged out → redirected to `/login`; submit wrong password → denied; submit correct → dashboard renders; `/logout` → back to gate. Confirm an `/api/*` call while logged out → 401.
- **ux visual gate:** PM/ux-expert renders `/login` and confirms it matches the light card UI (not broken/unstyled, not dark-themed) — per the prior "actually look at the render" lesson.

## Decisions
| Decision | Rationale |
|----------|-----------|
| Single shared password, not per-user accounts | Single-operator use case; matches the operator's spec; minimal surface |
| Flask signed-session (cookie) gate, not HTTP Basic | Operator wants a login PAGE that's the only thing visible; better UX; logout support |
| Hash form (`DASHBOARD_PASSWORD_HASH`) preferred over plaintext | `.env` need not hold the plaintext password |
| In-memory throttle (no DB) | Single-process daemon; restart-reset is an acceptable brute-force speed-bump |
| Fail-closed on misconfig | The catastrophic failure mode is serving OPEN on a public IP — must never happen |
| `/api/*` → 401, HTML → 302 | SPA JS can handle 401 cleanly; browsers follow the 302 to the login page |
| TLS/tunnel deferred to the droplet deploy | Transport security is a deployment concern, not app logic; but MANDATORY companion |

## Scope Boundaries
- **IN:** `app.py` before_request auth gate + `/login` + `/logout` + credential resolution (plaintext/hash) + constant-time compare + failed-attempt throttle + session config/cookie flags + fail-closed behavior; `templates/login.html`; minimal login styling reusing the existing CSS; the full test suite above.
- **OUT:** per-user accounts/roles/permissions; password reset/recovery; email/2FA; TLS / reverse-proxy / SSH-tunnel (the droplet-deploy companion); rate-limiting beyond the basic throttle; the droplet deployment itself; any change to `LIVE_EXECUTION`/credential handling or the execution path.
