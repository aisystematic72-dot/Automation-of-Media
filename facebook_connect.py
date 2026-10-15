"""Facebook authorization; temporary Page tokens stay on the server."""
import json
import os
import secrets
import sqlite3
import time
from contextlib import contextmanager
from urllib.parse import urlencode

import requests
from flask import Blueprint, current_app, redirect, render_template, request, session, url_for


def create_facebook_blueprint(get_sheet):
    bp = Blueprint("facebook", __name__)

    def settings():
        values = {key: os.getenv(key, "").strip() for key in (
            "FACEBOOK_APP_ID", "FACEBOOK_APP_SECRET", "FACEBOOK_CONFIG_ID",
            "FACEBOOK_REDIRECT_URI", "FACEBOOK_GRAPH_VERSION")}
        if not all(values.values()):
            raise ValueError("Facebook connection is not configured. Contact the administrator.")
        return values

    @contextmanager
    def database():
        db = sqlite3.connect(os.getenv("FACEBOOK_OAUTH_DB", "facebook_oauth.sqlite3"))
        db.execute("CREATE TABLE IF NOT EXISTS pending (id TEXT PRIMARY KEY, user_id TEXT, expires REAL, pages TEXT)")
        db.execute("DELETE FROM pending WHERE expires < ?", (time.time(),))
        db.commit()
        try:
            yield db
            db.commit()
        finally:
            db.close()

    def error(message, status=400):
        return render_template("facebook_connect.html", error=message), status

    def graph(config, path, params=None, token=None):
        response = requests.get(
            f"https://graph.facebook.com/{config['FACEBOOK_GRAPH_VERSION']}/{path}",
            params=params, headers={"Authorization": f"Bearer {token}"} if token else {},
            timeout=30)
        data = response.json()
        if not response.ok or "error" in data:
            raise ValueError("Facebook could not authorize this connection. Please reconnect and check your Page permissions.")
        return data

    @bp.route("/auth/facebook")
    def start():
        if "user_id" not in session:
            return redirect(url_for("home"))
        try:
            config = settings()
        except ValueError as exc:
            return error(str(exc), 503)
        state = secrets.token_urlsafe(32)
        session["facebook_oauth"] = {"state": state, "user_id": str(session["user_id"]), "created": time.time()}
        params = {"client_id": config["FACEBOOK_APP_ID"],
                  "redirect_uri": config["FACEBOOK_REDIRECT_URI"],
                  "config_id": config["FACEBOOK_CONFIG_ID"],
                  "response_type": "code", "state": state}
        return redirect(f"https://www.facebook.com/{config['FACEBOOK_GRAPH_VERSION']}/dialog/oauth?{urlencode(params)}")

    @bp.route("/auth/facebook/callback")
    def callback():
        attempt = session.pop("facebook_oauth", None)
        if (not attempt or "user_id" not in session
                or attempt["user_id"] != str(session["user_id"])
                or time.time() - attempt["created"] > 600
                or not secrets.compare_digest(attempt["state"], request.args.get("state", ""))):
            return error("This connection request expired or is invalid. Start again from your dashboard.")
        if request.args.get("error") or not request.args.get("code"):
            return error("Facebook connection was cancelled. You can try again from your dashboard.")
        try:
            config = settings()
            token = graph(config, "oauth/access_token", {
                "client_id": config["FACEBOOK_APP_ID"], "client_secret": config["FACEBOOK_APP_SECRET"],
                "redirect_uri": config["FACEBOOK_REDIRECT_URI"], "code": request.args["code"]})["access_token"]
            token = graph(config, "oauth/access_token", {
                "grant_type": "fb_exchange_token", "client_id": config["FACEBOOK_APP_ID"],
                "client_secret": config["FACEBOOK_APP_SECRET"], "fb_exchange_token": token})["access_token"]
            pages, after = [], None
            returned_count = 0
            token_count = 0
            publishing_count = 0
            while True:
                params = {"fields": "id,name,access_token,tasks", "limit": 100}
                if after:
                    params["after"] = after
                data = graph(config, "me/accounts", params, token)
                returned = data.get("data", [])
                returned_count += len(returned)
                token_count += sum(bool(p.get("access_token")) for p in returned)
                publishing_count += sum("CREATE_CONTENT" in p.get("tasks", []) for p in returned)
                pages.extend(p for p in data.get("data", []) if p.get("access_token")
                             and "CREATE_CONTENT" in p.get("tasks", []))
                paging = data.get("paging", {})
                cursor = paging.get("cursors", {}).get("after")
                if not paging.get("next") or not cursor or cursor == after:
                    break
                after = cursor
            if not pages:
                required = {"pages_show_list", "pages_read_engagement", "pages_manage_posts"}
                permissions = graph(config, "me/permissions", token=token)
                granted = {p.get("permission") for p in permissions.get("data", [])
                           if p.get("status") == "granted"}
                missing = sorted(required - granted)
                current_app.logger.warning(
                    "Facebook discovery: returned=%d with_token=%d with_create_content=%d missing_permissions=%s",
                    returned_count, token_count, publishing_count, ",".join(missing) or "none")
                if missing:
                    return error("Facebook did not grant these required permissions: " + ", ".join(missing)
                                 + ". Check your Meta login configuration and reconnect with the requested access.")
                if not returned_count:
                    return error("Facebook returned no Pages, although the required permissions are granted. "
                                 "Edit this app's access in Facebook Business Integrations to include your Page, "
                                 "and confirm the authorizing profile has Page access in your business portfolio.")
                return error("Facebook returned Pages, but none included both a Page access token and the "
                             "CREATE_CONTENT publishing task. Check the Page assignment for the authorizing "
                             "profile. Discovery counts are available in the server logs.")
            pending_id = secrets.token_urlsafe(32)
            with database() as db:
                db.execute("INSERT INTO pending VALUES (?, ?, ?, ?)",
                           (pending_id, str(session["user_id"]), time.time() + 600, json.dumps(pages)))
            session["facebook_pending"] = pending_id
            return redirect(url_for("facebook.choose_page"))
        except (requests.RequestException, ValueError, KeyError, sqlite3.Error):
            return error("Facebook connection could not be completed. Please try again and check the app configuration.", 502)

    @bp.route("/auth/facebook/pages", methods=["GET", "POST"])
    def choose_page():
        if "user_id" not in session:
            return redirect(url_for("home"))
        pending_id = session.get("facebook_pending", "")
        with database() as db:
            row = db.execute("SELECT pages FROM pending WHERE id = ? AND user_id = ? AND expires > ?",
                             (pending_id, str(session["user_id"]), time.time())).fetchone()
        if not row:
            return error("Page selection expired. Please connect Facebook again.")
        pages = json.loads(row[0])
        if request.method == "GET":
            return render_template("facebook_connect.html", pages=pages, csrf=pending_id)
        if not secrets.compare_digest(pending_id, request.form.get("csrf", "")):
            return error("Invalid selection request. Please try again.")
        page = next((p for p in pages if p["id"] == request.form.get("page_id")), None)
        if not page:
            return error("Select one of your available Pages.")
        try:
            users = get_sheet()
            for index, user in enumerate(users.get_all_records(), start=2):
                if str(user["user_id"]) == str(session["user_id"]):
                    users.update(range_name=f"E{index}:F{index}", values=[[page["access_token"], page["id"]]])
                    break
            else:
                return error("Your account could not be found.", 404)
        except Exception:
            return error("Could not save your connection. Please try selecting the Page again.", 503)
        with database() as db:
            db.execute("DELETE FROM pending WHERE id = ?", (pending_id,))
        session.pop("facebook_pending", None)
        return render_template("facebook_connect.html", connected=True)

    return bp
