import os
import re
import csv
import io
import sqlite3
from datetime import datetime
from functools import wraps

from flask import Flask, render_template, request, redirect, url_for, flash, session, Response
from werkzeug.security import generate_password_hash, check_password_hash

try:
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.metrics.pairwise import cosine_similarity
    SKLEARN_AVAILABLE = True
except ImportError:
    SKLEARN_AVAILABLE = False

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "change-this-secret-key-before-deployment")
DB_PATH = os.environ.get("ISSUETWIN_DB", os.path.join(os.path.dirname(__file__), "issuetwin.db"))
MATCH_THRESHOLD = float(os.environ.get("MATCH_THRESHOLD", "0.34"))

SECTORS = {
    "College / University": [
        "Water", "Electricity", "Wi-Fi / Network", "Classroom", "Laboratory",
        "Library", "Hostel", "Transport", "Cleanliness", "Safety", "Other"
    ],
    "Apartment / Housing Society": [
        "Water", "Electricity", "Lift / Elevator", "Security", "Parking",
        "Waste", "Plumbing", "Common Area", "Noise", "Other"
    ],
    "Municipality / Public Services": [
        "Road", "Streetlight", "Drainage", "Water Supply", "Garbage",
        "Public Safety", "Footpath", "Public Facility", "Other"
    ],
    "Organization / Office": [
        "IT Support", "Network", "Electrical", "Plumbing", "Workplace",
        "Security", "HR / Administration", "Equipment", "Other"
    ],
    "Large Company": [
        "IT Support", "Network", "Electrical", "Facilities", "Security",
        "Equipment", "Logistics", "Workplace", "Other"
    ],
    "Factory / Industrial Site": [
        "Machine / Equipment", "Electrical", "Safety", "Fire / Emergency",
        "Quality", "Production", "Material Handling", "Utilities",
        "Maintenance", "Other"
    ],
}
SEVERITIES = ["Low", "Medium", "High", "Critical"]
STATUSES = ["Open", "Acknowledged", "In Progress", "On Hold", "Resolved", "Closed"]
PRIORITY_ORDER = {"Critical": 4, "High": 3, "Medium": 2, "Low": 1}

# Small domain vocabulary helps a lightweight text model recognize common variations.
SYNONYMS = {
    "wifi": "wireless network internet",
    "wi fi": "wireless network internet",
    "internet": "wireless network internet",
    "network": "wireless network internet",
    "water cooler": "drinking water dispenser",
    "dispenser": "drinking water dispenser",
    "water supply": "water supply",
    "lift": "elevator",
    "elevator": "elevator",
    "garbage": "waste trash rubbish",
    "trash": "waste trash rubbish",
    "street light": "streetlight",
    "street lamp": "streetlight",
    "machine": "equipment machinery",
    "machinery": "equipment machinery",
    "projector": "classroom audiovisual equipment",
    "ac": "air conditioner cooling",
    "air conditioning": "air conditioner cooling",
}

def now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn

