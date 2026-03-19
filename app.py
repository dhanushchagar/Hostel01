import os
import json
import time
import requests
from datetime import datetime

from flask import Flask, request, render_template, redirect
import psycopg2
from psycopg2.extras import DictCursor
import gspread
from gspread.exceptions import APIError
from google.oauth2.service_account import Credentials

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "hostel-secret")

# =====================================================
# DATABASE CONNECTION
# =====================================================

def get_db_connection():
    return psycopg2.connect(
        os.environ.get("DATABASE_URL"),
        sslmode="require"
    )

def init_db():
    conn = get_db_connection()
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS students (
            roll_number VARCHAR(20) PRIMARY KEY,
            name VARCHAR(100),
            department VARCHAR(50),
            room VARCHAR(20),
            student_phone VARCHAR(15),
            parent_phone VARCHAR(15)
        );
    """)

    conn.commit()
    cur.close()
    conn.close()

init_db()

def get_student_details(roll_number):
    roll_number = roll_number.strip().upper()

    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=DictCursor)

    cur.execute("SELECT * FROM students WHERE roll_number = %s", (roll_number,))
    result = cur.fetchone()

    cur.close()
    conn.close()

    return result

# =====================================================
# GOOGLE SHEETS
# =====================================================

def save_to_google_sheets(data):
    try:
        creds_json = json.loads(os.environ.get("GOOGLE_CREDENTIALS"))

        credentials = Credentials.from_service_account_info(
            creds_json,
            scopes=[
                "https://www.googleapis.com/auth/spreadsheets",
                "https://www.googleapis.com/auth/drive"
            ],
        )

        gc = gspread.authorize(credentials)

        for attempt in range(3):
            try:
                sheet = gc.open("Hostel Leave Records").sheet1
                sheet.append_row(data)
                return True
            except APIError as e:
                print("Google Sheets API error:", e)
                time.sleep(3)

    except Exception as e:
        print("Google Sheets connection error:", e)

    return False

# =====================================================
# WHATSAPP API
# =====================================================

WHATSAPP_TOKEN = os.environ.get("WHATSAPP_TOKEN")
PHONE_NUMBER_ID = os.environ.get("PHONE_NUMBER_ID")

def format_phone(phone):
    phone = phone.strip().replace("+", "")
    if not phone.startswith("91"):
        phone = "91" + phone
    return phone

def send_whatsapp_message(phone, action, name, roll, dept, room, reason, days, start, end, use_template=False):
    """
    Sends WhatsApp message via Meta Cloud API.
    If use_template=True, will send a template message instead of normal text.
    """
    formatted = format_phone(phone)
    url = f"https://graph.facebook.com/v18.0/{PHONE_NUMBER_ID}/messages"
    headers = {
        "Authorization": f"Bearer {WHATSAPP_TOKEN}",
        "Content-Type": "application/json"
    }

    if use_template:
        # TEMPLATE MESSAGE STRUCTURE
        data = {
            "messaging_product": "whatsapp",
            "to": formatted,
            "type": "template",
            "template": {
                "name": "leave_approval",  # Template name from Meta Dashboard
                "language": {"code": "en_US"},
                "components": [
                    {
                        "type": "body",
                        "parameters": [
                            {"type": "text", "text": name},
                            {"type": "text", "text": roll},
                            {"type": "text", "text": dept},
                            {"type": "text", "text": room},
                            {"type": "text", "text": reason},
                            {"type": "text", "text": days},
                            {"type": "text", "text": start},
                            {"type": "text", "text": end},
                            {"type": "text", "text": action}
                        ]
                    }
                ]
            }
        }
    else:
        # NORMAL TEXT MESSAGE
        data = {
            "messaging_product": "whatsapp",
            "to": formatted,
            "type": "text",
            "text": {
                "body": f"""Leave {action}

Student: {name}
Roll: {roll}
Dept: {dept}
Room: {room}

Reason: {reason}
Days: {days}
Start: {start}
End: {end}

- Hostel Management
"""
            }
        }

    # SEND REQUEST
    try:
        print("📱 Sending WhatsApp to:", formatted)
        print("📨 URL:", url)
        res = requests.post(url, headers=headers, json=data, timeout=10)
        print("✅ Status:", res.status_code)
        print("📨 Response:", res.text)
    except Exception as e:
        print("❌ WhatsApp Error:", e)

# =====================================================
# TEMP STORAGE
# =====================================================

leave_requests = []

@app.route("/webhook", methods=["POST"])
def whatsapp_webhook():
    msg = request.form.get("Body")
    sender = request.form.get("From")

    leave_requests.append({
        "message": msg,
        "sender": sender,
        "time": datetime.now().strftime("%Y-%m-%d %H:%M")
    })

    return "Received", 200

# =====================================================
# HOME
# =====================================================

@app.route("/", methods=["GET", "POST"])
def home():
    student_data = None

    if request.method == "POST":
        roll = request.form.get("roll")
        if roll:
            student_data = get_student_details(roll)

    return render_template(
        "warden.html",
        requests=leave_requests,
        student=student_data
    )

# =====================================================
# APPROVE / REJECT
# =====================================================

@app.route("/approve", methods=["POST"])
def approve():

    roll_number = request.form.get("roll").strip().upper()
    reason = request.form.get("reason")
    start = request.form.get("start")
    end = request.form.get("end")
    days = request.form.get("days")
    action = request.form.get("action")

    print("🔍 Action:", action)

    student = get_student_details(roll_number)

    if not student:
        return "Student not found"

    name = student["name"]
    department = student["department"]
    room = student["room"]
    student_phone = student["student_phone"]
    parent_phone = student["parent_phone"]

    # SEND WHATSAPP MESSAGE
    if action and action.lower() == "approved":
        print("🚀 Sending WhatsApp...")

        # If template approved, set use_template=True
        send_whatsapp_message(
            student_phone,
            action,
            name,
            roll_number,
            department,
            room,
            reason,
            days,
            start,
            end,
            use_template=False  # Change to True after template approval
        )

        # Optional: also send to parent
        if parent_phone:
            send_whatsapp_message(
                parent_phone,
                action,
                name,
                roll_number,
                department,
                room,
                reason,
                days,
                start,
                end,
                use_template=False  # Change to True after template approval
            )

    # SAVE TO GOOGLE SHEETS
    save_to_google_sheets([
        roll_number,
        name,
        department,
        room,
        reason,
        days,
        start,
        end,
        student_phone,
        parent_phone,
        action,
        datetime.now().strftime("%Y-%m-%d %H:%M")
    ])

    return redirect("/")

# =====================================================
# ADD STUDENT
# =====================================================

@app.route("/add-student", methods=["POST"])
def add_student():

    roll = request.form.get("roll").strip().upper()
    name = request.form.get("name")
    department = request.form.get("department")
    room = request.form.get("room")
    student_phone = request.form.get("student_phone")
    parent_phone = request.form.get("parent_phone")

    conn = get_db_connection()
    cur = conn.cursor()

    cur.execute("""
        INSERT INTO students VALUES (%s, %s, %s, %s, %s, %s)
        ON CONFLICT (roll_number) DO UPDATE
        SET name = EXCLUDED.name,
            department = EXCLUDED.department,
            room = EXCLUDED.room,
            student_phone = EXCLUDED.student_phone,
            parent_phone = EXCLUDED.parent_phone
    """, (roll, name, department, room, student_phone, parent_phone))

    conn.commit()
    cur.close()
    conn.close()

    return redirect("/")

# =====================================================
# RUN
# =====================================================

if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
