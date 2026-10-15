from flask import Flask, render_template, request
from werkzeug.security import generate_password_hash, check_password_hash
from flask import Flask, render_template, request, session, redirect, url_for
import gspread
import time
from gspread.exceptions import APIError
import uuid
import json
import os
import cloudinary
import traceback
import smtplib
import random
from datetime import datetime, timedelta
from email.mime.text import MIMEText
import requests
from facebook_connect import create_facebook_blueprint
import cloudinary.uploader

from dotenv import load_dotenv

from google.oauth2.service_account import Credentials
from werkzeug.security import generate_password_hash

load_dotenv()


MAIL_USERNAME = os.getenv("MAIL_USERNAME")
MAIL_PASSWORD = os.getenv("MAIL_PASSWORD")
MAIL_SENDER = os.getenv("MAIL_SENDER")
SUPPORT_EMAIL = os.getenv("SUPPORT_EMAIL")
secret_key=os.getenv("FLASK_SECRET_KEY")

password_reset_codes = {}

cloudinary.config(
    cloud_name=os.getenv("CLOUDINARY_CLOUD_NAME"),
    api_key=os.getenv("CLOUDINARY_API_KEY"),
    api_secret=os.getenv("CLOUDINARY_API_SECRET"),
    secure=True
)

app = Flask(__name__)
app.secret_key = secret_key

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive"
]

google_credentials = json.loads(
    os.getenv("GOOGLE_CREDENTIALS_JSON")
)

credentials = Credentials.from_service_account_info(
    google_credentials,
    scopes=SCOPES
)

client = gspread.authorize(credentials)

# Replace with your actual Google Sheet name
sheet = client.open("Video Automation").worksheet("Users")
app.register_blueprint(create_facebook_blueprint(lambda: sheet))

def send_reset_code_email(receiver_email, code):

    subject = "Password Reset Verification Code"

    body = f"""
Hello,

Your password reset verification code is:

{code}

This code will expire in 10 minutes.

If you did not request a password reset, you can ignore this email.
"""

    message = MIMEText(body)

    message["Subject"] = subject
    message["From"] = MAIL_SENDER
    message["To"] = receiver_email

    try:

        with smtplib.SMTP_SSL(
            "smtp.gmail.com",
            465
        ) as server:

            server.login(
                MAIL_USERNAME,
                MAIL_PASSWORD
            )

            server.sendmail(
                MAIL_SENDER,
                receiver_email,
                message.as_string()
            )

        return True

    except Exception as error:

        print("Email sending error:", error)

        return False

@app.route("/forgot-password", methods=["POST"])
def forgot_password():

    email = request.form["email"].strip().lower()

    records = sheet.get_all_records()

    matched_user = None

    for user in records:
        if user["email"].strip().lower() == email:
            matched_user = user
            break

    if not matched_user:
        return {
            "success": False,
            "message": "We couldn't verify that email. Please check it and try again."
        }, 404

    code = str(random.randint(100000, 999999))

    expiry = datetime.now() + timedelta(minutes=10)

    password_reset_codes[email] = {
        "code": code,
        "expiry": expiry,
        "verified": False
    }

    email_sent = send_reset_code_email(
        email,
        code
    )

    if not email_sent:

        password_reset_codes.pop(
            email,
            None
        )

        return {
            "success": False,
            "message": "Could not send verification code. Please try again."
        }, 500

    return {
        "success": True,
        "message": "Verification code sent successfully."
    }

@app.route("/verify-reset-code", methods=["POST"])
def verify_reset_code():

    email = request.form["email"].strip().lower()
    code = request.form["code"].strip()

    reset_data = password_reset_codes.get(email)

    if not reset_data:
        return {
            "success": False,
            "message": "No password reset request was found. Please request a new code."
        }, 400

    # Check expiry
    if datetime.now() > reset_data["expiry"]:

        password_reset_codes.pop(
            email,
            None
        )

        return {
            "success": False,
            "message": "Verification code has expired. Please request a new code."
        }, 400

    # Check code
    if reset_data["code"] != code:

        return {
            "success": False,
            "message": "Incorrect verification code."
        }, 400

    # Mark code as verified
    reset_data["verified"] = True

    return {
        "success": True,
        "message": "Code verified successfully."
    }