def init_db():
    conn = db()
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS organizations (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        sector TEXT NOT NULL,
        created_at TEXT NOT NULL,
        UNIQUE(name, sector)
    );
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        organization_id INTEGER NOT NULL,
        name TEXT NOT NULL,
        email TEXT NOT NULL UNIQUE,
        password_hash TEXT NOT NULL,
        role TEXT NOT NULL DEFAULT 'reporter',
        created_at TEXT NOT NULL,
        FOREIGN KEY(organization_id) REFERENCES organizations(id)
    );
    CREATE TABLE IF NOT EXISTS issues (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        organization_id INTEGER NOT NULL,
        title TEXT NOT NULL,
        description TEXT NOT NULL,
        category TEXT NOT NULL,
        site TEXT NOT NULL,
        location TEXT NOT NULL,
        severity TEXT NOT NULL,
        priority TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'Open',
        assigned_department TEXT DEFAULT '',
        recurring INTEGER NOT NULL DEFAULT 0,
        previous_issue_id INTEGER,
        created_by INTEGER NOT NULL,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        FOREIGN KEY(organization_id) REFERENCES organizations(id),
        FOREIGN KEY(created_by) REFERENCES users(id),
        FOREIGN KEY(previous_issue_id) REFERENCES issues(id)
    );
    CREATE TABLE IF NOT EXISTS complaints (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        issue_id INTEGER NOT NULL,
        user_id INTEGER NOT NULL,
        title TEXT NOT NULL,
        description TEXT NOT NULL,
        category TEXT NOT NULL,
        site TEXT NOT NULL,
        location TEXT NOT NULL,
        severity TEXT NOT NULL,
        similarity_score REAL DEFAULT 0,
        created_at TEXT NOT NULL,
        FOREIGN KEY(issue_id) REFERENCES issues(id),
        FOREIGN KEY(user_id) REFERENCES users(id)
    );
    CREATE TABLE IF NOT EXISTS confirmations (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        issue_id INTEGER NOT NULL,
        user_id INTEGER NOT NULL,
        created_at TEXT NOT NULL,
        UNIQUE(issue_id, user_id),
        FOREIGN KEY(issue_id) REFERENCES issues(id),
        FOREIGN KEY(user_id) REFERENCES users(id)
    );
    CREATE TABLE IF NOT EXISTS updates_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        issue_id INTEGER NOT NULL,
        user_id INTEGER NOT NULL,
        old_status TEXT,
        new_status TEXT NOT NULL,
        remarks TEXT DEFAULT '',
        created_at TEXT NOT NULL,
        FOREIGN KEY(issue_id) REFERENCES issues(id),
        FOREIGN KEY(user_id) REFERENCES users(id)
    );
    """)
    # A ready-to-use demo workspace. Change the password after first login.
    org = conn.execute(
        "SELECT id FROM organizations WHERE name=? AND sector=?",
        ("IssueTwin Demo Campus", "College / University")
    ).fetchone()
    if not org:
        cur = conn.execute(
            "INSERT INTO organizations(name,sector,created_at) VALUES(?,?,?)",
            ("IssueTwin Demo Campus", "College / University", now())
        )
        org_id = cur.lastrowid
    else:
        org_id = org["id"]
    admin = conn.execute("SELECT id FROM users WHERE email=?", ("admin@issuetwin.demo",)).fetchone()
    if not admin:
        conn.execute(
            """INSERT INTO users(organization_id,name,email,password_hash,role,created_at)
               VALUES(?,?,?,?,?,?)""",
            (org_id, "Demo Administrator", "admin@issuetwin.demo",
             generate_password_hash("Admin123!"), "admin", now())
        )
    conn.commit()
    conn.close()

def login_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not session.get("user_id"):
            return redirect(url_for("login"))
        return fn(*args, **kwargs)
    return wrapper

def admin_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not session.get("user_id"):
            return redirect(url_for("login"))
        if session.get("role") != "admin":
            flash("Administrator access is required for that action.", "warning")
            return redirect(url_for("dashboard"))
        return fn(*args, **kwargs)
    return wrapper

def current_user():
    if not session.get("user_id"):
        return None
    conn = db()
    user = conn.execute("""
        SELECT users.*, organizations.name AS organization_name,
               organizations.sector AS sector
        FROM users JOIN organizations ON organizations.id=users.organization_id
        WHERE users.id=?
    """, (session["user_id"],)).fetchone()
    conn.close()
    return user

@app.context_processor
def inject_globals():
    return {"current_user": current_user(), "sectors": SECTORS, "statuses": STATUSES}

def normalize_text(text):
    text = (text or "").lower()
    text = re.sub(r"[^a-z0-9\s]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    for phrase, replacement in SYNONYMS.items():
        text = re.sub(r"\b" + re.escape(phrase) + r"\b", replacement, text)
    return text

def similarity_score(text_a, text_b):
    a, b = normalize_text(text_a), normalize_text(text_b)
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    if SKLEARN_AVAILABLE:
        try:
            vectorizer = TfidfVectorizer(ngram_range=(1, 2), analyzer="word", sublinear_tf=True)
            matrix = vectorizer.fit_transform([a, b])
            return float(cosine_similarity(matrix[0:1], matrix[1:2])[0][0])
        except ValueError:
            return 0.0
    # Fallback: word overlap score. Install requirements for better matching.
    wa, wb = set(a.split()), set(b.split())
    return len(wa & wb) / max(1, len(wa | wb))

def issue_text(row):
    return " ".join([row["title"] or "", row["description"] or "", row["category"] or ""])

def calculate_priority(severity, report_count, description):
    text = (description or "").lower()
    if severity == "Critical" or any(w in text for w in ["fire", "smoke", "electric shock", "gas leak", "injury", "accident", "exposed wire"]):
        return "Critical"
    if severity == "High" or report_count >= 10:
        return "High"
    if severity == "Medium" or report_count >= 4:
        return "Medium"
    return "Low"

def find_matching_issue(conn, org_id, title, description, category, site, location):
    rows = conn.execute("""
        SELECT * FROM issues
        WHERE organization_id=? AND status NOT IN ('Resolved','Closed')
        ORDER BY updated_at DESC
    """, (org_id,)).fetchall()
    incoming = f"{title} {description} {category}"
    best, best_score = None, 0.0
    for row in rows:
        # Keep matching within the same site/building to reduce false merges.
        if (row["site"] or "").strip().lower() != (site or "").strip().lower():
            continue
        # Exact category is preferred; high text similarity can still find a related category.
        score = similarity_score(incoming, issue_text(row))
        category_match = row["category"].lower() == category.lower()
        if category_match and score >= MATCH_THRESHOLD and score > best_score:
            best, best_score = row, score
        elif score >= 0.62 and score > best_score:
            best, best_score = row, score
    return best, best_score

def find_resolved_predecessor(conn, org_id, title, description, category, site):
    rows = conn.execute("""
        SELECT * FROM issues
        WHERE organization_id=? AND status IN ('Resolved','Closed')
        ORDER BY updated_at DESC LIMIT 100
    """, (org_id,)).fetchall()
    incoming = f"{title} {description} {category}"
    best, best_score = None, 0.0
    for row in rows:
        if row["site"].strip().lower() != site.strip().lower():
            continue
        score = similarity_score(incoming, issue_text(row))
        if (row["category"].lower() == category.lower() and score >= MATCH_THRESHOLD) or score >= 0.62:
            if score > best_score:
                best, best_score = row, score
    return best, best_score

def issue_report_count(conn, issue_id):
    row = conn.execute("SELECT COUNT(*) AS n FROM complaints WHERE issue_id=?", (issue_id,)).fetchone()
    return row["n"]

@app.route("/")
def index():
    if session.get("user_id"):
        return redirect(url_for("dashboard"))
    return render_template("index.html")

@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        org_name = request.form.get("organization", "").strip()
        sector = request.form.get("sector", "")
        if not all([name, email, password, org_name, sector]):
            flash("Please complete all fields.", "danger")
            return redirect(url_for("register"))
        if len(password) < 8:
            flash("Password must contain at least 8 characters.", "danger")
            return redirect(url_for("register"))
        if sector not in SECTORS:
            flash("Choose a valid sector.", "danger")
            return redirect(url_for("register"))
        conn = db()
        try:
            org = conn.execute(
                "SELECT id FROM organizations WHERE name=? AND sector=?",
                (org_name, sector)
            ).fetchone()
            if org:
                org_id = org["id"]
                new_role = "reporter"
            else:
                cur = conn.execute(
                    "INSERT INTO organizations(name,sector,created_at) VALUES(?,?,?)",
                    (org_name, sector, now())
                )
                org_id = cur.lastrowid
                # The person who creates a new workspace becomes its first administrator.
                new_role = "admin"
            conn.execute(
                """INSERT INTO users(organization_id,name,email,password_hash,role,created_at)
                   VALUES(?,?,?,?,?,?)""",
                (org_id, name, email, generate_password_hash(password), new_role, now())
            )
            conn.commit()
            flash("Account created. Ask your organization administrator to grant admin access if needed.", "success")
            return redirect(url_for("login"))
        except sqlite3.IntegrityError:
            flash("That email is already registered. Please log in.", "warning")
            return redirect(url_for("login"))
        finally:
            conn.close()
    return render_template("register.html")

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        conn = db()
        user = conn.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
        conn.close()
        if user and check_password_hash(user["password_hash"], password):
            session.clear()
            session["user_id"] = user["id"]
            session["role"] = user["role"]
            session["organization_id"] = user["organization_id"]
            session["name"] = user["name"]
            return redirect(url_for("dashboard"))
        flash("Invalid email or password.", "danger")
    return render_template("login.html")

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("index"))

@app.route("/dashboard")
@login_required
def dashboard():
    conn = db()
    org_id = session["organization_id"]
    issues = conn.execute("""
        SELECT i.*,
        (SELECT COUNT(*) FROM complaints c WHERE c.issue_id=i.id) AS report_count,
        (SELECT COUNT(*) FROM confirmations cf WHERE cf.issue_id=i.id) AS confirmations
        FROM issues i WHERE i.organization_id=?
        ORDER BY CASE i.priority WHEN 'Critical' THEN 4 WHEN 'High' THEN 3 WHEN 'Medium' THEN 2 ELSE 1 END DESC,
                 i.updated_at DESC
    """, (org_id,)).fetchall()
    stats = {
        "total": len(issues),
        "open": sum(1 for x in issues if x["status"] not in ("Resolved", "Closed")),
        "resolved": sum(1 for x in issues if x["status"] in ("Resolved", "Closed")),
        "critical": sum(1 for x in issues if x["priority"] == "Critical"),
        "reports": conn.execute("""
            SELECT COUNT(*) AS n FROM complaints c JOIN issues i ON i.id=c.issue_id
            WHERE i.organization_id=?
        """, (org_id,)).fetchone()["n"]
    }
    conn.close()
    return render_template("dashboard.html", issues=issues, stats=stats)

@app.route("/complaints/new", methods=["GET", "POST"])
@login_required
def new_complaint():
    user = current_user()
    categories = SECTORS.get(user["sector"], ["Other"])
    if request.method == "POST":
        title = request.form.get("title", "").strip()
        description = request.form.get("description", "").strip()
        category = request.form.get("category", "Other")
        site = request.form.get("site", "").strip()
        location = request.form.get("location", "").strip()
        severity = request.form.get("severity", "Medium")
        department = request.form.get("department", "").strip()
        if not all([title, description, site, location]):
            flash("Please complete title, description, site and location.", "danger")
            return redirect(url_for("new_complaint"))
        if category not in categories or severity not in SEVERITIES:
            flash("Choose a valid category and severity.", "danger")
            return redirect(url_for("new_complaint"))
        conn = db()
        org_id = session["organization_id"]
        match, score = find_matching_issue(conn, org_id, title, description, category, site, location)
        if match:
            issue_id = match["id"]
            conn.execute("""
                INSERT INTO complaints(issue_id,user_id,title,description,category,site,location,severity,similarity_score,created_at)
                VALUES(?,?,?,?,?,?,?,?,?,?)
            """, (issue_id, session["user_id"], title, description, category, site, location, severity, score, now()))
            count = issue_report_count(conn, issue_id)
            new_priority = calculate_priority(
                max([severity, match["severity"]], key=lambda s: PRIORITY_ORDER.get(s, 1)),
                count, description
            )
            conn.execute("UPDATE issues SET priority=?, updated_at=? WHERE id=?", (new_priority, now(), issue_id))
            conn.commit()
            conn.close()
            flash(f"Your report was linked to existing ISSUE-{issue_id:04d} (similarity {score:.0%}).", "success")
            return redirect(url_for("issue_detail", issue_id=issue_id))
        predecessor, old_score = find_resolved_predecessor(conn, org_id, title, description, category, site)
        recurring = 1 if predecessor else 0
        previous_id = predecessor["id"] if predecessor else None
        priority = calculate_priority(severity, 1, description)
        cur = conn.execute("""
            INSERT INTO issues(organization_id,title,description,category,site,location,severity,priority,status,
                               assigned_department,recurring,previous_issue_id,created_by,created_at,updated_at)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (org_id, title, description, category, site, location, severity, priority, "Open",
              department, recurring, previous_id, session["user_id"], now(), now()))
        issue_id = cur.lastrowid
        conn.execute("""
            INSERT INTO complaints(issue_id,user_id,title,description,category,site,location,severity,similarity_score,created_at)
            VALUES(?,?,?,?,?,?,?,?,?,?)
        """, (issue_id, session["user_id"], title, description, category, site, location, severity, 0.0, now()))
        conn.commit()
        conn.close()
        if recurring:
            flash(f"New issue created as ISSUE-{issue_id:04d}. It may be a recurrence of ISSUE-{previous_id:04d}.", "warning")
        else:
            flash(f"New issue created as ISSUE-{issue_id:04d}.", "success")
        return redirect(url_for("issue_detail", issue_id=issue_id))
    return render_template("new_complaint.html", categories=categories, severities=SEVERITIES)

