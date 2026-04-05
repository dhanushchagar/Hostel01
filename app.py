import os
import json
import requests

from flask import Flask, request, render_template, redirect, session
import psycopg2
from psycopg2.extras import DictCursor

import gspread
from google.oauth2.service_account import Credentials

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "hostel-secret")

# =========================
# DATABASE
# =========================

def get_db_connection():
    return psycopg2.connect(os.environ.get("DATABASE_URL"), sslmode="require")


def init_db():
    conn = get_db_connection()
    cur = conn.cursor()

    cur.execute("""
    CREATE TABLE IF NOT EXISTS students (
        id SERIAL PRIMARY KEY,
        roll_number VARCHAR(20) UNIQUE,
        name TEXT,
        department TEXT,
        room TEXT,
        student_phone TEXT,
        parent_phone TEXT
    );
    """)

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


def get_student(roll):
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=DictCursor)

    cur.execute("SELECT * FROM students WHERE roll_number=%s", (roll,))
    student = cur.fetchone()

    cur.close()
    conn.close()
    return student


def format_phone(phone):
    phone = ''.join(filter(str.isdigit, str(phone)))
    if phone.startswith("0"):
        phone = phone[1:]
    if not phone.startswith("91"):
        phone = "91" + phone
    return phone
    
def safe(value):
    """
    Ensures the value is always a string and not None.
    If None, replaces with "-"
    """
    return str(value) if value else "-"
# =========================
# WHATSAPP
# =========================

TOKEN = os.environ.get("WHATSAPP_TOKEN")
PHONE_NUMBER_ID = os.environ.get("PHONE_NUMBER_ID")

def send_whatsapp(phone, action, name, roll, dept, room, reason, days, start, end, use_template=True):
    formatted = format_phone(phone)

    url = f"https://graph.facebook.com/v18.0/{PHONE_NUMBER_ID}/messages"

    headers = {
        "Authorization": f"Bearer {TOKEN}",
        "Content-Type": "application/json"
    }

    data = {
        "messaging_product": "whatsapp",
        "to": formatted,
        "type": "template",
        "template": {
            "name": "hostel_details",
            "language": {"code": "en"},
            "components": [
                {
                    "type": "body",
                    "parameters": [
                        {"type": "text", "text": safe(name)},
                        {"type": "text", "text": safe(roll)},
                        {"type": "text", "text": safe(dept)},
                        {"type": "text", "text": safe(room)},
                        {"type": "text", "text": safe(reason)},
                        {"type": "text", "text": safe(days)},
                        {"type": "text", "text": safe(start)},
                        {"type": "text", "text": safe(end)}
                    ]
                }
            ]
        }
    }

    try:
        print("📱 Sending WhatsApp to:", formatted)
        print("📦 Payload:", json.dumps(data, indent=2))

        res = requests.post(url, headers=headers, json=data, timeout=10)
        print("✅ Status:", res.status_code)
        print("📨 Response:", res.text)

        response_json = res.json()

        if "messages" in response_json:
            message_id = response_json["messages"][0]["id"]
            status = "sent"
            print("✅ Message accepted by WhatsApp")
        else:
            message_id = None
            status = "failed"
            print("❌ Message failed:", response_json)

    except Exception as e:
        print("❌ WhatsApp Error:", e)
        message_id = None
        status = "error"

    # Save log
    conn = get_db_connection()
    cur = conn.cursor()

    cur.execute("""
    INSERT INTO message_logs (roll_number, phone, message_id, status)
    VALUES (%s,%s,%s,%s)
    """, (roll, formatted, message_id, status))

    conn.commit()
    cur.close()
    conn.close()
# =========================
# APPROVE (SAVE TO SHEET)
# =========================
# =========================
# GOOGLE SHEETS
# =========================

def get_sheet():
    import os
    import json
    import gspread
    from google.oauth2.service_account import Credentials

    creds_dict = json.loads(os.environ.get("GOOGLE_CREDENTIALS"))

    scope = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive"
    ]

    creds = Credentials.from_service_account_info(creds_dict, scopes=scope)
    client = gspread.authorize(creds)

    sheet = client.open("Hostel Leave Records").sheet1
    return sheet
