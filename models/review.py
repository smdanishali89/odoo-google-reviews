import requests

from odoo import _, fields, models
from odoo.exceptions import UserError


class GoogleBusinessReview(models.Model):
    _name = "google.business.review"
    _description = "Google Business Review"
    _order = "review_date desc, id desc"

    connection_id = fields.Many2one(
        "google.reviews.connection", required=True, ondelete="cascade"
    )
    google_review_id = fields.Char(required=True, index=True)
    google_location_id = fields.Char(index=True)
    location_name = fields.Char()
    reviewer_name = fields.Char()
    reviewer_photo_url = fields.Char(string="Reviewer photo URL")
    star_rating = fields.Selection(
        [(str(i), "%s star%s" % (i, "" if i == 1 else "s")) for i in range(1, 6)]
    )
    comment = fields.Text()
    review_date = fields.Datetime()
    reply = fields.Text()
    reply_to_send = fields.Text()

    _google_review_unique = models.Constraint(
        "UNIQUE(connection_id, google_review_id)",
        "This Google review is already linked to this connection.",
    )

    def action_open_reply(self):
        self.ensure_one()
        if not self.comment:
            raise UserError(_("This review has no written comment, so Google does not allow a reply here."))
        return {
            "type": "ir.actions.act_window",
            "name": _("Reply to Google Review"),
            "res_model": self._name,
            "res_id": self.id,
            "view_mode": "form",
            "target": "current",
            "context": {"form_view_initial_mode": "edit"},
        }

    def action_reply(self):
        for review in self:
            connection = review.connection_id
            if not review.reply_to_send:
                raise UserError(_("Write a reply before sending it to Google."))
            token = connection._get_access_token()
            review_resource = review.google_review_id
            if "/" not in review_resource:
                review_resource = "accounts/%s/locations/%s/reviews/%s" % (
                    connection.google_account_id,
                    review.google_location_id or connection.location_id,
                    review_resource,
                )
            url = (
                "https://mybusiness.googleapis.com/v4/%s/reply"
                % review_resource
            )
            response = requests.put(
                url,
                json={"comment": review.reply_to_send},
                headers={"Authorization": "Bearer " + token},
                timeout=30,
            )
            if not response.ok:
                raise UserError(_("Google could not publish the reply: %s") % response.text[:500])
            review.write({"reply": review.reply_to_send, "reply_to_send": False})
