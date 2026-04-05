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

# =========================
# GOOGLE SHEETS
# =========================

def get_sheet():
    creds_dict = json.loads(os.environ.get("GOOGLE_CREDENTIALS"))

    scope = ["https://www.googleapis.com/auth/spreadsheets"]
    creds = Credentials.from_service_account_info(creds_dict, scopes=scope)

    client = gspread.authorize(creds)

    sheet = client.open("Hostel Leave Records").sheet1
    return sheet

# =========================
# HELPER
# =========================

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

# =========================
# WHATSAPP
# =========================

TOKEN = os.environ.get("WHATSAPP_TOKEN")
PHONE_ID = os.environ.get("PHONE_NUMBER_ID")

def send_whatsapp(phone, roll, name):
    phone = format_phone(phone)

    url = f"https://graph.facebook.com/v18.0/{PHONE_ID}/messages"

    payload = {
        "messaging_product": "whatsapp",
        "to": phone,
        "type": "text",
        "text": {"body": f"Leave Approved for {name} ({roll})"}
    }

    headers = {
        "Authorization": f"Bearer {TOKEN}",
        "Content-Type": "application/json"
    }

    try:
        res = requests.post(url, headers=headers, json=payload)
        response = res.json()

        if "messages" in response:
            message_id = response["messages"][0]["id"]
            status = "sent"
        else:
            message_id = None
            status = "failed"

    except Exception:
        message_id = None
        status = "error"

    # Save log
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
# APPROVE (SAVE TO SHEET)
# =========================

@app.route("/approve", methods=["POST"])
def approve():
    try:
        roll = request.form.get("roll").strip().upper()
        action = request.form.get("action")
        reason = request.form.get("reason")
        days = request.form.get("days")
        start = request.form.get("start")
        end = request.form.get("end")

        student = get_student(roll)
        if not student:
            return "Student not found"

        # ✅ SAVE TO GOOGLE SHEET
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

        # ✅ WhatsApp only if approved
        if action == "Approved" and student.get("parent_phone"):
            send_whatsapp(student["parent_phone"], roll, student["name"])

        return redirect("/")

    except Exception as e:
        print("❌ ERROR:", e)
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

if __name__ == "__main__":
    app.run(debug=True)