@app.route("/issues/<int:issue_id>")
@login_required
def issue_detail(issue_id):
    conn = db()
    issue = conn.execute("""
        SELECT i.*, o.name AS organization_name,
        (SELECT COUNT(*) FROM complaints c WHERE c.issue_id=i.id) AS report_count,
        (SELECT COUNT(*) FROM confirmations cf WHERE cf.issue_id=i.id) AS confirmations
        FROM issues i JOIN organizations o ON o.id=i.organization_id
        WHERE i.id=? AND i.organization_id=?
    """, (issue_id, session["organization_id"])).fetchone()
    if not issue:
        conn.close()
        flash("Issue not found.", "danger")
        return redirect(url_for("dashboard"))
    complaints = conn.execute("""
        SELECT c.*, u.name AS reporter_name FROM complaints c
        JOIN users u ON u.id=c.user_id WHERE c.issue_id=? ORDER BY c.created_at
    """, (issue_id,)).fetchall()
    updates = conn.execute("""
        SELECT l.*, u.name AS admin_name FROM updates_log l
        JOIN users u ON u.id=l.user_id WHERE l.issue_id=? ORDER BY l.created_at DESC
    """, (issue_id,)).fetchall()
    confirmed = conn.execute(
        "SELECT id FROM confirmations WHERE issue_id=? AND user_id=?",
        (issue_id, session["user_id"])
    ).fetchone()
    conn.close()
    return render_template("issue_detail.html", issue=issue, complaints=complaints,
                           updates=updates, confirmed=bool(confirmed), statuses=STATUSES)

