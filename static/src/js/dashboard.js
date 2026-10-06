/** @odoo-module **/

import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { Component, onMounted, onPatched, onWillStart, onWillUnmount, useRef, useState } from "@odoo/owl";

class GoogleReviewsDashboard extends Component {
    static template = "google_reviews.Dashboard";

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.notification = useService("notification");
        this.mapRef = useRef("locationMap");
        this.ratingChartRef = useRef("ratingChart");
        this.trendChartRef = useRef("trendChart");
        this.performanceChartRef = useRef("performanceChart");
        this.locationMap = null;
        this.mapMarkers = null;
        this.mapSignature = null;
        this.ratingChart = null;
        this.trendChart = null;
        this.performanceChart = null;
        this.state = useState({
            loading: true,
            syncing: false,
            syncProgress: "",
            days: 30,
            locationFilter: "all",
            locationSearch: "",
            reviewSearch: "",
            reviewFilter: "all",
            mapSyncing: false,
            data: null,
        });
        onWillStart(() => this.load());
        onMounted(() => {
            this.updateMap();
            this.updateCharts();
        });
        onPatched(() => {
            this.updateMap();
            this.updateCharts();
        });
        onWillUnmount(() => {
            this.locationMap?.remove();
            this.ratingChart?.destroy();
            this.trendChart?.destroy();
            this.performanceChart?.destroy();
        });
    }

    async load() {
        this.state.loading = true;
        try {
            this.state.data = await this.orm.call(
                "google.reviews.connection", "get_dashboard_data", [this.state.days, this.state.locationFilter]
            );
        } catch (error) {
            this.notification.add(error.message || "Dashboard data could not be loaded.", { type: "danger" });
        } finally {
            this.state.loading = false;
        }
    }

    async setDays(event) {
        this.state.days = Number(event.target.value);
        await this.load();
    }

    async setLocation(event) {
        this.state.locationFilter = event.target.value;
        await this.load();
    }

    setLocationSearch(event) {
        this.state.locationSearch = event.target.value.toLowerCase().trim();
    }

    setReviewSearch(event) {
        this.state.reviewSearch = event.target.value.toLowerCase().trim();
    }

    setReviewFilter(filter) {
        this.state.reviewFilter = filter;
    }

    get mapLocations() {
        return (this.state.data?.locations || []).filter(
            (location) => location.latitude !== null && location.latitude !== false
                && location.longitude !== null && location.longitude !== false
                && Number.isFinite(Number(location.latitude)) && Number.isFinite(Number(location.longitude))
        );
    }

    updateMap() {
        const element = this.mapRef.el;
        if (!element || !window.L) return;
        if (!this.locationMap) {
            this.locationMap = window.L.map(element, { scrollWheelZoom: false, zoomControl: true });
            window.L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
                maxZoom: 19,
                attribution: '&copy; <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener">OpenStreetMap contributors</a>',
            }).addTo(this.locationMap);
            this.mapMarkers = window.L.markerClusterGroup({
                showCoverageOnHover: false,
                spiderfyOnMaxZoom: true,
                iconCreateFunction(cluster) {
                    return window.L.divIcon({
                        className: "o_gr_map_cluster",
                        html: `<span>${cluster.getChildCount()}</span>`,
                        iconSize: [42, 42],
                    });
                },
            }).addTo(this.locationMap);
        }
        window.setTimeout(() => this.locationMap?.invalidateSize(), 0);
        const signature = this.mapLocations.map((item) => `${item.connection_id}:${item.location_id}:${item.latitude}:${item.longitude}`).join("|");
        if (signature === this.mapSignature) return;
        this.mapSignature = signature;
        this.mapMarkers.clearLayers();
        const bounds = [];
        for (const location of this.mapLocations) {
            const point = [Number(location.latitude), Number(location.longitude)];
            bounds.push(point);
            const marker = window.L.marker(point, {
                icon: window.L.divIcon({ className: "o_gr_map_pin", html: "<span></span>", iconSize: [20, 28], iconAnchor: [10, 26] }),
            });
            const popup = document.createElement("div");
            popup.className = "o_gr_map_popup";
            const title = document.createElement("strong");
            title.textContent = location.name || "Google Business location";
            const details = document.createElement("small");
            details.textContent = `${location.reviews} reviews · ${Number(location.average).toFixed(1)} ★ · ${location.pending} pending replies`;
            const openButton = document.createElement("button");
            openButton.type = "button";
            openButton.className = "btn btn-sm btn-primary";
            openButton.textContent = "View reviews";
            openButton.addEventListener("click", () => this.openLocationReviews(location));
            popup.append(title, details, openButton);
            marker.bindPopup(popup).addTo(this.mapMarkers);
        }
        if (bounds.length === 1) this.locationMap.setView(bounds[0], 12);
        else if (bounds.length > 1) this.locationMap.fitBounds(bounds, { padding: [28, 28], maxZoom: 12 });
        else this.locationMap.setView([20, 0], 2);
    }

    mapX(longitude) {
        return 12 + ((Number(longitude) + 180) / 360) * 876;
    }

    mapY(latitude) {
        const bounded = Math.max(-60, Math.min(85, Number(latitude)));
        return 12 + ((85 - bounded) / 145) * 276;
    }

    syncMapLocations = async () => {
        this.state.mapSyncing = true;
        try {
            const result = await this.orm.call("google.reviews.connection", "sync_location_map_data", []);
            await this.load();
            if (result.errors.length) {
                this.notification.add(result.errors.join("; "), { type: "warning" });
            } else if (result.coordinates) {
                this.notification.add(`Map updated with ${result.coordinates} Google locations.`, { type: "success" });
            } else {
                this.notification.add("Google did not provide map coordinates for these locations. The location list is still available below.", { type: "warning" });
            }
        } catch (error) {
            this.notification.add(error.message || "Google locations could not be refreshed.", { type: "danger" });
        } finally {
            this.state.mapSyncing = false;
        }
    };

    async syncPerformance() {
        this.state.syncing = true;
        let offset = 0;
        let synced = 0;
        const errors = [];
        try {
            while (true) {
                const result = await this.orm.call(
                    "google.reviews.connection", "sync_dashboard_performance", [offset, this.state.days]
                );
                synced += result.synced;
                errors.push(...result.errors);
                offset = result.next_offset;
                this.state.syncProgress = `Loaded performance for ${synced} locations…`;
                if (result.done) break;
            }
            await this.load();
            if (errors.length) {
                this.notification.add(`Performance synced with ${errors.length} location error(s). Check the API status panel.`, { type: "warning" });
            } else {
                this.notification.add(`Google performance updated for ${synced} locations.`, { type: "success" });
            }
        } catch (error) {
            this.notification.add(error.message || "Performance sync failed.", { type: "danger" });
        } finally {
            this.state.syncing = false;
            this.state.syncProgress = "";
        }
    }

    get locations() {
        const rows = this.state.data?.locations || [];
        const search = this.state.locationSearch;
        return rows.filter((row) => !search || `${row.name} ${row.address}`.toLowerCase().includes(search));
    }

    get recentReviews() {
        const rows = this.state.data?.recent_reviews || [];
        return rows.filter((row) => {
            if (this.state.reviewFilter === "pending" && row.reply) return false;
            if (this.state.reviewFilter === "written" && !row.comment) return false;
            if (this.state.reviewFilter === "rating" && row.comment) return false;
            const search = this.state.reviewSearch;
            return !search || `${row.reviewer_name || ""} ${row.location_name || ""} ${row.comment || ""}`.toLowerCase().includes(search);
        });
    }

    get performance() {
        return this.state.data?.performance || {};
    }

    get locationOptions() {
        return this.state.data?.location_options || [];
    }

    get reviewCounts() {
        return this.state.data?.rating_distribution || [];
    }

    get reviewFilterDomain() {
        if (this.state.reviewFilter === "pending") return [["reply", "=", false]];
        if (this.state.reviewFilter === "written") return [["comment", "!=", false]];
        if (this.state.reviewFilter === "rating") return [["comment", "=", false]];
        return [];
    }

    updateCharts() {
        const Chart = window.Chart;
        if (!Chart || !this.state.data) return;
        const data = this.state.data;
        const colors = ["#f3b641", "#34b89a", "#7184e7", "#ef8a72", "#bb6ad6"];
        const ratingCanvas = this.ratingChartRef.el;
        if (ratingCanvas) {
            const ratingData = {
                labels: data.rating_distribution.map((item) => `${item.rating} star`),
                datasets: [{ data: data.rating_distribution.map((item) => item.count), backgroundColor: colors, borderColor: "#fff", borderWidth: 4, hoverOffset: 8 }],
            };
            if (!this.ratingChart) {
                this.ratingChart = new Chart(ratingCanvas, {
                    type: "doughnut",
                    data: ratingData,
                    options: { responsive: true, maintainAspectRatio: false, cutout: "76%", plugins: { legend: { display: false }, tooltip: { padding: 12, displayColors: true }, ratingCenter: { average: data.average_rating } } },
                    plugins: [{ id: "ratingCenter", afterDraw(chart) {
                        const { ctx, chartArea } = chart;
                        if (!chartArea) return;
                        ctx.save();
                        ctx.textAlign = "center";
                        ctx.fillStyle = "#28345d";
                        ctx.font = "700 28px Inter, sans-serif";
                        ctx.fillText(Number(chart.options.plugins.ratingCenter.average || 0).toFixed(1), (chartArea.left + chartArea.right) / 2, (chartArea.top + chartArea.bottom) / 2);
                        ctx.fillStyle = "#8992a8";
                        ctx.font = "11px Inter, sans-serif";
                        ctx.fillText("AVG RATING", (chartArea.left + chartArea.right) / 2, (chartArea.top + chartArea.bottom) / 2 + 19);
                        ctx.restore();
                    } }],
                });
            } else {
                this.ratingChart.data = ratingData;
                this.ratingChart.options.plugins.ratingCenter.average = data.average_rating;
                this.ratingChart.update("none");
            }
        }
        const trendCanvas = this.trendChartRef.el;
        if (trendCanvas) {
            const labels = data.review_trend.map((item) => item.date.slice(5));
            const trendData = {
                labels,
                datasets: [{ label: "Reviews", data: data.review_trend.map((item) => item.reviews), borderColor: "#6865db", backgroundColor: "#6865db20", borderWidth: 3, pointRadius: 2, pointHoverRadius: 5, fill: true, tension: 0.38 }],
            };
            if (!this.trendChart) this.trendChart = new Chart(trendCanvas, { type: "line", data: trendData, options: this.lineChartOptions() });
            else { this.trendChart.data = trendData; this.trendChart.update("none"); }
        }
        const performanceCanvas = this.performanceChartRef.el;
        if (performanceCanvas) {
            const series = data.performance_series;
            const datasets = [
                ["website_clicks", "Website clicks", "#14a78c"],
                ["call_clicks", "Calls", "#6875dd"],
                ["direction_requests", "Directions", "#f2b43e"],
            ].map(([key, label, color]) => ({ label, data: series.map((point) => point[key] || 0), borderColor: color, backgroundColor: `${color}22`, borderWidth: 2.5, pointRadius: 0, pointHoverRadius: 4, fill: true, tension: 0.35 }));
            const perfData = { labels: series.map((point) => point.date.slice(5)), datasets };
            if (!this.performanceChart) this.performanceChart = new Chart(performanceCanvas, { type: "line", data: perfData, options: this.lineChartOptions() });
            else { this.performanceChart.data = perfData; this.performanceChart.update("none"); }
        }
    }

    lineChartOptions() {
        return {
            responsive: true,
            maintainAspectRatio: false,
            interaction: { intersect: false, mode: "index" },
            plugins: { legend: { position: "bottom", labels: { usePointStyle: true, pointStyle: "circle", boxWidth: 7, padding: 18, color: "#737d98", font: { size: 11 } } }, tooltip: { padding: 12, cornerRadius: 10 } },
            scales: {
                x: { grid: { display: false }, ticks: { color: "#8c94a8", maxTicksLimit: 10, maxRotation: 0 } },
                y: { beginAtZero: true, grid: { color: "#edf0f6" }, border: { display: false }, ticks: { precision: 0, color: "#8c94a8" } },
            },
        };
    }

    openReviews = async (domain = [], reviewId = false) => {
        await this.action.doAction({
            type: "ir.actions.act_window",
            name: "Google Reviews",
            res_model: "google.business.review",
            // Client-side act_window actions need the normalized views array;
            // view_mode alone is only expanded when Odoo loads a server action.
            views: reviewId ? [[false, "form"]] : [[false, "list"], [false, "form"]],
            view_mode: reviewId ? "form" : "list,form",
            domain,
            res_id: reviewId || false,
        });
    };

    openReview = (review) => this.openReviews([], review.id);

    openPending = () => {
        return this.openReviews([["reply", "=", false]]);
    };

    openLocationReviews = (location) => {
        return this.openReviews([
            ["connection_id", "=", location.connection_id],
            ["google_location_id", "=", location.location_id],
        ]);
    };

    formatDate(value) {
        if (!value) return "Not synced yet";
        return new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" })
            .format(new Date(value.replace(" ", "T") + (value.endsWith("Z") ? "" : "Z")));
    }

    ratingClass(value) {
        return Number(value) < 3 ? "text-danger" : Number(value) < 4 ? "text-warning" : "text-success";
    }

    metricWidth(value, rows) {
        const max = Math.max(...rows.map((row) => Number(row.reviews || 0)), 1);
        return `${Math.max(3, Math.round((Number(value || 0) / max) * 100))}%`;
    }

    barHeight(value, key, rows) {
        const max = Math.max(...rows.map((row) => Number(row[key] || 0)), 1);
        return `${Math.max(2, Math.round((Number(value || 0) / max) * 96))}px`;
    }
}

registry.category("actions").add("google_reviews.dashboard", GoogleReviewsDashboard);
