import base64
import json

from odoo import fields, http
from odoo.exceptions import AccessError
from odoo.http import request


class GoogleReviewsOAuthController(http.Controller):
    @http.route(
        "/google_reviews/oauth/callback",
        type="http",
        auth="user",
        methods=["GET"],
        csrf=False,
    )
    def callback(self, **kwargs):
        # The n8n broker returns token data in the URL fragment so it is never
        # sent to Odoo/nginx access logs. This same-origin page saves it by RPC.
        body = """<!doctype html><html><head><meta charset='utf-8'><title>Google Reviews</title></head>
<body><main style='font:16px sans-serif;max-width:600px;margin:4rem auto'>
<h2 id='status'>Finishing Google authorization…</h2><p>You can close this window when complete.</p>
<script>
const statusEl = document.getElementById('status');
const params = new URLSearchParams(location.hash.slice(1));
history.replaceState(null, '', location.pathname);
const state = params.get('state');
const error = params.get('error');
if (error) { statusEl.textContent = 'Google authorization failed: ' + error; }
else if (!state || !params.get('refresh_token')) { statusEl.textContent = 'Google did not return a refresh token.'; }
else fetch('/google_reviews/oauth/complete', {method:'POST', credentials:'same-origin',
  headers:{'Content-Type':'application/json'},
  body:JSON.stringify({state, refresh_token:params.get('refresh_token'),
    access_token:params.get('access_token'), expires_in:params.get('expires_in')})})
  .then(r => r.json()).then(data => { statusEl.textContent = data.ok ?
    (data.sync_error ? 'Google connected, but sync needs attention: ' + data.sync_error : 'Google connected and reviews synced. Returning to Odoo…') : (data.error || 'Could not save Google connection.');
    if (data.ok) setTimeout(() => window.close(), 700); })
  .catch(() => { statusEl.textContent = 'Could not save Google connection. Please retry.'; });
</script></main></body></html>"""
        return request.make_response(
            body,
            headers=[("Content-Type", "text/html; charset=utf-8"), ("Cache-Control", "no-store")],
        )

    @http.route(
        "/google_reviews/oauth/complete",
        type="http",
        auth="user",
        methods=["POST"],
        csrf=False,
    )
    def complete(self, **kwargs):
        data = request.httprequest.get_json(silent=True) or {}
        state = data.get("state")
        refresh_token = data.get("refresh_token")
        if not state or not refresh_token:
            return request.make_json_response({"ok": False, "error": "Missing OAuth token data."}, status=400)
        try:
            padded = state + "=" * (-len(state) % 4)
            state_data = json.loads(base64.urlsafe_b64decode(padded.encode()).decode())
            connection = request.env["google.reviews.connection"].browse(
                int(state_data["connection_id"])
            ).exists()
        except (ValueError, KeyError, TypeError, json.JSONDecodeError):
            return request.make_json_response({"ok": False, "error": "Invalid OAuth state."}, status=400)
        if not connection or connection.pending_state != state:
            return request.make_json_response({"ok": False, "error": "Authorization expired. Retry."}, status=400)
        expected = request.env["ir.config_parameter"].sudo().get_param(
            "web.base.url", "http://localhost:8069"
        ).rstrip("/") + "/google_reviews/oauth/callback"
        if state_data.get("return_url") != expected:
            return request.make_json_response({"ok": False, "error": "Odoo callback mismatch."}, status=400)
        try:
            connection.check_access_rights("write")
            connection.check_access_rule("write")
        except AccessError:
            return request.make_json_response({"ok": False, "error": "Not allowed to connect this record."}, status=403)
        try:
            expires_in = int(data.get("expires_in") or 3600)
        except (ValueError, TypeError):
            expires_in = 3600
        connection.write({
            "refresh_token": refresh_token,
            "access_token": data.get("access_token") or False,
            "token_expiry": fields.Datetime.add(fields.Datetime.now(), seconds=expires_in),
            "pending_state": False,
        })
        sync_error = False
        try:
            connection.action_fetch_business_data()
        except Exception as exc:
            sync_error = str(exc)[:300]
            connection.last_sync_error = sync_error
        return request.make_json_response({"ok": True, "sync_error": sync_error})
