from flask import Flask, render_template, request, jsonify, session
from flask_cors import CORS
import uuid
import random
import pymysql

app = Flask(__name__)
app.secret_key = "hogwarts_secret_key_123"
CORS(app)

# ================= DATABASE CONNECTION =================
DB_CONFIG = {
    "host": "localhost",
    "user": "root",
    "password": "1035@Vish",
    "database": "hogwarts_bank",
    "cursorclass": pymysql.cursors.DictCursor,
    "autocommit": False,
}

conn = None
cursor = None

# ================= FIX: RECONNECT IF LOST =================
def get_cursor():
    global conn, cursor
    if conn is None or not getattr(conn, "open", False):
        conn = pymysql.connect(**DB_CONFIG)
        cursor = conn.cursor()
        return cursor

    try:
        conn.ping(reconnect=True)
    except pymysql.MySQLError:
        conn = pymysql.connect(**DB_CONFIG)
        cursor = conn.cursor()

    return cursor

# ================= ACCOUNT NUMBER =================
def generate_account_number():
    cur = get_cursor()
    while True:
        acc = str(random.randint(100000000000, 999999999999))
        cur.execute("SELECT id FROM users WHERE account_number=%s", (acc,))
        if not cur.fetchone():
            return acc

# ================= HOME =================
@app.route("/")
def home():
    return render_template("index2.html")

# ================= CREATE ACCOUNT =================
@app.route("/create_account", methods=["POST"])
def create_account():
    try:
        cur = get_cursor()
        data = request.json

        name = data["name"]
        email = data["email"]
        password = data["password"]
        phone = data["phone"]

        cur.execute("SELECT id FROM users WHERE email=%s", (email,))
        if cur.fetchone():
            return jsonify({"success": False, "error": "Email already exists ❌"})

        account_number = generate_account_number()

        cur.execute("""
            INSERT INTO users (name, email, password, phone, balance, account_number)
            VALUES (%s,%s,%s,%s,%s,%s)
        """, (name, email, password, phone, 50000, account_number))

        conn.commit()

        return jsonify({
            "success": True,
            "account_number": account_number
        })

    except Exception as e:
        conn.rollback()
        return jsonify({"error": str(e)})

# ================= LOGIN =================
@app.route("/login", methods=["POST"])
def login():
    try:
        cur = get_cursor()
        data = request.json

        cur.execute("""
            SELECT * FROM users 
            WHERE email=%s AND password=%s AND phone=%s
        """, (data["email"], data["password"], data["phone"]))

        user = cur.fetchone()

        if user:
            session["user_email"] = user["email"]
            return jsonify({"success": True})
        else:
            return jsonify({"success": False})

    except Exception as e:
        return jsonify({"error": str(e)})

# ================= USER DETAILS =================
@app.route("/user_details")
def user_details():
    cur = get_cursor()

    if "user_email" not in session:
        return jsonify({"error": "Login first"}), 401

    email = session["user_email"]

    cur.execute("SELECT * FROM users WHERE email=%s", (email,))
    user = cur.fetchone()

    cur.execute("SELECT COALESCE(SUM(loan_amount),0) AS total FROM loans WHERE email=%s", (email,))
    loan = cur.fetchone()

    user["loan"] = loan["total"]

    return jsonify(user)