@app.route("/approve", methods=["POST"])
def approve():
    try:
        # Get form data
        roll = request.form.get("roll").strip().upper()
        action = request.form.get("action")
        reason = request.form.get("reason") or "-"
        days = request.form.get("days") or "-"
        start = request.form.get("start") or "-"
        end = request.form.get("end") or "-"

        # Fetch student from DB
        student = get_student(roll)
        if not student:
            return "Student not found"

        # -----------------------------
        # Save to Google Sheet
        # -----------------------------
        sheet = get_sheet()
        sheet.append_row([
            student["name"],
            roll,
            student["department"],
            student["room"],
            reason,
            days,
            start,
            end,
            action
        ])

        # -----------------------------
        # Send WhatsApp if approved
        # -----------------------------
        if action == "Approved":
            # Send to parent
            if student.get("parent_phone"):
                send_whatsapp(
                    student["parent_phone"],
                    action,
                    student["name"],
                    roll,
                    student["department"],
                    student["room"],
                    reason,
                    days,
                    start,
                    end
                )

            # Send to student
            if student.get("student_phone"):
                send_whatsapp(
                    student["student_phone"],
                    action,
                    student["name"],
                    roll,
                    student["department"],
                    student["room"],
                    reason,
                    days,
                    start,
                    end
                )

        return redirect("/")

    except Exception as e:
        print("❌ ERROR in /approve:", e)
        return "Error: " + str(e)

    
# =========================
# LOGIN
# =========================

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        if request.form["username"] == "vysya" and request.form["password"] == "7818":
            session["user"] = "admin"
            return redirect("/")
        return "Invalid Login"
    return render_template("login.html")

@app.route("/logout")
def logout():
    session.pop("user", None)
    return redirect("/login")

# =========================
# HOME
# =========================

@app.route("/", methods=["GET", "POST"])
def home():
    if "user" not in session:
        return redirect("/login")

    student = None
    roll = None

    if request.method == "POST":
        roll = request.form.get("roll").strip().upper()
        if roll:
            student = get_student(roll)

    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=DictCursor)

    cur.execute("SELECT COUNT(*) FROM students")
    students_count = cur.fetchone()[0]

    cur.execute("SELECT COUNT(*) FROM message_logs WHERE status='sent'")
    messages = cur.fetchone()[0]

    cur.execute("""
        SELECT roll_number, phone, status, created_at
        FROM message_logs
        ORDER BY created_at DESC
        LIMIT 10
    """)
    messages_list = cur.fetchall()

    cur.close()
    conn.close()

    return render_template(
        "warden.html",
        student=student,
        students_count=students_count,
        approved_count=0,
        rejected_count=0,
        messages_sent=messages,
        messages_list=messages_list
    )

# =========================
# ADD STUDENT
# =========================

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
    VALUES (%s,%s,%s,%s,%s,%s)
    ON CONFLICT (roll_number)
    DO UPDATE SET
        name=EXCLUDED.name,
        department=EXCLUDED.department,
        room=EXCLUDED.room,
        student_phone=EXCLUDED.student_phone,
        parent_phone=EXCLUDED.parent_phone
    """, (roll, name, department, room, student_phone, parent_phone))

    conn.commit()
    cur.close()
    conn.close()

    return redirect("/")

# =========================
# DELETE STUDENT
# =========================

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

# =========================
# RUN
# =========================
@app.route("/webhook", methods=["GET", "POST"])
def webhook():
    if request.method == "GET":
        verify_token = "myverify123"

        token = request.args.get("hub.verify_token")
        challenge = request.args.get("hub.challenge")

        if token == verify_token:
            return challenge
        return "Error", 403

    elif request.method == "POST":
        data = request.get_json()
        print("Webhook Data:", data)
        return "OK", 200

if __name__ == "__main__":
    app.run(debug=True)