@app.route("/reset-password", methods=["POST"])
def reset_password():

    email = request.form["email"].strip().lower()
    new_password = request.form["new_password"]
    confirm_password = request.form["confirm_password"]

    reset_data = password_reset_codes.get(email)

    if not reset_data:
        return {
            "success": False,
            "message": "No verified password reset request was found."
        }, 400

    if not reset_data.get("verified"):
        return {
            "success": False,
            "message": "Please verify your code first."
        }, 400

    if datetime.now() > reset_data["expiry"]:

        password_reset_codes.pop(
            email,
            None
        )

        return {
            "success": False,
            "message": "Password reset session has expired. Please request a new code."
        }, 400

    if not new_password:
        return {
            "success": False,
            "message": "Please enter a new password."
        }, 400

    if new_password != confirm_password:
        return {
            "success": False,
            "message": "Passwords do not match."
        }, 400

    if len(new_password) < 8:
        return {
            "success": False,
            "message": "Password must be at least 8 characters long."
        }, 400

    records = sheet.get_all_records()

    user_row = None

    for row_number, user in enumerate(
        records,
        start=2
    ):

        if user["email"].strip().lower() == email:

            user_row = row_number
            break

    if not user_row:

        return {
            "success": False,
            "message": "User account could not be found."
        }, 404

    hashed_password = generate_password_hash(
        new_password
    )

    # Column 4 = password
    sheet.update_cell(
        user_row,
        4,
        hashed_password
    )

    # Remove the reset code completely
    password_reset_codes.pop(
        email,
        None
    )

    return {
        "success": True,
        "message": "Password updated successfully. You can now log in."
    }

@app.route("/")
def home():
    return render_template("index.html")


@app.route("/signup", methods=["POST"])
def signup():
    name = request.form["name"]
    email = request.form["email"].strip().lower()
    password = request.form["password"]

    existing_emails = sheet.col_values(3)

    if email in [item.strip().lower() for item in existing_emails]:
        return "This email already exists. Go for the Login option OR Contact the team."

    user_id = str(uuid.uuid4())

    hashed_password = generate_password_hash(password)

    sheet.append_row([
        user_id,
        name,
        email,
        hashed_password
    ])

    return "Account created successfully!"

@app.route("/login", methods=["POST"])
def login():
    email = request.form["email"].strip().lower()
    password = request.form["password"]

    records = sheet.get_all_records()

    for user in records:
        if user["email"].strip().lower() == email:
            if check_password_hash(user["password"], password):
                session["user_id"] = user["user_id"]
                return redirect(url_for("dashboard"))
            else:
                return "Incorrect password."

    return "Email not found. Please sign up first."

@app.route("/connect-facebook", methods=["POST"])
def connect_facebook():
    if "user_id" not in session:
        return redirect(url_for("home"))

    access_token = request.form["access_token"]
    page_id = request.form["page_id"]
    user_id = session["user_id"]

    records = sheet.get_all_records()

    for index, user in enumerate(records, start=2):
        if str(user["user_id"]) == str(user_id):
            sheet.update_cell(index, 5, access_token)
            sheet.update_cell(index, 6, page_id)

            return redirect(url_for("dashboard"))

    return "User not found."

@app.route("/dashboard")
def dashboard():
    if "user_id" not in session:
        return redirect(url_for("home"))

    user_id = str(session["user_id"])
    records = sheet.get_all_records()

    facebook_connected = False
    instagram_connected = False

    for user in records:

        if str(user["user_id"]) == user_id:

            if (
                user.get("facebook_access_token")
                and user.get("facebook_page_id")
            ):
                facebook_connected = True

            if (
                user.get("instagram_user_id")
                and user.get("instagram_access_token")
            ):
                instagram_connected = True

            break

    return render_template(
        "dashboard.html",
        facebook_connected=facebook_connected,
        instagram_connected=instagram_connected
    )