# ================= TRANSFER =================
@app.route("/transfer", methods=["POST"])
def transfer():
    try:
        cur = get_cursor()

        if "user_email" not in session:
            return jsonify({"error": "Login first"}), 401

        data = request.json
        amount = int(data["amount"])
        receiver_account = data["receiver_account"]
        sender_email = session["user_email"]

        # sender
        cur.execute("SELECT * FROM users WHERE email=%s", (sender_email,))
        sender = cur.fetchone()

        if sender["balance"] < amount:
            return jsonify({"error": "Insufficient balance"})

        # receiver
        cur.execute("SELECT * FROM users WHERE account_number=%s", (receiver_account,))
        receiver = cur.fetchone()

        if not receiver:
            return jsonify({"error": "Receiver not found"})

        # update
        cur.execute("UPDATE users SET balance=balance-%s WHERE email=%s", (amount, sender_email))
        cur.execute("UPDATE users SET balance=balance+%s WHERE account_number=%s", (amount, receiver_account))

        txn_id = "TXN" + str(random.randint(100000000,999999999))

        cur.execute("""
            INSERT INTO transactions (transaction_id,email,type,amount,receiver_account)
            VALUES (%s,%s,%s,%s,%s)
        """,(txn_id, sender_email, "Sent", amount, receiver_account))

        conn.commit()

        return jsonify({"success": True})

    except Exception as e:
        conn.rollback()
        return jsonify({"error": str(e)})

# ================= FEEDBACK =================
@app.route("/submit_feedback", methods=["POST"])
def submit_feedback():
    try:
        cur = get_cursor()
        data = request.json

        rating = int(data.get("rating", 0))
        message = data.get("message", "").strip()
        email = session.get("user_email")

        if rating < 1 or rating > 5 or len(message) < 5:
            return jsonify({"error": "Please provide a rating and a feedback message."}), 400

        cur.execute("""
            CREATE TABLE IF NOT EXISTS feedback (
                id INT AUTO_INCREMENT PRIMARY KEY,
                email VARCHAR(255),
                rating TINYINT NOT NULL,
                message TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
        """)

        cur.execute("""
            INSERT INTO feedback (email, rating, message)
            VALUES (%s, %s, %s)
        """, (email, rating, message))

        conn.commit()
        return jsonify({"success": True})

    except Exception as e:
        conn.rollback()
        return jsonify({"error": str(e)}), 500

@app.route("/feedback_stats")
def feedback_stats():
    try:
        cur = get_cursor()

        cur.execute("""
            CREATE TABLE IF NOT EXISTS feedback (
                id INT AUTO_INCREMENT PRIMARY KEY,
                email VARCHAR(255),
                rating TINYINT NOT NULL,
                message TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
        """)

        cur.execute("SELECT COALESCE(AVG(rating), 0) AS avg_rating, COUNT(*) AS total_feedback FROM feedback")
        stats = cur.fetchone()

        return jsonify({
            "avg_rating": float(stats["avg_rating"]),
            "total_feedback": int(stats["total_feedback"]),
        })

    except Exception as e:
        return jsonify({"avg_rating": 0, "total_feedback": 0, "error": str(e)}), 500

# ================= APPLY LOAN =================
@app.route("/apply_loan", methods=["POST"])
def apply_loan():
    try:
        if "user_email" not in session:
            return jsonify({"error": "Login first"}), 401

        cur = get_cursor()
        data = request.json
        amount = int(data.get("amount", 0))
        email = session["user_email"]

        if amount < 5000 or amount > 50000:
            return jsonify({"error": "Loan amount must be between ₹5,000 and ₹50,000"}), 400

        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS loans (
                id INT AUTO_INCREMENT PRIMARY KEY,
                email VARCHAR(255),
                loan_amount INT NOT NULL,
                status VARCHAR(50) DEFAULT 'Approved',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
            """
        )

        cur.execute("UPDATE users SET balance = balance + %s WHERE email = %s", (amount, email))
        cur.execute("INSERT INTO loans (email, loan_amount) VALUES (%s, %s)", (email, amount))

        txn_id = "TXN" + str(random.randint(100000000, 999999999))
        cur.execute("INSERT INTO transactions (transaction_id, email, type, amount) VALUES (%s, %s, %s, %s)", (txn_id, email, "Loan", amount))

        conn.commit()

        return jsonify({"success": True, "message": f"Loan of ₹{amount} approved and credited to your account."})

    except Exception as e:
        conn.rollback()
        return jsonify({"error": str(e)}), 500

# ================= RUN =================
if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5001, debug=True)