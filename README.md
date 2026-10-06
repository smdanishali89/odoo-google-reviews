# Google Reviews for Odoo 19

Manage Google Business Profile reviews in Odoo. The module can connect a Google account, synchronize accounts and locations, fetch reviews, publish replies, and show review and profile performance summaries.

## Requirements

- Odoo 19.0
- Access to the Google Business Profile APIs for the Google Cloud project used by this integration
- A working HTTPS Google OAuth callback and refresh-token service
- Google Business Profile permissions for the Google account being connected

This release is configured to use the eCube OAuth service at `solution.ecube.pk`. Google authorization codes pass through that service, and it exchanges access or refresh tokens with Google. The refresh token is saved in the Odoo database for the relevant connection. Installing this addon does not deploy or configure the OAuth service. Availability of Google connection and token refresh depends on that service remaining operational.

## Setup

1. Install **Google Reviews** from the Odoo Apps menu.
2. Open **Google Reviews → Connections** and create a connection.
3. Choose **Connect Google** and grant the requested Business Profile permission.
4. Fetch the available accounts and locations, then synchronize reviews.
5. Open a written review to publish a reply.

Administrators can access the connection records, including stored tokens. Secure Odoo accounts, database access, and backups. Revoke the integration from the Google Account security page when access is no longer needed.

Publisher support: faheem.riaz@wags.sa  
Privacy policy: https://wags.sa/en/policy

Before production release, configure this privacy policy URL in the Google Cloud OAuth consent screen and complete any Google verification required for public users.

## Third-party libraries

- Chart.js (MIT License), license text in `static/lib/chart/LICENSE`
- Leaflet (BSD 2-Clause License), license text in `static/lib/leaflet/LICENSE`
- Leaflet.markercluster (MIT License), license text in `static/lib/leaflet-markercluster/LICENSE`

## License

LGPL-3. See `LICENSE`.
