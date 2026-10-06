import base64
from collections import defaultdict
from datetime import date, timedelta
import json
import secrets
from urllib.parse import urlencode

import requests
from dateutil.parser import isoparse

from odoo import _, api, fields, models
from odoo.exceptions import UserError

GOOGLE_CALLBACK_URL = "https://solution.ecube.pk/webhook/google-callback"
GOOGLE_TOKEN_REFRESH_URL = "https://solution.ecube.pk/webhook/google-token-refresh"


class GoogleReviewsConnection(models.Model):
    _name = "google.reviews.connection"
    _description = "Google Business Profile Connection"
    _order = "id desc"

    name = fields.Char(required=True, default="Google Business Profile")
    company_id = fields.Many2one(
        "res.company", required=True, default=lambda self: self.env.company
    )
    client_id = fields.Char(
        required=True,
        default="220160292834-8ita420bi6o33trtu1fs4qvagqgojk07.apps.googleusercontent.com",
        readonly=True,
        groups="base.group_system",
    )
    redirect_uri = fields.Char(
        required=True,
        default="https://solution.ecube.pk/webhook/google-callback",
        groups="base.group_system",
    )
    scope = fields.Char(
        required=True,
        default="https://www.googleapis.com/auth/business.manage",
        groups="base.group_system",
    )
    refresh_token = fields.Char(copy=False, groups="base.group_system")
    access_token = fields.Char(copy=False, groups="base.group_system")
    token_expiry = fields.Datetime(copy=False, groups="base.group_system")
    pending_state = fields.Char(copy=False, groups="base.group_system")
    google_account_id = fields.Char(groups="base.group_system")
    location_id = fields.Char(groups="base.group_system")
    google_accounts_json = fields.Text(copy=False, groups="base.group_system")
    google_locations_json = fields.Text(copy=False, groups="base.group_system")
    last_sync = fields.Datetime(readonly=True)
    last_sync_error = fields.Char(readonly=True)
    performance_json = fields.Text(copy=False, groups="base.group_system")
    performance_last_sync = fields.Datetime(readonly=True)
    performance_error = fields.Char(readonly=True)
    review_count = fields.Integer(compute="_compute_review_stats")
    average_rating = fields.Float(compute="_compute_review_stats", digits=(3, 2))
    pending_reply_count = fields.Integer(compute="_compute_review_stats")
    connected = fields.Boolean(compute="_compute_connected")
    review_ids = fields.One2many("google.business.review", "connection_id")

    @api.depends("refresh_token")
    def _compute_connected(self):
        for record in self:
            record.connected = bool(record.refresh_token)

    @api.depends("review_ids.star_rating", "review_ids.reply")
    def _compute_review_stats(self):
        for record in self:
            reviews = record.review_ids
            ratings = [int(review.star_rating) for review in reviews if review.star_rating]
            record.review_count = len(reviews)
            record.average_rating = sum(ratings) / len(ratings) if ratings else 0.0
            record.pending_reply_count = len(reviews.filtered(lambda review: not review.reply))

    def action_connect_google(self):
        self.ensure_one()
        # Existing connections may have the old test callback saved.
        if self.redirect_uri != GOOGLE_CALLBACK_URL:
            self.redirect_uri = GOOGLE_CALLBACK_URL
        self._check_google_settings()
        base_url = self.env["ir.config_parameter"].sudo().get_param(
            "web.base.url", "http://localhost:8069"
        ).rstrip("/")
        state_payload = {
            "connection_id": self.id,
            "nonce": secrets.token_urlsafe(32),
            "return_url": base_url + "/google_reviews/oauth/callback",
        }
        state = base64.urlsafe_b64encode(
            json.dumps(state_payload, separators=(",", ":")).encode()
        ).rstrip(b"=").decode()
        self.pending_state = state
        query = urlencode(
            {
                "client_id": self.client_id,
                "redirect_uri": self.redirect_uri,
                "response_type": "code",
                "scope": self.scope,
                "access_type": "offline",
                "prompt": "consent",
                "state": state,
            }
        )
        return {
            "type": "ir.actions.client",
            "tag": "google_reviews.oauth_popup",
            "params": {
                "url": "https://accounts.google.com/o/oauth2/v2/auth?" + query,
            },
        }

    def _check_google_settings(self):
        self.ensure_one()
        if not self.client_id or not self.redirect_uri:
            raise UserError(_("The Google OAuth client ID and callback URL are not configured."))
        if not self.redirect_uri.startswith("https://"):
            raise UserError(_("The Google callback URL must use HTTPS."))

    def _get_access_token(self):
        self.ensure_one()
        self._check_google_settings()
        if not self.refresh_token:
            raise UserError(_("Connect a Google account first."))
        if (
            self.access_token
            and self.token_expiry
            and self.token_expiry > fields.Datetime.add(fields.Datetime.now(), minutes=2)
        ):
            return self.access_token
        response = requests.post(
            GOOGLE_TOKEN_REFRESH_URL,
            json={"refresh_token": self.refresh_token},
            timeout=30,
        )
        if not response.ok:
            raise UserError(_("Google token refresh broker failed: %s") % response.text[:500])
        payload = response.json()
        self.write(
            {
                "access_token": payload["access_token"],
                "token_expiry": fields.Datetime.add(
                    fields.Datetime.now(), seconds=payload.get("expires_in", 3600)
                ),
            }
        )
        return payload["access_token"]

    def _google_get(self, url, params=None):
        token = self._get_access_token()
        response = requests.get(
            url,
            params=params,
            headers={"Authorization": "Bearer " + token},
            timeout=30,
        )
        if not response.ok:
            raise UserError(_("Google Business Profile API error: %s") % response.text[:500])
        return response.json()

    def action_load_accounts(self):
        return self.action_fetch_business_data()

    def action_load_locations(self):
        return self.action_fetch_business_data()

    def _google_get_all_pages(self, url, params, result_key):
        results = []
        page_token = False
        seen_tokens = set()
        page_count = 0
        while True:
            if page_token:
                if page_token in seen_tokens:
                    raise UserError(_("Google repeated a page token while loading %s.") % result_key)
                seen_tokens.add(page_token)
            page_count += 1
            if page_count > 100:
                raise UserError(_("Google returned more than 100 pages of %s; sync stopped safely.") % result_key)
            page_params = dict(params or {})
            if page_token:
                page_params["pageToken"] = page_token
            payload = self._google_get(url, page_params)
            results.extend(payload.get(result_key, []))
            page_token = payload.get("nextPageToken")
            if not page_token:
                return results

    def action_fetch_business_data(self):
        """Fetch all accessible accounts, locations, and reviews after OAuth."""
        self.ensure_one()
        accounts = self._google_get_all_pages(
            "https://mybusinessaccountmanagement.googleapis.com/v1/accounts",
            {"pageSize": 100},
            "accounts",
        )
        if not accounts:
            raise UserError(_("No Google Business Profile accounts were returned."))

        account_rows = []
        location_rows = []
        for account in accounts:
            account_name = account.get("name", "")
            account_id = account_name.rsplit("/", 1)[-1]
            account_rows.append({
                "id": account_id,
                "name": account.get("accountName") or account_name,
            })
            locations = self._google_get_all_pages(
                "https://mybusinessbusinessinformation.googleapis.com/v1/accounts/%s/locations"
                % account_id,
                {"readMask": "name,title,latlng,storefrontAddress", "pageSize": 100},
                "locations",
            )
            for location in locations:
                location_rows.append({
                    "account_id": account_id,
                    "location_id": location.get("name", "").rsplit("/", 1)[-1],
                    "name": location.get("title") or location.get("name"),
                    "resource_name": location.get("name"),
                    "latitude": (location.get("latlng") or {}).get("latitude"),
                    "longitude": (location.get("latlng") or {}).get("longitude"),
                    "address": ", ".join(filter(None, [
                        (location.get("storefrontAddress") or {}).get("locality"),
                        (location.get("storefrontAddress") or {}).get("administrativeArea"),
                        (location.get("storefrontAddress") or {}).get("regionCode"),
                    ])),
                })

        if not location_rows:
            raise UserError(_("No locations were returned for your Business Profile accounts."))
        self.write({
            "google_account_id": account_rows[0]["id"],
            "location_id": location_rows[0]["location_id"],
            "name": account_rows[0]["name"] or self.name,
            "google_accounts_json": json.dumps(account_rows),
            "google_locations_json": json.dumps(location_rows),
            "last_sync_error": False,
        })
        self.action_sync_reviews()
        return True

    @api.model
    def sync_location_map_data(self):
        """Refresh locations and coordinates using paginated Business Info calls."""
        updated = 0
        errors = []
        for connection in self.search([("refresh_token", "!=", False)], order="id"):
            locations = json.loads(connection.google_locations_json or "[]")
            if not locations:
                continue
            account_ids = {item.get("account_id") for item in locations if item.get("account_id")}
            if not account_ids and connection.google_account_id:
                account_ids = {connection.google_account_id}
            try:
                by_resource = {}
                for account_id in filter(None, account_ids):
                    rows = connection._google_get_all_pages(
                        "https://mybusinessbusinessinformation.googleapis.com/v1/accounts/%s/locations" % account_id,
                        {"readMask": "name,title,latlng,storefrontAddress", "pageSize": 100},
                        "locations",
                    )
                    for row in rows:
                        by_resource[row.get("name")] = row
                for item in locations:
                    row = by_resource.get(item.get("resource_name"))
                    if not row:
                        continue
                    coords = row.get("latlng") or {}
                    address = row.get("storefrontAddress") or {}
                    item.update({
                        "name": row.get("title") or item.get("name"),
                        "latitude": coords.get("latitude"),
                        "longitude": coords.get("longitude"),
                        "address": ", ".join(filter(None, [address.get("locality"), address.get("administrativeArea"), address.get("regionCode")])),
                    })
                    if coords.get("latitude") is not None and coords.get("longitude") is not None:
                        updated += 1
                connection.write({"google_locations_json": json.dumps(locations), "last_sync_error": False})
            except Exception as exc:
                errors.append("%s: %s" % (connection.name, str(exc)[:180]))
        return {"coordinates": updated, "errors": errors}

    def action_sync_reviews(self):
        self.ensure_one()
        locations = json.loads(self.google_locations_json or "[]")
        if not locations and self.google_account_id and self.location_id:
            locations = [{"account_id": self.google_account_id, "location_id": self.location_id}]
        if not locations:
            raise UserError(_("Connect Google and fetch locations before syncing reviews."))
        Review = self.env["google.business.review"]
        rating_values = {
            "ONE": "1",
            "TWO": "2",
            "THREE": "3",
            "FOUR": "4",
            "FIVE": "5",
        }
        for location in locations:
            url = "https://mybusiness.googleapis.com/v4/accounts/%s/locations/%s/reviews" % (
                location["account_id"], location["location_id"]
            )
            reviews = self._google_get_all_pages(url, {"pageSize": 50}, "reviews")
            for item in reviews:
                resource_name = item.get("name") or item.get("reviewId")
                if not resource_name:
                    continue
                if "/" not in resource_name:
                    resource_name = "%s/reviews/%s" % (
                        location.get("resource_name") or "accounts/%s/locations/%s" % (
                            location["account_id"], location["location_id"]
                        ),
                        resource_name,
                    )
                review = Review.search(
                    [("connection_id", "=", self.id), ("google_review_id", "=", resource_name)],
                    limit=1,
                )
                values = {
                    "connection_id": self.id,
                    "google_review_id": resource_name,
                    "google_location_id": location["location_id"],
                    "location_name": location.get("name"),
                    "reviewer_name": item.get("reviewer", {}).get("displayName"),
                    "reviewer_photo_url": item.get("reviewer", {}).get("profilePhotoUrl"),
                    "star_rating": rating_values.get(item.get("starRating")),
                    "comment": item.get("comment"),
                    "review_date": (
                        isoparse(item["createTime"]).strftime("%Y-%m-%d %H:%M:%S")
                        if item.get("createTime")
                        else False
                    ),
                    "reply": item.get("reviewReply", {}).get("comment"),
                }
                if review:
                    review.write(values)
                else:
                    Review.create(values)
        self.write({"last_sync": fields.Datetime.now(), "last_sync_error": False})
        return True

    def action_open_reviews(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Google Reviews"),
            "res_model": "google.business.review",
            "view_mode": "graph,pivot,list,form",
            "domain": [("connection_id", "=", self.id)],
            "context": {"default_connection_id": self.id},
        }

    def action_open_pending_reviews(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Reviews Needing a Reply"),
            "res_model": "google.business.review",
            "view_mode": "list,form",
            "domain": [("connection_id", "=", self.id), ("reply", "=", False)],
            "context": {"default_connection_id": self.id},
        }

    @api.model
    def get_dashboard_data(self, days=30, location_filter="all"):
        """Return review aggregates and cached GBP performance data for the dashboard."""
        try:
            days = min(max(int(days), 7), 90)
        except (TypeError, ValueError):
            days = 30
        connections = self.search([], order="id")
        start_date = date.today() - timedelta(days=days - 1)
        start_dt = fields.Datetime.to_datetime(start_date)
        connection_ids = connections.ids
        selected_connection_id = selected_location_id = False
        if location_filter and location_filter != "all":
            try:
                selected_connection_id, selected_location_id = location_filter.split(":", 1)
                selected_connection_id = int(selected_connection_id)
            except (ValueError, AttributeError):
                selected_connection_id = selected_location_id = False
            if selected_connection_id not in connection_ids or not selected_location_id:
                selected_connection_id = selected_location_id = False
        locations_by_connection = {}
        location_options = []
        for connection in connections:
            locations_by_connection[connection.id] = json.loads(
                connection.google_locations_json or "[]"
            )
            location_options.extend({
                "connection_id": connection.id,
                "location_id": location.get("location_id") or "",
                "name": location.get("name") or location.get("location_id") or "Google location",
            } for location in locations_by_connection[connection.id])

        review_totals = {}
        location_totals = {}
        if connection_ids:
            sql_filter = " AND connection_id = %s AND google_location_id = %s" if selected_connection_id else ""
            sql_params = (connection_ids, start_dt, selected_connection_id, selected_location_id) if selected_connection_id else (connection_ids, start_dt)
            self.env.cr.execute("""
                SELECT connection_id, COUNT(*), AVG(NULLIF(star_rating, '')::integer),
                       COUNT(*) FILTER (WHERE COALESCE(reply, '') = ''),
                       COUNT(*) FILTER (WHERE star_rating IN ('1', '2'))
                  FROM google_business_review
                 WHERE connection_id = ANY(%s) AND review_date >= %s
            """ + sql_filter + """
              GROUP BY connection_id
            """, sql_params)
            review_totals = {
                row[0]: {"reviews": row[1], "average": float(row[2] or 0), "pending": row[3], "negative": row[4]}
                for row in self.env.cr.fetchall()
            }
            self.env.cr.execute("""
                SELECT connection_id, google_location_id, COUNT(*),
                       AVG(NULLIF(star_rating, '')::integer),
                       COUNT(*) FILTER (WHERE COALESCE(reply, '') = ''),
                       COUNT(*) FILTER (WHERE star_rating IN ('1', '2'))
                  FROM google_business_review
                 WHERE connection_id = ANY(%s) AND review_date >= %s
            """ + sql_filter + """
              GROUP BY connection_id, google_location_id
            """, sql_params)
            location_totals = {
                (row[0], row[1]): {"reviews": row[2], "average": float(row[3] or 0), "pending": row[4], "negative": row[5]}
                for row in self.env.cr.fetchall()
            }

        recent_reviews = self.env["google.business.review"].search_read(
            [("connection_id", "in", connection_ids), ("review_date", ">=", start_dt)] + (
                [("connection_id", "=", selected_connection_id), ("google_location_id", "=", selected_location_id)]
                if selected_connection_id else []
            ),
            ["connection_id", "google_location_id", "location_name", "reviewer_name", "reviewer_photo_url", "star_rating", "comment", "review_date", "reply"],
            limit=12, order="review_date desc, id desc",
        ) if connection_ids else []
        for review in recent_reviews:
            review["connection_id"] = review["connection_id"][1]
            review["review_date"] = fields.Datetime.to_string(review["review_date"]) if review["review_date"] else ""

        metric_names = {
            "WEBSITE_CLICKS": "website_clicks",
            "CALL_CLICKS": "call_clicks",
            "BUSINESS_DIRECTION_REQUESTS": "direction_requests",
            "BUSINESS_IMPRESSIONS_DESKTOP_MAPS": "maps_views",
            "BUSINESS_IMPRESSIONS_MOBILE_MAPS": "maps_views",
            "BUSINESS_IMPRESSIONS_DESKTOP_SEARCH": "search_views",
            "BUSINESS_IMPRESSIONS_MOBILE_SEARCH": "search_views",
        }
        performance_totals = defaultdict(int)
        performance_series = defaultdict(lambda: defaultdict(int))
        performance_synced_at = False
        locations = []
        total_reviews = total_pending = total_negative = 0
        weighted_rating = 0.0
        rating_counts = {str(rating): 0 for rating in range(1, 6)}
        review_trend = []
        if connection_ids:
            review_scope = " AND connection_id = %s AND google_location_id = %s" if selected_connection_id else ""
            review_scope_params = (connection_ids, start_dt, selected_connection_id, selected_location_id) if selected_connection_id else (connection_ids, start_dt)
            self.env.cr.execute("""
                SELECT star_rating, COUNT(*)
                  FROM google_business_review
                 WHERE connection_id = ANY(%s) AND review_date >= %s AND star_rating IS NOT NULL
            """ + review_scope + " GROUP BY star_rating", review_scope_params)
            rating_counts.update({str(row[0]): row[1] for row in self.env.cr.fetchall()})
            self.env.cr.execute("""
                SELECT review_date::date::text, COUNT(*)
                  FROM google_business_review
                 WHERE connection_id = ANY(%s) AND review_date >= %s
            """ + review_scope + " GROUP BY review_date::date ORDER BY review_date::date", review_scope_params)
            review_trend = [{"date": row[0], "reviews": row[1]} for row in self.env.cr.fetchall()]
        locations_filter = (selected_connection_id, selected_location_id) if selected_connection_id else False
        for connection in connections:
            stats = review_totals.get(connection.id, {})
            total_reviews += stats.get("reviews", 0)
            total_pending += stats.get("pending", 0)
            total_negative += stats.get("negative", 0)
            weighted_rating += stats.get("average", 0) * stats.get("reviews", 0)
            if connection.performance_last_sync and (
                not performance_synced_at or connection.performance_last_sync > performance_synced_at
            ):
                performance_synced_at = connection.performance_last_sync
            cache = json.loads(connection.performance_json or "{}")
            for location in locations_by_connection[connection.id]:
                location_id = location.get("location_id") or ""
                if locations_filter and (connection.id, location_id) != locations_filter:
                    continue
                stats_by_location = location_totals.get((connection.id, location_id), {})
                location_metrics = cache.get(location_id, {})
                perf = defaultdict(int)
                for api_metric, values_by_date in location_metrics.items():
                    key = metric_names.get(api_metric)
                    if key:
                        for day, value in values_by_date.items():
                            if day >= start_date.isoformat():
                                perf[key] += int(value or 0)
                                performance_totals[key] += int(value or 0)
                                performance_series[day][key] += int(value or 0)
                locations.append({
                    "connection_id": connection.id,
                    "connection_name": connection.name,
                    "location_id": location_id,
                    "name": location.get("name") or location_id,
                    "address": location.get("address") or "",
                    "latitude": location.get("latitude"),
                    "longitude": location.get("longitude"),
                    "reviews": stats_by_location.get("reviews", 0),
                    "average": stats_by_location.get("average", 0),
                    "pending": stats_by_location.get("pending", 0),
                    "negative": stats_by_location.get("negative", 0),
                    "performance": dict(perf),
                })
        locations.sort(key=lambda row: (-row["pending"], -row["reviews"], row["name"].lower()))
        return {
            "days": days,
            "start_date": start_date.isoformat(),
            "end_date": date.today().isoformat(),
            "connected_accounts": sum(
                len(json.loads(connection.google_accounts_json or "[]"))
                for connection in connections.filtered("refresh_token")
            ),
            "location_count": len(locations),
            "review_count": total_reviews,
            "average_rating": round(weighted_rating / total_reviews, 2) if total_reviews else 0,
            "pending_count": total_pending,
            "negative_count": total_negative,
            "rating_distribution": [
                {"rating": rating, "count": rating_counts[rating]}
                for rating in ("5", "4", "3", "2", "1")
            ],
            "review_trend": review_trend,
            "performance": dict(performance_totals),
            "performance_series": [
                {"date": day, **dict(values)} for day, values in sorted(performance_series.items())
            ],
            "performance_last_sync": fields.Datetime.to_string(performance_synced_at) if performance_synced_at else False,
            "performance_errors": [connection.performance_error for connection in connections if connection.performance_error],
            "locations": locations,
            "location_options": location_options,
            "recent_reviews": recent_reviews,
            "last_sync": fields.Datetime.to_string(max(connections.mapped("last_sync"))) if connections.mapped("last_sync") else False,
        }

    @api.model
    def sync_dashboard_performance(self, offset=0, days=30):
        """Fetch a bounded batch of locations to avoid slow dashboard requests."""
        try:
            offset = max(int(offset), 0)
            days = min(max(int(days), 7), 90)
        except (TypeError, ValueError):
            offset, days = 0, 30
        entries = []
        for connection in self.search([("refresh_token", "!=", False)], order="id"):
            for location in json.loads(connection.google_locations_json or "[]"):
                entries.append((connection, location))
        batch = entries[offset:offset + 8]
        metrics = [
            "WEBSITE_CLICKS", "CALL_CLICKS", "BUSINESS_DIRECTION_REQUESTS",
            "BUSINESS_IMPRESSIONS_DESKTOP_MAPS", "BUSINESS_IMPRESSIONS_MOBILE_MAPS",
            "BUSINESS_IMPRESSIONS_DESKTOP_SEARCH", "BUSINESS_IMPRESSIONS_MOBILE_SEARCH",
        ]
        end_date = date.today() - timedelta(days=1)
        start_date = end_date - timedelta(days=days - 1)
        errors = []
        for connection, location in batch:
            location_id = location.get("location_id")
            if not location_id:
                continue
            url = "https://businessprofileperformance.googleapis.com/v1/locations/%s:fetchMultiDailyMetricsTimeSeries" % location_id
            params = [("dailyMetrics", metric) for metric in metrics]
            for prefix, value in (("start_date", start_date), ("end_date", end_date)):
                params.extend([
                    ("dailyRange.%s.year" % prefix, value.year),
                    ("dailyRange.%s.month" % prefix, value.month),
                    ("dailyRange.%s.day" % prefix, value.day),
                ])
            try:
                token = connection._get_access_token()
                response = requests.get(
                    url, params=params,
                    headers={"Authorization": "Bearer " + token}, timeout=20,
                )
                response.raise_for_status()
                payload = response.json()
                cache = json.loads(connection.performance_json or "{}")
                location_cache = cache.setdefault(location_id, {})
                for group in payload.get("multiDailyMetricTimeSeries", []):
                    for metric_series in group.get("dailyMetricTimeSeries", []):
                        metric = metric_series.get("dailyMetric")
                        if metric not in metrics:
                            continue
                        date_values = location_cache.setdefault(metric, {})
                        for item in metric_series.get("timeSeries", {}).get("datedValues", []):
                            date_obj = item.get("date", {})
                            if date_obj:
                                key = "%04d-%02d-%02d" % (date_obj.get("year"), date_obj.get("month"), date_obj.get("day"))
                                date_values[key] = int(float(item.get("value", 0)))
                connection.write({"performance_json": json.dumps(cache), "performance_last_sync": fields.Datetime.now(), "performance_error": False})
            except Exception as exc:
                connection.performance_error = "%s: %s" % (location.get("name") or location_id, str(exc)[:220])
                errors.append(connection.performance_error)
        next_offset = offset + len(batch)
        return {"next_offset": next_offset, "done": next_offset >= len(entries), "synced": len(batch), "errors": errors}