@app.route("/videos")
def videos():
    if "user_id" not in session:
        return redirect(url_for("home"))

    user_id = str(session["user_id"])

    try:
        user_sheet = client.open("Video Automation").worksheet(user_id)
    except gspread.exceptions.WorksheetNotFound:
        return f"No media sheet found for user {user_id}"

    records = user_sheet.get_all_records()
    media_items = []

    for row_number, row in enumerate(records, start=2):

        if str(row.get("posted", "")).strip().lower() != "no":
            continue

        media_url = row.get("media_url")
        media_type = str(row.get("media_type", "")).strip().lower()
        caption = row.get("caption", "")
        cloudinary_url = row.get("cloudinary_url", "")

        try:
            # Upload only if not already uploaded
            if not cloudinary_url:

                resource_type = (
                    "video"
                    if media_type == "video"
                    else "image"
                )

                result = cloudinary.uploader.upload(
                    media_url,
                    resource_type=resource_type,
                    folder=f"video_automation/{user_id}"
                )

                cloudinary_url = result["secure_url"]

                # Save Cloudinary URL into column 5
                user_sheet.update_cell(
                    row_number,
                    5,
                    cloudinary_url
                )

            media_items.append({
                "row_number": row_number,
                "cloudinary_url": cloudinary_url,
                "media_type": media_type,
                "caption": caption
            })

        except Exception as error:
            print("Cloudinary error:", error)

    return render_template(
        "videos.html",
        media_items=media_items
    )

