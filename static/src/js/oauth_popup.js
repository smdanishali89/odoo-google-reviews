/** @odoo-module **/

import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { Component } from "@odoo/owl";

class GoogleReviewsOAuthPopup extends Component {
    static template = "google_reviews.OAuthPopup";

    setup() {
        this.notification = useService("notification");
        this.params = this.props.action.params;
    }

    connect() {
        const { url } = this.params;
        const popup = window.open("about:blank", "google_reviews_oauth", "width=600,height=750");
        if (!popup) {
            this.notification.add("Allow popups for this Odoo site, then try again.", { type: "danger" });
            return;
        }
        popup.location = url;
        const refreshWhenClosed = window.setInterval(() => {
            if (popup.closed) {
                window.clearInterval(refreshWhenClosed);
                window.location.reload();
            }
        }, 700);
    }
}

registry.category("actions").add("google_reviews.oauth_popup", GoogleReviewsOAuthPopup);
