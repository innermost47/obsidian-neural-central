from google.analytics.data_v1beta import BetaAnalyticsDataClient
from google.analytics.data_v1beta.types import (
    DateRange,
    Dimension,
    Metric,
    RunReportRequest,
    OrderBy,
)
from google.oauth2 import service_account
from datetime import datetime, timedelta
from typing import Dict, List, Optional


from server.config import settings


class GoogleAnalyticsService:
    def __init__(self):
        self.property_id = settings.GA4_PROPERTY_ID
        self.client = None

        if settings.GOOGLE_ANALYTICS_CREDENTIALS_PATH:
            try:
                credentials = service_account.Credentials.from_service_account_file(
                    settings.GOOGLE_ANALYTICS_CREDENTIALS_PATH,
                    scopes=["https://www.googleapis.com/auth/analytics.readonly"],
                )

                self.client = BetaAnalyticsDataClient(credentials=credentials)
                print("Google Analytics client initialized successfully")

            except Exception as e:
                print(f"Failed to initialize Google Analytics client: {e}")
                self.client = None
        else:
            print("Google Analytics credentials path not configured")

    def _run_report(
        self,
        dimensions: List[str],
        metrics: List[str],
        days: int = 30,
        order_by: Optional[List] = None,
        limit: int = None,
    ) -> Optional[object]:
        if not self.client or not self.property_id:
            print("Google Analytics not properly configured")
            return None

        try:
            end_date = datetime.now()
            if days >= 9999:
                start_date = datetime(2020, 10, 14)
            else:
                start_date = end_date - timedelta(days=days)

            request = RunReportRequest(
                property=f"properties/{self.property_id}",
                date_ranges=[
                    DateRange(
                        start_date=start_date.strftime("%Y-%m-%d"),
                        end_date=end_date.strftime("%Y-%m-%d"),
                    )
                ],
                dimensions=[Dimension(name=d) for d in dimensions],
                metrics=[Metric(name=m) for m in metrics],
            )

            if order_by:
                request.order_bys = order_by

            if limit:
                request.limit = limit

            response = self.client.run_report(request)
            return response

        except Exception as e:
            print(f"Error running GA4 report: {e}")
            return None

    def get_overview_stats(self, days: int = 30) -> Dict:
        response = self._run_report(
            dimensions=[],
            metrics=[
                "activeUsers",
                "newUsers",
                "sessions",
                "screenPageViews",
                "averageSessionDuration",
                "bounceRate",
            ],
            days=days,
        )

        if not response or not response.rows:
            return {
                "stats": {
                    "active_users": 0,
                    "new_users": 0,
                    "sessions": 0,
                    "page_views": 0,
                    "avg_session_duration": 0,
                    "bounce_rate": 0,
                }
            }

        row = response.rows[0]

        return {
            "stats": {
                "active_users": int(row.metric_values[0].value),
                "new_users": int(row.metric_values[1].value),
                "sessions": int(row.metric_values[2].value),
                "page_views": int(row.metric_values[3].value),
                "avg_session_duration": float(row.metric_values[4].value),
                "bounce_rate": float(row.metric_values[5].value),
            }
        }

    def get_daily_stats(self, days: int = 30) -> Dict:
        response = self._run_report(
            dimensions=["date"],
            metrics=["activeUsers", "newUsers", "sessions"],
            days=days,
            order_by=[
                OrderBy(dimension=OrderBy.DimensionOrderBy(dimension_name="date"))
            ],
        )

        if not response or not response.rows:
            return {"data": []}

        data = []
        for row in response.rows:
            date_str = row.dimension_values[0].value
            formatted_date = f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:]}"

            data.append(
                {
                    "date": formatted_date,
                    "active_users": int(row.metric_values[0].value),
                    "new_users": int(row.metric_values[1].value),
                    "sessions": int(row.metric_values[2].value),
                }
            )

        return {"data": data}

    def get_top_pages(self, days: int = 30, limit: int = 10) -> Dict:
        response = self._run_report(
            dimensions=["pageTitle", "pagePath"],
            metrics=["screenPageViews", "userEngagementDuration"],
            days=days,
            order_by=[
                OrderBy(
                    metric=OrderBy.MetricOrderBy(metric_name="screenPageViews"),
                    desc=True,
                )
            ],
            limit=limit,
        )

        if not response or not response.rows:
            return {"pages": []}

        pages = []
        for row in response.rows:
            views = int(row.metric_values[0].value)
            duration = float(row.metric_values[1].value)
            avg_time = duration / views if views > 0 else 0

            pages.append(
                {
                    "title": row.dimension_values[0].value,
                    "path": row.dimension_values[1].value,
                    "views": views,
                    "avg_time": avg_time,
                }
            )

        return {"pages": pages}

    def get_traffic_sources(self, days: int = 30) -> Dict:
        response = self._run_report(
            dimensions=["sessionSource"],
            metrics=["sessions", "newUsers"],
            days=days,
            order_by=[
                OrderBy(metric=OrderBy.MetricOrderBy(metric_name="sessions"), desc=True)
            ],
            limit=10,
        )

        if not response or not response.rows:
            return {"sources": []}

        sources = []
        for row in response.rows:
            sources.append(
                {
                    "source": row.dimension_values[0].value,
                    "sessions": int(row.metric_values[0].value),
                    "new_users": int(row.metric_values[1].value),
                }
            )

        return {"sources": sources}

    def get_device_breakdown(self, days: int = 30) -> Dict:
        response = self._run_report(
            dimensions=["deviceCategory"],
            metrics=["activeUsers", "sessions"],
            days=days,
        )

        if not response or not response.rows:
            return {"devices": []}

        devices = []
        for row in response.rows:
            devices.append(
                {
                    "device": row.dimension_values[0].value.capitalize(),
                    "users": int(row.metric_values[0].value),
                    "sessions": int(row.metric_values[1].value),
                }
            )

        return {"devices": devices}

    def get_countries(self, days: int = 30, limit: int = 10) -> Dict:
        response = self._run_report(
            dimensions=["country"],
            metrics=["activeUsers", "sessions", "newUsers"],
            days=days,
            order_by=[
                OrderBy(
                    metric=OrderBy.MetricOrderBy(metric_name="activeUsers"), desc=True
                )
            ],
            limit=limit,
        )

        if not response or not response.rows:
            return {"countries": []}

        countries = []
        for row in response.rows:
            countries.append(
                {
                    "country": row.dimension_values[0].value,
                    "users": int(row.metric_values[0].value),
                    "sessions": int(row.metric_values[1].value),
                    "new_users": int(row.metric_values[2].value),
                }
            )

        return {"countries": countries}

    def get_conversion_funnel(self, days: int = 30) -> Dict:
        response = self._run_report(
            dimensions=["pagePath"],
            metrics=["screenPageViews", "activeUsers"],
            days=days,
        )

        if not response or not response.rows:
            return {"funnel": [], "paths": {}, "summary": {}}

        pages_data = {}

        for row in response.rows:
            page_path = row.dimension_values[0].value.lower()
            views = int(row.metric_values[0].value)
            users = int(row.metric_values[1].value)

            if page_path in ["/", "/index.html"]:
                key = "home"
            elif "pricing" in page_path:
                key = "pricing"
            elif "register" in page_path:
                key = "register"
            elif "login" in page_path:
                key = "login"
            elif "dashboard" in page_path:
                key = "dashboard"
            elif "documentation" in page_path:
                key = "documentation"
            elif "gift" in page_path:
                key = "gift"
            elif "press" in page_path:
                key = "press"
            elif "contact" in page_path:
                key = "contact"
            elif "status" in page_path:
                key = "status"
            else:
                continue

            if key not in pages_data:
                pages_data[key] = {"views": 0, "users": 0}

            pages_data[key]["views"] += views
            pages_data[key]["users"] += users

        funnel_steps = [
            {
                "id": "home",
                "name": "🏠 Landing",
                "description": "First contact with the site",
                "views": pages_data.get("home", {}).get("views", 0),
                "users": pages_data.get("home", {}).get("users", 0),
            },
            {
                "id": "pricing",
                "name": "💰 Interest",
                "description": "Explored pricing options",
                "views": pages_data.get("pricing", {}).get("views", 0),
                "users": pages_data.get("pricing", {}).get("users", 0),
            },
            {
                "id": "register",
                "name": "✍️ Sign Up Intent",
                "description": "Started registration",
                "views": pages_data.get("register", {}).get("views", 0),
                "users": pages_data.get("register", {}).get("users", 0),
            },
            {
                "id": "dashboard",
                "name": "✅ Conversion",
                "description": "Successfully onboarded",
                "views": pages_data.get("dashboard", {}).get("views", 0),
                "users": pages_data.get("dashboard", {}).get("users", 0),
            },
        ]

        total_visitors = funnel_steps[0]["users"] if funnel_steps[0]["users"] > 0 else 1

        for i, step in enumerate(funnel_steps):
            step["conversion_rate"] = (
                (step["users"] / total_visitors) * 100 if total_visitors > 0 else 0
            )

            if i > 0:
                prev_users = funnel_steps[i - 1]["users"]
                step["drop_off"] = prev_users - step["users"]
                step["retention_rate"] = (
                    (step["users"] / prev_users * 100) if prev_users > 0 else 0
                )
            else:
                step["drop_off"] = 0
                step["retention_rate"] = 100.0

        register_users = pages_data.get("register", {}).get("users", 0)
        pricing_users = pages_data.get("pricing", {}).get("users", 0)
        dashboard_users = pages_data.get("dashboard", {}).get("users", 0)
        login_users = pages_data.get("login", {}).get("users", 0)

        paths = {
            "direct_register": {
                "name": "🎯 Direct Registration",
                "description": "Went straight to sign up (skipped pricing)",
                "count": max(0, register_users - min(pricing_users, register_users)),
                "icon": "fa-bolt",
            },
            "pricing_engaged": {
                "name": "💡 Pricing Interested",
                "description": "Viewed pricing but didn't register yet",
                "count": max(0, pricing_users - register_users),
                "icon": "fa-eye",
            },
            "completed_signup": {
                "name": "🎉 Successful Signups",
                "description": "Registered AND accessed dashboard",
                "count": dashboard_users,
                "rate": (
                    (dashboard_users / register_users * 100)
                    if register_users > 0
                    else 0
                ),
                "icon": "fa-check-circle",
            },
            "returning_users": {
                "name": "🔄 Returning Users",
                "description": "Existing users logging back in",
                "count": login_users,
                "icon": "fa-redo",
            },
            "documentation_readers": {
                "name": "📖 Documentation Readers",
                "description": "Users exploring the docs",
                "count": pages_data.get("documentation", {}).get("users", 0),
                "icon": "fa-book",
            },
            "gift_card_interest": {
                "name": "🎁 Gift Card Interest",
                "description": "Checked out gift cards",
                "count": pages_data.get("gift", {}).get("users", 0),
                "icon": "fa-gift",
            },
        }

        summary = {
            "total_visitors": total_visitors,
            "total_pageviews": sum(p["views"] for p in pages_data.values()),
            "pricing_viewers": pricing_users,
            "registration_attempts": register_users,
            "successful_conversions": dashboard_users,
            "overall_conversion_rate": (
                (dashboard_users / total_visitors * 100) if total_visitors > 0 else 0
            ),
            "signup_completion_rate": (
                (dashboard_users / register_users * 100) if register_users > 0 else 0
            ),
        }

        return {"funnel": funnel_steps, "paths": paths, "summary": summary}

    def get_social_referrals(self, days: int = 30) -> Dict:
        response = self._run_report(
            dimensions=["sessionSource", "sessionMedium"],
            metrics=["sessions", "newUsers", "activeUsers"],
            days=days,
            order_by=[
                OrderBy(metric=OrderBy.MetricOrderBy(metric_name="sessions"), desc=True)
            ],
            limit=50,
        )

        if not response or not response.rows:
            return {"social": []}

        social_patterns = {
            "Facebook": ["facebook", "fb", "m.facebook"],
            "Instagram": ["instagram", "ig"],
            "Twitter": ["twitter", "t.co"],
            "LinkedIn": ["linkedin"],
            "Reddit": ["reddit"],
            "TikTok": ["tiktok"],
            "YouTube": ["youtube"],
            "Pinterest": ["pinterest"],
            "Snapchat": ["snapchat"],
            "WhatsApp": ["whatsapp"],
            "Telegram": ["telegram"],
            "Discord": ["discord"],
        }

        social_data = {}

        for row in response.rows:
            source = row.dimension_values[0].value.lower()
            sessions = int(row.metric_values[0].value)
            new_users = int(row.metric_values[1].value)
            active_users = int(row.metric_values[2].value)

            for platform, patterns in social_patterns.items():
                if any(pattern in source for pattern in patterns):
                    if platform not in social_data:
                        social_data[platform] = {
                            "platform": platform,
                            "sessions": 0,
                            "new_users": 0,
                            "active_users": 0,
                        }
                    social_data[platform]["sessions"] += sessions
                    social_data[platform]["new_users"] += new_users
                    social_data[platform]["active_users"] += active_users
                    break

        social_list = list(social_data.values())
        social_list.sort(key=lambda x: x["sessions"], reverse=True)

        return {"social": social_list}


google_analytics_service = GoogleAnalyticsService()