@app.route("/issues/<int:issue_id>/confirm", methods=["POST"])
@login_required
def confirm_issue(issue_id):
    conn = db()
    issue = conn.execute("SELECT id FROM issues WHERE id=? AND organization_id=?",
                         (issue_id, session["organization_id"])).fetchone()
    if issue:
        try:
            conn.execute("INSERT INTO confirmations(issue_id,user_id,created_at) VALUES(?,?,?)",
                         (issue_id, session["user_id"], now()))
            conn.commit()
            flash("You confirmed that this issue affects you.", "success")
        except sqlite3.IntegrityError:
            flash("You have already confirmed this issue.", "info")
    conn.close()
    return redirect(url_for("issue_detail", issue_id=issue_id))

@app.route("/issues/<int:issue_id>/update", methods=["POST"])
@admin_required
def update_issue(issue_id):
    status = request.form.get("status", "Open")
    priority = request.form.get("priority", "Medium")
    department = request.form.get("department", "").strip()
    remarks = request.form.get("remarks", "").strip()
    if status not in STATUSES or priority not in SEVERITIES:
        flash("Invalid status or priority.", "danger")
        return redirect(url_for("issue_detail", issue_id=issue_id))
    conn = db()
    issue = conn.execute("SELECT * FROM issues WHERE id=? AND organization_id=?",
                         (issue_id, session["organization_id"])).fetchone()
    if not issue:
        conn.close()
        flash("Issue not found.", "danger")
        return redirect(url_for("dashboard"))
    conn.execute("""
        UPDATE issues SET status=?, priority=?, assigned_department=?, updated_at=? WHERE id=?
    """, (status, priority, department, now(), issue_id))
    conn.execute("""
        INSERT INTO updates_log(issue_id,user_id,old_status,new_status,remarks,created_at)
        VALUES(?,?,?,?,?,?)
    """, (issue_id, session["user_id"], issue["status"], status, remarks, now()))
    conn.commit()
    conn.close()
    flash("Issue updated successfully.", "success")
    return redirect(url_for("issue_detail", issue_id=issue_id))

