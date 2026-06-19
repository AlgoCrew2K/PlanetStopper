# TDD Handoff
Plan: feature-plans/dashboard-auth.md
Branch: feat/dashboard-auth
Phase: red

## Test Files
- `tests/app/test_dashboard_auth.py` — 35 tests (34 failing RED, 1 pre-existing GREEN guard)

## A/C Coverage Matrix

| A/C ID | Description | Test Class | Test Name(s) | Status |
|--------|-------------|-----------|--------------|--------|
| AC-1 | Unauthenticated HTML routes → 302 /login | TestUnauthenticatedAccess | test_unauthenticated_root_redirects_to_login, test_unauthenticated_ai_advisor_redirects_to_login | RED |
| AC-2 | Unauthenticated /api/* → 401 JSON | TestUnauthenticatedAccess | test_unauthenticated_api_route_returns_401_json, test_unauthenticated_api_route_returns_json_not_html, test_unauthenticated_xhr_request_returns_401 | RED |
| AC-3 | GET /login reachable pre-auth, form present, static assets reachable | TestUnauthenticatedAccess, TestStaticAssetsPreAuth | test_login_get_is_reachable_unauthenticated, test_login_page_contains_password_form, test_login_page_contains_form_post_action, test_static_css_reachable_unauthenticated | RED |
| AC-4 | Correct password → session set + redirect + subsequent access granted | TestLoginFlow | test_correct_password_sets_session_and_redirects, test_correct_password_subsequent_protected_access_granted | RED |
| AC-5 | Wrong password → generic error, no session, throttle increments | TestLoginFlow | test_wrong_password_returns_200_with_generic_error, test_wrong_password_does_not_set_session | RED |
| AC-6 | Constant-time compare; DASHBOARD_PASSWORD_HASH authenticates; no password in logs | TestSecurity, TestHashedPassword | test_constant_time_compare_function_used, test_password_not_written_to_logs_on_wrong_attempt, test_hashed_password_authenticates_successfully, test_hash_takes_precedence_over_plaintext | RED |
| AC-7 | CSRF on login POST; GET /login issues CSRF token pre-auth | TestSecurity | test_csrf_missing_on_login_post_rejected (GREEN guard), test_login_get_issues_csrf_token_pre_auth | RED (1 pre-GREEN) |
| AC-8 | Fail-closed on misconfig: no password → deny all; no SECRET_KEY → deny all | TestSecurity | test_fail_closed_missing_dashboard_password_denies_root, test_fail_closed_missing_password_login_cannot_succeed, test_fail_closed_missing_secret_key_denies_all, test_fail_closed_empty_secret_key_denies_all | RED |
| AC-9 | Throttle/lockout after N failures; reset on success | TestSecurity | test_throttle_increments_on_wrong_password, test_throttle_resets_on_successful_login | RED |
| AC-10 | Cookie flags: HttpOnly, SameSite=Lax, Secure when env set | TestCookieFlags | test_session_cookie_is_httponly, test_session_cookie_has_samesite_lax, test_session_cookie_secure_flag_when_env_set | RED |
| AC-11 | GET /logout clears session → redirect /login | TestLogout | test_logout_clears_session_and_redirects, test_after_logout_protected_route_redirects_again | RED |
| AC-12 | Existing write paths also require auth | TestUnauthenticatedAccess | test_unauthenticated_write_path_settings_denied | RED |
| AC-13 | Already-authenticated /login → redirect to dashboard | TestLoginFlow | test_already_authenticated_login_get_redirects_to_dashboard | RED |

### Structural/contract tests (supporting AC-1/AC-6/AC-8/AC-9):
- `test_resolve_dashboard_credential_exists` — asserts `_resolve_dashboard_credential` callable exists
- `test_auth_exempt_endpoints_constant_exists` — asserts `_AUTH_EXEMPT_ENDPOINTS` defined
- `test_auth_before_request_hook_registered` — asserts `_auth_before_request` registered in `before_request_funcs`

## Questions for User
None — plan is unambiguous. One design choice left to implementer: exact hash algorithm for
DASHBOARD_PASSWORD_HASH (plan says "werkzeug.security or equivalent"). The test for
`test_hashed_password_authenticates_successfully` uses sha256 hex as a placeholder hash value;
the implementer should use werkzeug's `check_password_hash` / `generate_password_hash` and
the test will need to be updated to use werkzeug-formatted hashes in the fixture.
Flag: test_writer will update test_hashed_password_authenticates_successfully and
test_hash_takes_precedence_over_plaintext during the review phase once the implementer
confirms the hash format used.

## Import Stubs Created
None required. All tests reference `app as app_module` (existing module). No new top-level
modules introduced — the auth gate is added to app.py directly.

## Notes for Implementer
- Run tests from the neutral-dir technique to bypass pyproject.toml xdist addopts:
  ```python
  python -c "import sys,os; os.chdir('C:/Windows/Temp'); sys.exit(__import__('pytest').main(['<worktree>/tests/app/test_dashboard_auth.py', '-v', '--override-ini=addopts=']))"
  ```
- `_csrf_check_enabled` is already a module-level bool in app.py; the test fixture
  `_disable_csrf_for_tests` disables it globally but `TestSecurity::test_csrf_missing_on_login_post_rejected`
  re-enables it via monkeypatch for CSRF verification.
- The one pre-existing GREEN test (`test_csrf_missing_on_login_post_rejected`) verifies
  the existing CSRF infra rejects login POSTs — must stay GREEN after implementation.
- `SESSION_COOKIE_HTTPONLY`, `SESSION_COOKIE_SAMESITE`, `SESSION_COOKIE_SECURE` must be
  set in `app.config` at startup (not per-request) for Flask to honour them in cookies.
- Read the plan's Architecture section for the exact before_request ordering requirement:
  auth BEFORE csrf (auth gate registered first).

## Status Log
- [2026-06-19] test-writer: Starting RED phase
- [2026-06-19] test-writer: RED complete — 35 tests (34 failing RED, 1 pre-existing GREEN guard on CSRF infra), 0 stubs created
