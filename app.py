import os
import requests
from flask import Flask, request, render_template, redirect, session
import psycopg2
from psycopg2.extras import DictCursor

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
# PHONE FORMAT (FIXED)
# =========================

def format_phone(phone):
    phone = str(phone).strip()

    # keep only digits
    phone = ''.join(filter(str.isdigit, phone))

    # remove leading 0
    if phone.startswith("0"):
        phone = phone[1:]

    # ensure India code
    if not phone.startswith("91"):
        phone = "91" + phone

    # validate length
    if len(phone) != 12:
        print("⚠️ Invalid phone number:", phone)

    return phone

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
# GET STUDENT
# =========================

def get_student(roll):
    roll = roll.strip().upper()

    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=DictCursor)

    cur.execute("SELECT * FROM students WHERE roll_number=%s", (roll,))
    data = cur.fetchone()

    cur.close()
    conn.close()
    return data

# =========================
# WHATSAPP SEND
# =========================

TOKEN = os.environ.get("WHATSAPP_TOKEN")
PHONE_ID = os.environ.get("PHONE_NUMBER_ID")

def send_whatsapp(phone, roll, name, department, room, reason, days, start, end):
    phone = format_phone(phone)

    print("📱 Sending WhatsApp to:", phone)

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
                        {"type": "text", "text": department},
                        {"type": "text", "text": room},
                        {"type": "text", "text": reason},
                        {"type": "text", "text": str(days)},
                        {"type": "text", "text": start},
                        {"type": "text", "text": end}
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

        print("📨 WhatsApp Response:", response)

        status = "failed"
        if "messages" in response:
            status = "sent"

    except Exception as e:
        print("❌ Error sending WhatsApp:", e)
        status = "error"

    # SAVE LOG
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
        if roll:
            student = get_student(roll)

    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=DictCursor)

    cur.execute("SELECT COUNT(*) FROM students")
    students_count = cur.fetchone()[0]

    cur.execute("SELECT COUNT(*) FROM leave_requests WHERE status='Approved'")
    approved = cur.fetchone()[0]

    cur.execute("SELECT COUNT(*) FROM leave_requests WHERE status='Rejected'")
    rejected = cur.fetchone()[0]

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
        approved_count=approved,
        rejected_count=rejected,
        messages_sent=messages,
        messages_list=messages_list
    )

# =========================
# APPROVE
# =========================

@app.route("/approve", methods=["POST"])
def approve():
    roll = request.form.get("roll").strip().upper()
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

    cur.execute("""
    INSERT INTO leave_requests 
    (roll_number, reason, start_date, end_date, days, status)
    VALUES (%s,%s,%s,%s,%s,%s)
    """, (roll, reason, start, end, days, action))

    conn.commit()
    cur.close()
    conn.close()

    if action == "Approved":
        send_whatsapp(
            student["student_phone"],
            roll,
            student["name"],
            student["department"],
            student["room"],
            reason,
            days,
            start,
            end
        )

        if student["parent_phone"]:
            send_whatsapp(
                student["parent_phone"],
                roll,
                student["name"],
                student["department"],
                student["room"],
                reason,
                days,
                start,
                end
            )

    return redirect("/")

# =========================
# ADD STUDENT (FIXED)
# =========================

@app.route("/add-student", methods=["POST"])
def add_student():
    roll = request.form.get("roll").strip().upper()

    student_phone = format_phone(request.form["student_phone"])
    parent_phone = format_phone(request.form["parent_phone"])

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
        roll,
        request.form["name"],
        request.form["department"],
        request.form["room"],
        student_phone,
        parent_phone
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
