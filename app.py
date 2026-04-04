import os
import json
import time
import requests
from datetime import datetime

from flask import Flask, request, render_template, redirect, session
import psycopg2
from psycopg2.extras import DictCursor

import gspread
from gspread.exceptions import APIError
from google.oauth2.service_account import Credentials

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "hostel-secret")

VERIFY_TOKEN = "hostel123"

# =========================
# DATABASE
# =========================

def get_db_connection():
    return psycopg2.connect(os.environ.get("DATABASE_URL"), sslmode="require")

def init_db():
    conn = get_db_connection()
    cur = conn.cursor()

    cur.execute("""
    CREATE TABLE IF NOT EXISTS message_logs (
        id SERIAL PRIMARY KEY,
        roll_number VARCHAR(20),
        phone VARCHAR(15),
        message_id TEXT,
        status VARCHAR(20),
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    """)

    conn.commit()
    cur.close()
    conn.close()

init_db()

# =========================
# PHONE FORMAT
# =========================

def format_phone(phone):
    phone = ''.join(filter(str.isdigit, str(phone)))
    if phone.startswith("0"):
        phone = phone[1:]
    if not phone.startswith("91"):
        phone = "91" + phone
    return phone

# =========================
# WHATSAPP SEND
# =========================

TOKEN = os.environ.get("WHATSAPP_TOKEN")
PHONE_ID = os.environ.get("PHONE_NUMBER_ID")

@app.route("/delete-student", methods=["POST"])
def delete_student():
    roll = request.form.get("roll").strip().upper()

    conn = get_db_connection()
    cur = conn.cursor()

    cur.execute("DELETE FROM students WHERE roll_number=%s", (roll,))

    conn.commit()
    cur.close()
    conn.close()

    return redirect("/")

def send_whatsapp(phone, roll, name):
    phone = format_phone(phone)

    url = f"https://graph.facebook.com/v18.0/{PHONE_ID}/messages"

    payload = {
    "messaging_product": "whatsapp",
    "to": phone,
    "type": "template",
    "template": {
        "name": "hostel_details",
        "language": {"code": "en"},
        "components": [
            {
                "type": "body",
                "parameters": [
                    {"type": "text", "text": name},
                    {"type": "text", "text": roll},
                    {"type": "text", "text": "CSE"},
                    {"type": "text", "text": "101"},
                    {"type": "text", "text": "Test Reason"},
                    {"type": "text", "text": "1"},
                    {"type": "text", "text": "2026-04-04"},
                    {"type": "text", "text": "2026-04-04"}
                ]
            }
        ]
    }
}
    headers = {
        "Authorization": f"Bearer {TOKEN}",
        "Content-Type": "application/json"
    }

    try:
        res = requests.post(url, headers=headers, json=payload)
        response = res.json()

        print("📨 Response:", response)

        if "messages" in response:
            message_id = response["messages"][0]["id"]
            status = "sent"
        else:
            message_id = None
            status = "failed"

    except Exception as e:
        print("❌ Error:", e)
        message_id = None
        status = "error"

    # SAVE TO DB
    conn = get_db_connection()
    cur = conn.cursor()

    cur.execute("""
    INSERT INTO message_logs (roll_number, phone, message_id, status)
    VALUES (%s,%s,%s,%s)
    """, (roll, phone, message_id, status))

    conn.commit()
    cur.close()
    conn.close()

# =========================
# WEBHOOK (VERIFY + STATUS)
# =========================

@app.route("/webhook", methods=["GET", "POST"])
def webhook():

    # ✅ VERIFY (FIXED)
    if request.method == "GET":
        mode = request.args.get("hub.mode")
        token = request.args.get("hub.verify_token")
        challenge = request.args.get("hub.challenge")

        print("MODE:", mode)
        print("TOKEN:", token)
        print("CHALLENGE:", challenge)

        if mode == "subscribe" and token == VERIFY_TOKEN:
            print("✅ WEBHOOK VERIFIED")
            return challenge, 200
        else:
            print("❌ VERIFICATION FAILED")
            return "Forbidden", 403

    # ✅ RECEIVE STATUS UPDATE
    if request.method == "POST":
        data = request.get_json()
        print("📩 Webhook:", data)

        try:
            statuses = data["entry"][0]["changes"][0]["value"].get("statuses")

            if statuses:
                for status in statuses:
                    message_id = status["id"]
                    status_text = status["status"]

                    conn = get_db_connection()
                    cur = conn.cursor()

                    cur.execute("""
                    UPDATE message_logs
                    SET status=%s
                    WHERE message_id=%s
                    """, (status_text, message_id))

                    conn.commit()
                    cur.close()
                    conn.close()

                    print(f"✅ Updated {message_id} → {status_text}")

        except Exception as e:
            print("⚠️ Webhook error:", e)

        return "OK", 200
@app.route("/")
def dashboard():

    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=DictCursor)

    cur.execute("""
    SELECT roll_number, phone, status, created_at
    FROM message_logs
    ORDER BY created_at DESC
    LIMIT 20
    """)

    data = cur.fetchall()

    cur.execute("SELECT COUNT(*) FROM message_logs WHERE status='sent'")
    sent = cur.fetchone()[0]

    cur.execute("SELECT COUNT(*) FROM message_logs WHERE status='delivered'")
    delivered = cur.fetchone()[0]

    cur.execute("SELECT COUNT(*) FROM message_logs WHERE status='read'")
    read = cur.fetchone()[0]

    cur.close()
    conn.close()

    return render_template(
        "dashboard.html",
        logs=data,
        sent=sent,
        delivered=delivered,
        read=read
    )

# =========================
# TEST SEND
# =========================

@app.route("/send-test")
def test():
    send_whatsapp("919999999999", "TEST01", "Demo Student")
    return "Message Sent"

# =========================
# RUN
# =========================

if __name__ == "__main__":
    app.run(debug=True)
