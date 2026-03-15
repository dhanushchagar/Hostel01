import os
import json
import requests
from datetime import datetime

from flask import Flask, request, render_template, redirect
import psycopg2
from psycopg2.extras import DictCursor
import gspread
from google.oauth2.service_account import Credentials

app = Flask(__name__)

# =====================================================
# DATABASE CONNECTION (PostgreSQL)
# =====================================================

def get_db_connection():
    return psycopg2.connect(os.environ.get("DATABASE_URL"))

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

    cur.execute("""
        SELECT roll_number, name, department, room,
               student_phone, parent_phone
        FROM students
        WHERE roll_number = %s
    """, (roll_number,))

    result = cur.fetchone()

    cur.close()
    conn.close()

    return result


# =====================================================
# GOOGLE SHEETS
# =====================================================

def save_to_google_sheets(data):

    creds_json = json.loads(os.environ.get("GOOGLE_CREDENTIALS"))

    credentials = Credentials.from_service_account_info(
        creds_json,
        scopes=[
            "https://www.googleapis.com/auth/spreadsheets",
            "https://www.googleapis.com/auth/drive"
        ],
    )

    gc = gspread.authorize(credentials)
    sheet = gc.open("Hostel Leave Records").sheet1
    sheet.append_row(data)


# =====================================================
# META WHATSAPP CLOUD API
# =====================================================

WHATSAPP_TOKEN = os.environ.get("WHATSAPP_TOKEN")
PHONE_NUMBER_ID = os.environ.get("PHONE_NUMBER_ID")


def send_whatsapp_message(phone, message):

    url = f"https://graph.facebook.com/v18.0/{PHONE_NUMBER_ID}/messages"

    headers = {
        "Authorization": f"Bearer {WHATSAPP_TOKEN}",
        "Content-Type": "application/json"
    }

    data = {
        "messaging_product": "whatsapp",
        "to": f"91{phone}",
        "type": "text",
        "text": {
            "body": message
        }
    }

    requests.post(url, headers=headers, json=data)


# =====================================================
# TEMP STORAGE (Webhook)
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
# WARDEN PANEL
# =====================================================

@app.route("/", methods=["GET", "POST"])
def home():

    student_data = None

    if request.method == "POST":
        roll = request.form.get("roll")

        if roll:
            student_data = get_student_details(roll)

    return render_template("warden.html",
                           requests=leave_requests,
                           student=student_data)


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
    principal = request.form.get("principal")
    action = request.form.get("action")

    student = get_student_details(roll_number)

    if not student:
        return "Student not found"

    name = student["name"]
    department = student["department"]
    room = student["room"]
    student_phone = student["student_phone"]
    parent_phone = student["parent_phone"]

    message_body = f"""
LEAVE {action.upper()}

Student: {name}
Roll No: {roll_number}
Department & Year: {department}
Room: {room}

Reason: {reason}
Days: {days}
Start: {start}
End: {end}

By Warden
"""

    # Send WhatsApp only if Approved
    if action == "Approved":
        for number in [parent_phone, principal, student_phone]:
            if number:
                send_whatsapp_message(number, message_body)

    # Save to Google Sheets
    save_to_google_sheets([
        roll_number,
        name,
        department,
        room,
        reason,
        days,
        start,
        end,
        parent_phone,
        student_phone,
        action,
        datetime.now().strftime("%Y-%m-%d %H:%M")
    ])

    return redirect("/")


# =====================================================
# ADD / UPDATE STUDENT
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
        INSERT INTO students (roll_number, name, department, room, student_phone, parent_phone)
        VALUES (%s, %s, %s, %s, %s, %s)
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
    app.run()
