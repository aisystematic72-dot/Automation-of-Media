# Video Automation

A Flask application for publishing selected images and videos to connected Facebook Pages and Instagram accounts. Google Sheets stores users and media records; Cloudinary hosts the media used for publishing.

## Features

- Signup and login with hashed passwords and Flask sessions.
- Email password recovery using a six-digit code valid for 10 minutes.
- Manual connection of Facebook and Instagram accounts using account IDs and access tokens.
- Media previews, batch selection, Facebook image/video publishing, and Instagram image/Reel publishing.
- Publishing results for each selected row; successful items are marked as posted.

## Project files

| File | Purpose |
| --- | --- |
| `app.py` | Flask routes, authentication, Google Sheets access, media hosting, and social publishing |
| `templates/` | Login/signup, dashboard, media selection, publishing results, and contact pages |
| `requirements.txt` | Python dependencies, including Flask, gspread, Cloudinary, requests, and Gunicorn |
| `.env` | Local configuration and secrets (ignored by Git) |

## Setup

1. Install Python and create a virtual environment. From the project folder in PowerShell:

   ```powershell
   python -m venv venv
   .\venv\Scripts\Activate.ps1
   python -m pip install -r requirements.txt
   ```

2. Enable Google Sheets and Google Drive APIs for a Google Cloud project, create a service account, and obtain its JSON credentials. Create a spreadsheet named **Video Automation** and share it with the service account email with Editor access.

3. Create a worksheet named **Users** with these exact headers in this order:

   ```text
   user_id | name | email | password | facebook_access_token | facebook_page_id | instagram_user_id | instagram_access_token
   ```

   Signup fills the first four columns. The dashboard connection forms fill the social account columns.

4. After signup, create a separate worksheet named exactly as the user's generated `user_id`. Use these headers in this order:

   ```text
   media_url | media_type | caption | posted | cloudinary_url
   ```

   Add accessible source URLs, set `media_type` to `image` or `video`, set `posted` to `No`, and leave `cloudinary_url` blank. The app does not create user media worksheets automatically.

5. Create `.env` with your own values:

   ```dotenv
   FLASK_SECRET_KEY=replace-with-a-long-random-secret
   GOOGLE_CREDENTIALS_JSON='{"type":"service_account","...":"replace with the complete service account JSON on one line"}'
   CLOUDINARY_CLOUD_NAME=your-cloud-name
   CLOUDINARY_API_KEY=your-api-key
   CLOUDINARY_API_SECRET=your-api-secret
   MAIL_USERNAME=your-gmail-address
   MAIL_PASSWORD=your-gmail-app-password
   MAIL_SENDER=your-sender-email
   SUPPORT_EMAIL=your-support-email
   ```

   Replace the JSON example with the **complete** service account credentials. The app reads credentials from `GOOGLE_CREDENTIALS_JSON`; it does not load `credentials.json`. Password reset emails use Gmail SMTP over SSL on port 465. `SUPPORT_EMAIL` is loaded but currently unused.

6. Start the app:

   ```powershell
   python app.py
   ```

   Open `http://127.0.0.1:5000`. This command starts Flask in debug mode for local development.

## Usage and data flow

1. Sign up, log in, and enter the Facebook Page ID/token and/or Instagram account ID/token on the dashboard. These credentials must allow publishing to the intended accounts.
2. Populate the user's media worksheet, then open `/videos`. Rows marked `No` are uploaded to Cloudinary when their hosted URL is missing; that URL is saved in column 5.
3. Select media and publish. Each item is sent to every connected platform. Instagram containers are polled until processing completes before publishing.
4. Review the results. Column 4 becomes `Yes` only when publishing succeeds on all connected platforms.

## Routes

### Facebook login setup

The dashboard's Connect Facebook link opens Meta authorization in a new tab.
After approval, users choose a Page with publishing access. The Page ID and token
are saved in the existing Users worksheet columns E/F for the Publish route.
Refresh the original dashboard after connecting, or use Return to dashboard.

Set these variables locally in `.env` and in Railway's service Variables:

```dotenv
FACEBOOK_APP_ID=1624904499171672
FACEBOOK_APP_SECRET=your-private-meta-app-secret
FACEBOOK_CONFIG_ID=2808537102880969
FACEBOOK_REDIRECT_URI=https://automation-production-03f0.up.railway.app/auth/facebook/callback
FACEBOOK_GRAPH_VERSION=v24.0
```

Use a Graph API version supported by your Meta app; v24.0 is the example above.
Register the exact redirect URI in Facebook Login for Business settings. The
configuration must use General login, User access token, and permissions
`pages_show_list`, `pages_read_engagement`, and `pages_manage_posts`.
Standard Access/development mode only supports eligible users with app roles.
Public users require Meta's relevant production approvals.

Temporary Page tokens are stored server-side for ten minutes in
`facebook_oauth.sqlite3` (ignored by Git), never in browser cookies or HTML.
`FACEBOOK_OAUTH_DB` can override the database path. This SQLite setup supports
multiple workers on one instance; multiple replicas need a shared server-side
store. A restart may require repeating an unfinished connection. Selected Page
tokens remain in your existing Google Sheet: restrict access to that sheet.

Deploy the code after setting the variables. A live test must connect an app-role
user, choose a Page, and publish a selected item. No Meta credentials are bundled
in the code and no Facebook password is collected.

Run isolated authorization checks with `python -m unittest test_facebook_connect`.

| Method | Routes | Purpose |
| --- | --- | --- |
| GET | `/`, `/dashboard`, `/videos` | Authentication page, account dashboard, and pending media |
| POST | `/signup`, `/login` | Account creation and login |
| POST | `/forgot-password`, `/verify-reset-code`, `/reset-password` | Email password recovery |
| POST | `/connect-facebook`, `/connect-instagram` | Store social account credentials |
| POST | `/publish` | Publish selected media |
| GET | `/contact-team`, `/test-cloudinary` | Contact page and Cloudinary configuration check |

## Operational notes

- Publishing runs synchronously and may take several minutes for videos; there is no scheduler or background queue.
- Password reset codes are held in process memory and disappear on restart; multiple workers do not share them.
- Partial publishing failures leave the row unposted. Retrying can duplicate a post on a platform that already succeeded.
- `/test-cloudinary` checks that configuration values exist; it does not test a live upload. Keep `.env`, service credentials, and the spreadsheet containing access tokens private.
- For deployment, disable debug mode and use a production WSGI server. Gunicorn is included for Linux hosting (`gunicorn app:app`); configure request timeouts for long publishing operations.

## Resume bullet

- Built a Flask app integrating Google Sheets, Cloudinary, and Meta Graph API to publish images and videos to Facebook and Instagram, with user authentication and email password recovery.
