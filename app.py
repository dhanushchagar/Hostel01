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
app.secret_key = "hostel-secret"

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
        roll_number VARCHAR(20) PRIMARY KEY,
        name VARCHAR(100),
        department VARCHAR(50),
        room VARCHAR(20),
        student_phone VARCHAR(15),
        parent_phone VARCHAR(15)
    );
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS leave_requests (
        id SERIAL PRIMARY KEY,
        roll_number VARCHAR(20),
        reason TEXT,
        start_date DATE,
        end_date DATE,
        days INT,
        status VARCHAR(20),
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS message_logs (
        id SERIAL PRIMARY KEY,
        roll_number VARCHAR(20),
        phone VARCHAR(15),
        status VARCHAR(20),
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    """)

    conn.commit()
    cur.close()
    conn.close()

init_db()

# =========================
# LOGIN
# =========================

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        if request.form["username"] == "admin" and request.form["password"] == "1234":
            session["user"] = "admin"
            return redirect("/")
        else:
            return "Invalid login"
    return render_template("login.html")

@app.route("/logout")
def logout():
    session.pop("user", None)
    return redirect("/login")

# =========================
# STUDENT FETCH
# =========================

def get_student(roll):
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=DictCursor)

    cur.execute("SELECT * FROM students WHERE roll_number=%s", (roll,))
    data = cur.fetchone()

    cur.close()
    conn.close()
    return data

# =========================
# WHATSAPP
# =========================

TOKEN = os.environ.get("WHATSAPP_TOKEN")
PHONE_ID = os.environ.get("PHONE_NUMBER_ID")

def send_whatsapp(phone, roll):
    url = f"https://graph.facebook.com/v18.0/{PHONE_ID}/messages"

    payload = {
        "messaging_product": "whatsapp",
        "to": phone,
        "type": "text",
        "text": {"body": "Leave Approved"}
    }

    headers = {
        "Authorization": f"Bearer {TOKEN}",
        "Content-Type": "application/json"
    }

    res = requests.post(url, headers=headers, json=payload)
    data = res.json()

    status = "failed"
    if "messages" in data:
        status = "sent"

    # SAVE STATUS
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO message_logs (roll_number, phone, status) VALUES (%s,%s,%s)",
        (roll, phone, status)
    )
    conn.commit()
    cur.close()
    conn.close()

# =========================
# DASHBOARD
# =========================

@app.route("/", methods=["GET", "POST"])
def home():
    if "user" not in session:
        return redirect("/login")

    student = None

    if request.method == "POST":
        roll = request.form.get("roll")
        student = get_student(roll)

    conn = get_db_connection()
    cur = conn.cursor()

    cur.execute("SELECT COUNT(*) FROM students")
    students_count = cur.fetchone()[0]

    cur.execute("SELECT COUNT(*) FROM leave_requests WHERE status='Approved'")
    approved = cur.fetchone()[0]

    cur.execute("SELECT COUNT(*) FROM leave_requests WHERE status='Rejected'")
    rejected = cur.fetchone()[0]

    cur.execute("SELECT COUNT(*) FROM message_logs WHERE status='sent'")
    messages = cur.fetchone()[0]

    cur.close()
    conn.close()

    return render_template(
        "warden.html",
        student=student,
        students_count=students_count,
        approved_count=approved,
        rejected_count=rejected,
        messages_sent=messages
    )

# =========================
# APPROVE
# =========================

@app.route("/approve", methods=["POST"])
def approve():
    roll = request.form.get("roll")
    reason = request.form.get("reason")
    start = request.form.get("start")
    end = request.form.get("end")
    days = request.form.get("days")
    action = request.form.get("action")

    student = get_student(roll)

    if not student:
        return "Student not found"

    conn = get_db_connection()
    cur = conn.cursor()

    # SAVE LEAVE
    cur.execute("""
    INSERT INTO leave_requests 
    (roll_number, reason, start_date, end_date, days, status)
    VALUES (%s,%s,%s,%s,%s,%s)
    """, (roll, reason, start, end, days, action))

    conn.commit()
    cur.close()
    conn.close()

    # SEND MESSAGE
    if action == "Approved":
        send_whatsapp(student["student_phone"], roll)
        if student["parent_phone"]:
            send_whatsapp(student["parent_phone"], roll)

    return redirect("/")

# =========================
# ADD STUDENT
# =========================

@app.route("/add-student", methods=["POST"])
def add_student():
    conn = get_db_connection()
    cur = conn.cursor()

    cur.execute("""
    INSERT INTO students VALUES (%s,%s,%s,%s,%s,%s)
    ON CONFLICT (roll_number) DO UPDATE
    SET name=EXCLUDED.name,
        department=EXCLUDED.department,
        room=EXCLUDED.room,
        student_phone=EXCLUDED.student_phone,
        parent_phone=EXCLUDED.parent_phone
    """, (
        request.form["roll"],
        request.form["name"],
        request.form["department"],
        request.form["room"],
        request.form["student_phone"],
        request.form["parent_phone"]
    ))

    conn.commit()
    cur.close()
    conn.close()

    return redirect("/")

# =========================
# RUN
# =========================

if __name__ == "__main__":
    app.run(debug=True)
