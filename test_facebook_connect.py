import os
import tempfile
import unittest
from unittest.mock import Mock, patch
from flask import Flask, session
from facebook_connect import create_facebook_blueprint


class FacebookConnectionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {
            "FACEBOOK_APP_ID": "app", "FACEBOOK_APP_SECRET": "secret",
            "FACEBOOK_CONFIG_ID": "config", "FACEBOOK_GRAPH_VERSION": "v24.0",
            "FACEBOOK_REDIRECT_URI": "https://example.com/auth/facebook/callback",
            "FACEBOOK_OAUTH_DB": os.path.join(self.temp.name, "pending.sqlite3")})
        self.env.start()
        self.sheet = Mock()
        self.sheet.get_all_records.return_value = [{"user_id": "user"}]
        app = Flask(__name__)
        app.secret_key = "test-secret"
        app.testing = True
        app.add_url_rule("/", "home", lambda: "home")
        app.add_url_rule("/dashboard", "dashboard", lambda: "dashboard")
        app.register_blueprint(create_facebook_blueprint(lambda: self.sheet))
        self.client = app.test_client()
        with self.client.session_transaction() as state:
            state["user_id"] = "user"

    def tearDown(self):
        self.env.stop()
        self.temp.cleanup()

    @patch("facebook_connect.requests.get")
    def test_invalid_state_never_exchanges_code(self, get):
        self.client.get("/auth/facebook")
        self.assertEqual(self.client.get("/auth/facebook/callback?state=wrong&code=code").status_code, 400)
        get.assert_not_called()

    @patch("facebook_connect.requests.get")
    def test_empty_pages_identifies_missing_permission_without_leaking_tokens(self, get):
        responses = [{"access_token": "short-secret"}, {"access_token": "long-secret"},
                     {"data": []}, {"data": [
                         {"permission": "pages_show_list", "status": "granted"},
                         {"permission": "pages_read_engagement", "status": "granted"}]}]
        get.side_effect = [Mock(ok=True, json=Mock(return_value=data)) for data in responses]
        self.client.get("/auth/facebook")
        with self.client.session_transaction() as state:
            oauth_state = state["facebook_oauth"]["state"]
        with self.assertLogs(self.client.application.logger, level="WARNING") as logs:
            response = self.client.get(f"/auth/facebook/callback?state={oauth_state}&code=code")
        self.assertIn(b"pages_manage_posts", response.data)
        self.assertIn("returned=0", str(logs.output))
        self.assertNotIn("long-secret", str(logs.output))
        self.assertNotIn(b"long-secret", response.data)
        self.sheet.update.assert_not_called()

    @patch("facebook_connect.requests.get")
    def test_authorize_select_and_store_page_token(self, get):
        responses = [{"access_token": "short"}, {"access_token": "long"},
                     {"data": [{"id": "123", "name": "My Page", "access_token": "page-secret", "tasks": ["CREATE_CONTENT"]}]}]
        get.side_effect = [Mock(ok=True, json=Mock(return_value=data)) for data in responses]
        self.client.get("/auth/facebook")
        with self.client.session_transaction() as state:
            oauth_state = state["facebook_oauth"]["state"]
        response = self.client.get(f"/auth/facebook/callback?state={oauth_state}&code=code")
        self.assertEqual(response.status_code, 302)
        response = self.client.get("/auth/facebook/pages")
        self.assertIn(b"My Page", response.data)
        self.assertNotIn(b"page-secret", response.data)
        with self.client.session_transaction() as state:
            csrf = state["facebook_pending"]
            self.assertNotIn("page-secret", str(dict(state)))
        self.assertEqual(self.client.post("/auth/facebook/pages", data={"page_id": "123", "csrf": "wrong"}).status_code, 400)
        self.assertEqual(self.client.post("/auth/facebook/pages", data={"page_id": "999", "csrf": csrf}).status_code, 400)
        self.sheet.update.assert_not_called()
        response = self.client.post("/auth/facebook/pages", data={"page_id": "123", "csrf": csrf})
        self.assertEqual(response.status_code, 200)
        self.sheet.update.assert_called_once_with(range_name="E2:F2", values=[["page-secret", "123"]])
        self.assertEqual(self.client.get("/auth/facebook/pages").status_code, 400)


if __name__ == "__main__":
    unittest.main()