@app.route("/publish", methods=["POST"])
def publish():

    if "user_id" not in session:
        return redirect(url_for("home"))

    selected_rows = request.form.getlist("media")

    if not selected_rows:
        return render_template(
            "publish_result.html",
            results=[{
                "row": "-",
                "success": False,
                "error": "No media selected."
            }]
        )

    user_id = str(session["user_id"])

    # ==========================================
    # GET USER SOCIAL ACCOUNT CREDENTIALS
    # ==========================================

    users = sheet.get_all_records()

    facebook_access_token = None
    facebook_page_id = None

    instagram_user_id = None
    instagram_access_token = None

    for user in users:
        if str(user.get("user_id", "")).strip() == str(user_id).strip():

            facebook_access_token = str(
                user.get("facebook_access_token", "")
            ).strip()

            facebook_page_id = str(
                user.get("facebook_page_id", "")
            ).strip()

            instagram_user_id = str(
                user.get("instagram_user_id", "")
            ).strip()

            instagram_access_token = str(
                user.get("instagram_access_token", "")
            ).strip()

            break

    # ==========================================
    # DETERMINE WHICH ACCOUNTS ARE CONNECTED
    # ==========================================

    facebook_connected = bool(
        facebook_access_token and facebook_page_id
    )

    instagram_connected = bool(
        instagram_user_id and instagram_access_token
    )

    # ==========================================
    # OPEN USER MEDIA SHEET
    # ==========================================

    try:

        user_sheet = client.open(
            "Video Automation"
        ).worksheet(user_id)

    except gspread.exceptions.WorksheetNotFound:

        return render_template(
            "publish_result.html",
            results=[{
                "row": "-",
                "success": False,
                "error": "User media sheet not found."
            }]
        )

    results = []

    # ==========================================
    # PROCESS SELECTED MEDIA
    # ==========================================

    for row_number in selected_rows:

        try:

            row_number = int(row_number)

            row_values = user_sheet.row_values(
                row_number
            )

            if len(row_values) < 5:

                results.append({
                    "row": row_number,
                    "success": False,
                    "error": "Media row is incomplete."
                })

                continue

            media_type = row_values[1].strip().lower()
            caption = row_values[2]
            cloudinary_url = row_values[4]

            facebook_success = False
            instagram_success = False

            errors = []
            creation_id = None

            # ======================================
            # IMAGE
            # ======================================

            if media_type == "image":

                # ----------------------------------
                # FACEBOOK IMAGE
                # ----------------------------------

                if facebook_connected:

                    fb_response = requests.post(
                        f"https://graph.facebook.com/{facebook_page_id}/photos",
                        data={
                            "url": cloudinary_url,
                            "caption": caption,
                            "access_token": facebook_access_token
                        },
                        timeout=60
                    )

                    fb_result = fb_response.json()

                    if fb_response.ok:

                        facebook_success = True

                    else:

                        errors.append(
                            "Facebook: " +
                            fb_result.get(
                                "error",
                                {}
                            ).get(
                                "message",
                                "Unknown Facebook error."
                            )
                        )

                # ----------------------------------
                # INSTAGRAM IMAGE
                # ----------------------------------

                if instagram_connected:

                    create_response = requests.post(
                        f"https://graph.facebook.com/{instagram_user_id}/media",
                        data={
                            "image_url": cloudinary_url,
                            "caption": caption,
                            "access_token": instagram_access_token
                        },
                        timeout=60
                    )

                    create_result = create_response.json()
                    print("Instagram image create response:", create_result)

                    if create_response.ok:

                        creation_id = create_result.get("id")

                        if not creation_id:
                            errors.append(
                                "Instagram: Media container ID was not returned."
                            )

                        else:

                            media_ready = False
                            media_failed = False

                            for attempt in range(20):

                                time.sleep(2)

                                status_response = requests.get(
                                    f"https://graph.facebook.com/{creation_id}",
                                    params={
                                        "fields": "status_code,status",
                                        "access_token": instagram_access_token
                                    },
                                    timeout=30
                                )

                                status_result = status_response.json()

                                print(
                                    f"Instagram image status attempt {attempt + 1}:",
                                    status_result
                                )

                                status_code = status_result.get("status_code")

                                if status_code == "FINISHED":

                                    media_ready = True
                                    break

                                elif status_code == "ERROR":

                                    media_failed = True

                                    errors.append(
                                        "Instagram: " +
                                        status_result.get(
                                            "status",
                                            "Media processing failed."
                                        )
                                    )

                                    break

                            if media_ready:

                                publish_response = requests.post(
                                    f"https://graph.facebook.com/{instagram_user_id}/media_publish",
                                    data={
                                        "creation_id": creation_id,
                                        "access_token": instagram_access_token
                                    },
                                    timeout=60
                                )

                                publish_result = publish_response.json()

                                if publish_response.ok:

                                    instagram_success = True

                                else:

                                    errors.append(
                                        "Instagram: " +
                                        publish_result.get(
                                            "error",
                                            {}
                                        ).get(
                                            "message",
                                            "Instagram publish failed."
                                        )
                                    )

                            elif not media_failed:

                                errors.append(
                                    "Instagram: Media processing timed out."
                                )

                    else:

                        errors.append(
                            "Instagram: " +
                            create_result.get(
                                "error",
                                {}
                            ).get(
                                "message",
                                "Instagram media creation failed."
                            )
                        )

            # ======================================
            # VIDEO / REEL
            # ======================================

            elif media_type == "video":

                # ----------------------------------
                # FACEBOOK VIDEO
                # ----------------------------------

                if facebook_connected:

                    fb_response = requests.post(
                        f"https://graph.facebook.com/{facebook_page_id}/videos",
                        data={
                            "file_url": cloudinary_url,
                            "description": caption,
                            "access_token": facebook_access_token
                        },
                        timeout=300
                    )

                    fb_result = fb_response.json()

                    if fb_response.ok:

                        facebook_success = True

                    else:

                        errors.append(
                            "Facebook: " +
                            fb_result.get(
                                "error",
                                {}
                            ).get(
                                "message",
                                "Unknown Facebook error."
                            )
                        )

                # ----------------------------------
                # INSTAGRAM REEL
                # ----------------------------------

                if instagram_connected:

                    create_response = requests.post(
                        f"https://graph.facebook.com/{instagram_user_id}/media",
                        data={
                            "media_type": "REELS",
                            "video_url": cloudinary_url,
                            "caption": caption,
                            "access_token": instagram_access_token
                        },
                        timeout=60
                    )

                    create_result = create_response.json()

                    print("Instagram Reel create response:", create_result)

                    if create_response.ok:

                        creation_id = create_result.get("id")

                        if not creation_id:

                            errors.append(
                                "Instagram: Reel container ID was not returned."
                            )

                        else:

                            # ==================================
                            # WAIT FOR INSTAGRAM PROCESSING
                            # ==================================

                            reel_ready = False
                            reel_failed = False

                            for attempt in range(60):

                                time.sleep(5)

                                status_response = requests.get(
                                    f"https://graph.facebook.com/{creation_id}",
                                    params={
                                        "fields": "status_code,status",
                                        "access_token": instagram_access_token
                                    },
                                    timeout=30
                                )

                                status_result = status_response.json()

                                status_code = status_result.get("status_code")

                                print(
                                    f"Instagram Reel status attempt {attempt + 1}:",
                                    status_result
                                )

                                if status_code == "FINISHED":

                                    reel_ready = True
                                    break

                                elif status_code == "ERROR":

                                    reel_failed = True

                                    processing_error = status_result.get(
                                        "status",
                                        "Instagram Reel processing failed."
                                    )

                                    errors.append(
                                        f"Instagram: {processing_error}"
                                    )

                                    break

                            # ==================================
                            # PUBLISH INSTAGRAM REEL
                            # ==================================

                            if reel_ready:

                                publish_response = requests.post(
                                    f"https://graph.facebook.com/{instagram_user_id}/media_publish",
                                    data={
                                        "creation_id": creation_id,
                                        "access_token": instagram_access_token
                                    },
                                    timeout=60
                                )

                                publish_result = publish_response.json()

                                if publish_response.ok:

                                    instagram_success = True

                                else:

                                    errors.append(
                                        "Instagram: " +
                                        publish_result.get(
                                            "error",
                                            {}
                                        ).get(
                                            "message",
                                            "Instagram Reel publish failed."
                                        )
                                    )

                            elif not reel_failed:

                                errors.append(
                                    "Instagram: Reel processing took longer than 5 minutes."
                                )

                    else:

                        errors.append(
                            "Instagram: " +
                            create_result.get(
                                "error",
                                {}
                            ).get(
                                "message",
                                "Instagram Reel creation failed."
                            )
                        )

            # ======================================
            # UNSUPPORTED MEDIA TYPE
            # ======================================

            else:

                results.append({
                    "row": row_number,
                    "success": False,
                    "error": (
                        f"Unsupported media type: "
                        f"{media_type}"
                    )
                })

                continue

            # ======================================
            # FINAL RESULT
            # ======================================

            platform_results = []

            # Only include platforms that are
            # actually connected.

            if facebook_connected:
                platform_results.append(
                    facebook_success
                )

            if instagram_connected:
                platform_results.append(
                    instagram_success
                )

            # --------------------------------------
            # NO SOCIAL ACCOUNT CONNECTED
            # --------------------------------------

            if not platform_results:

                results.append({
                    "row": row_number,
                    "success": False,
                    "error": (
                        "No social account is connected."
                    )
                })

                continue

            # --------------------------------------
            # ALL CONNECTED ACCOUNTS SUCCESSFUL
            # --------------------------------------

            if all(platform_results):

                # Update posted column:
                # Column 4 = posted

                for attempt in range(3):

                    try:

                        user_sheet.update_cell(
                            row_number,
                            4,
                            "Yes"
                        )

                        break

                    except APIError as error:

                        if attempt < 2:

                            time.sleep(2)

                        else:

                            raise error

                results.append({
                    "row": row_number,
                    "success": True,
                    "error": None
                })

            # --------------------------------------
            # ONE OR MORE CONNECTED ACCOUNTS FAILED
            # --------------------------------------

            else:

                results.append({
                    "row": row_number,
                    "success": False,
                    "error": (
                        " | ".join(errors)
                        if errors
                        else
                        "Publishing failed."
                    )
                })

        except Exception as error:

            print(f"ERROR while publishing row {row_number}:")
            traceback.print_exc()

            results.append({
                "row": row_number,
                "success": False,
                "error": str(error)
            })

    # ==========================================
    # SHOW RESULTS
    # ==========================================

    return render_template(
        "publish_result.html",
        results=results
    )

@app.route("/test-cloudinary")
def test_cloudinary():
    config = cloudinary.config()

    if (
        config.cloud_name
        and config.api_key
        and config.api_secret
    ):
        return "Cloudinary connected successfully!"

    return "Cloudinary configuration missing."

@app.route("/connect-instagram", methods=["POST"])
def connect_instagram():
    if "user_id" not in session:
        return redirect(url_for("home"))

    instagram_user_id = request.form["instagram_user_id"].strip()
    instagram_access_token = request.form["instagram_access_token"].strip()

    user_id = str(session["user_id"])
    records = sheet.get_all_records()

    for row_number, user in enumerate(records, start=2):

        if str(user["user_id"]) == user_id:

            # Column 7 = instagram_user_id
            # Column 8 = instagram_access_token
            sheet.update_cell(row_number, 7, instagram_user_id)
            sheet.update_cell(row_number, 8, instagram_access_token)

            return redirect(url_for("dashboard"))

    return "User not found."

@app.route("/contact-team")
def contact_team():
    return render_template("contact_team.html")


if __name__ == "__main__":
    app.run(debug=True)