@app.route("/reports/export.csv")
@admin_required
def export_csv():
    conn = db()
    rows = conn.execute("""
        SELECT i.id, i.title, i.category, i.site, i.location, i.severity, i.priority,
               i.status, i.assigned_department, i.recurring, i.created_at,
               COUNT(c.id) AS report_count
        FROM issues i LEFT JOIN complaints c ON c.issue_id=i.id
        WHERE i.organization_id=?
        GROUP BY i.id ORDER BY i.created_at DESC
    """, (session["organization_id"],)).fetchall()
    conn.close()
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Issue ID","Title","Category","Site","Location","Severity","Priority",
                     "Status","Department","Recurring","Created At","Complaint Reports"])
    for r in rows:
        writer.writerow([r["id"],r["title"],r["category"],r["site"],r["location"],r["severity"],
                         r["priority"],r["status"],r["assigned_department"],r["recurring"],
                         r["created_at"],r["report_count"]])
    return Response(output.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition":"attachment; filename=issuetwin_report.csv"})

@app.route("/admin/users", methods=["GET", "POST"])
@admin_required
def manage_users():
    conn = db()
    if request.method == "POST":
        user_id = request.form.get("user_id", type=int)
        role = request.form.get("role", "reporter")
        if role in ("admin", "reporter"):
            conn.execute("UPDATE users SET role=? WHERE id=? AND organization_id=?",
                         (role, user_id, session["organization_id"]))
            conn.commit()
            flash("User role updated.", "success")
        return redirect(url_for("manage_users"))
    users = conn.execute("SELECT id,name,email,role,created_at FROM users WHERE organization_id=? ORDER BY name",
                         (session["organization_id"],)).fetchall()
    conn.close()
    return render_template("users.html", users=users)

@app.errorhandler(404)
def not_found(error):
    return render_template("404.html"), 404

if __name__ == "__main__":
    init_db()
    app.run(debug=True)
else:
    init_db()
