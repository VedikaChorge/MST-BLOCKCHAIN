import os, io, re, json, hashlib, secrets, sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from functools import wraps
from pathlib import Path
from flask import Flask, render_template, request, redirect, url_for, flash, session, send_file, abort, make_response
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from decimal import Decimal
from dotenv import load_dotenv
from web3 import Web3
from PIL import Image, ImageDraw, ImageFont
import qrcode
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from reportlab.lib.utils import ImageReader
import chain as chain_lib
import payments as payments_lib

load_dotenv()
BASE = Path(__file__).resolve().parent

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY") or secrets.token_hex(32)
app.config.update(
    MAX_CONTENT_LENGTH=int(os.getenv("MAX_UPLOAD_MB", "25")) * 1024 * 1024,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=os.getenv("SESSION_COOKIE_SECURE", "false").lower() == "true",
    PERMANENT_SESSION_LIFETIME=timedelta(hours=8),
)
ALLOWED_EXTENSIONS = set(os.getenv("ALLOWED_EXTENSIONS", "pdf,png,jpg,jpeg,webp,txt,doc,docx,zip,py,js,html,css,svg").split(","))
HASH_RE = re.compile(r"^[0-9a-f]{64}$")
TX_RE = re.compile(r"^0x[0-9a-fA-F]{64}$")
MAX_LOGIN_FAILURES, LOCKOUT = 5, timedelta(minutes=15)
STATUS_LABELS = {"local": "Not anchored", "pending": "Pending confirmation", "confirmed": "Confirmed", "failed": "Last attempt failed"}
REVIEW_LABELS = {"pending": "Pending review", "under_review": "Under review", "approved": "Approved", "rejected": "Rejected", "needs_info": "Needs more info"}
OPEN_REVIEW_STATUSES = ("pending", "under_review", "needs_info")
NOTE_REQUIRED_STATUSES = ("rejected", "needs_info")
FREE_EVIDENCE_LIMIT = int(os.getenv("FREE_EVIDENCE_LIMIT", "2"))
SUBSCRIPTION_DAYS = int(os.getenv("SUBSCRIPTION_DAYS", "30"))
CURRENCY_SYMBOL = os.getenv("CURRENCY_SYMBOL", "₹")
PLANS = {
    "evidence": {"label": "1 evidence credit", "description": "Register one more evidence record.",
                 "amount": round(float(os.getenv("PRICE_PER_EVIDENCE", "49")) * 100)},
    "subscription": {"label": f"{SUBSCRIPTION_DAYS}-day subscription", "description": f"Unlimited evidence for {SUBSCRIPTION_DAYS} days.",
                     "amount": round(float(os.getenv("SUBSCRIPTION_PRICE", "299")) * 100)},
}
USER_COLUMNS = {"evidence_credits": "INTEGER NOT NULL DEFAULT 0", "subscription_until": "TEXT DEFAULT ''"}
PAYMENT_COLUMNS = {"provider_order_id": "TEXT DEFAULT ''", "provider_payment_id": "TEXT DEFAULT ''", "crypto_amount": "TEXT DEFAULT ''"}
MST_PAYMENT_ADDRESS = os.getenv("MST_PAYMENT_ADDRESS", "").strip() if Web3.is_address(os.getenv("MST_PAYMENT_ADDRESS", "").strip()) else ""
MST_PRICES = {"evidence": Web3.to_wei(Decimal(os.getenv("PRICE_PER_EVIDENCE_MST", "1")), "ether"),
              "subscription": Web3.to_wei(Decimal(os.getenv("SUBSCRIPTION_PRICE_MST", "5")), "ether")}
CSRF_EXEMPT_PATHS = {"/webhooks/razorpay"}
BUSINESS = {
    "name": os.getenv("BUSINESS_NAME", ""), "email": os.getenv("SUPPORT_EMAIL", ""),
    "phone": os.getenv("SUPPORT_PHONE", ""), "address": os.getenv("BUSINESS_ADDRESS", ""),
}
RAZORPAY_CSP = ("default-src 'self'; script-src 'self' https://checkout.razorpay.com; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
                "font-src https://fonts.gstatic.com; img-src 'self' data: https://*.razorpay.com; frame-src https://*.razorpay.com; "
                "connect-src 'self' https://*.razorpay.com; frame-ancestors 'none'; form-action 'self'")
EVIDENCE_COLUMNS = {
    "billing": "TEXT DEFAULT ''",
    "chain_status": "TEXT NOT NULL DEFAULT 'local'", "registrant_address": "TEXT DEFAULT ''",
    "onchain_recorded_at": "INTEGER DEFAULT 0", "block_number": "INTEGER DEFAULT 0",
    "root_id": "INTEGER", "parent_id": "INTEGER", "version_no": "INTEGER NOT NULL DEFAULT 1",
    "review_status": "TEXT NOT NULL DEFAULT 'pending'", "review_note": "TEXT DEFAULT ''", "reviewed_at": "TEXT DEFAULT ''",
}
chain = chain_lib.from_env()
gateway = payments_lib.from_env()
DB_PATH = ORIGINALS = WATERMARKS = REPORTS = None

@contextmanager
def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        with conn: yield conn
    finally:
        conn.close()

def configure(db_path, storage):
    global DB_PATH, ORIGINALS, WATERMARKS, REPORTS
    storage = Path(storage); DB_PATH = Path(db_path)
    ORIGINALS, WATERMARKS, REPORTS = storage / "originals", storage / "watermarked", storage / "reports"
    for p in (storage, ORIGINALS, WATERMARKS, REPORTS): p.mkdir(parents=True, exist_ok=True)
    init_db()

def init_db():
    with db() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS users (
          id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT UNIQUE NOT NULL,
          email TEXT UNIQUE NOT NULL, password_hash TEXT NOT NULL,
          role TEXT NOT NULL DEFAULT 'user', created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS evidence (
          id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL,
          title TEXT NOT NULL, description TEXT DEFAULT '', original_name TEXT NOT NULL,
          stored_name TEXT NOT NULL, sha256 TEXT NOT NULL, file_size INTEGER NOT NULL,
          mime_type TEXT DEFAULT '', created_at TEXT NOT NULL, watermark_name TEXT DEFAULT '',
          chain_network TEXT DEFAULT '', tx_hash TEXT DEFAULT '', contract_address TEXT DEFAULT '',
          FOREIGN KEY(user_id) REFERENCES users(id)
        );
        CREATE TABLE IF NOT EXISTS audit_log (
          id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, evidence_id INTEGER,
          action TEXT NOT NULL, details TEXT DEFAULT '', created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS login_failures (
          id INTEGER PRIMARY KEY AUTOINCREMENT, attempt_key TEXT NOT NULL, created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS evidence_reviews (
          id INTEGER PRIMARY KEY AUTOINCREMENT, evidence_id INTEGER NOT NULL, admin_id INTEGER NOT NULL,
          status TEXT NOT NULL, note TEXT DEFAULT '', created_at TEXT NOT NULL,
          FOREIGN KEY(evidence_id) REFERENCES evidence(id), FOREIGN KEY(admin_id) REFERENCES users(id)
        );
        CREATE TABLE IF NOT EXISTS payments (
          id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, reference TEXT UNIQUE NOT NULL,
          kind TEXT NOT NULL, amount INTEGER NOT NULL, provider TEXT NOT NULL DEFAULT 'simulated',
          status TEXT NOT NULL DEFAULT 'pending', created_at TEXT NOT NULL, paid_at TEXT DEFAULT '',
          FOREIGN KEY(user_id) REFERENCES users(id)
        );
        CREATE INDEX IF NOT EXISTS idx_evidence_sha256 ON evidence(sha256);
        CREATE INDEX IF NOT EXISTS idx_login_failures_key ON login_failures(attempt_key, created_at);
        """)
        for table, columns in (("evidence", EVIDENCE_COLUMNS), ("users", USER_COLUMNS), ("payments", PAYMENT_COLUMNS)):
            existing = {r["name"] for r in c.execute(f"PRAGMA table_info({table})")}
            for column, ddl in columns.items():
                if column not in existing: c.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")
        c.execute("UPDATE evidence SET root_id=id WHERE root_id IS NULL")
        c.execute("UPDATE evidence SET chain_status='confirmed' WHERE chain_status='local' AND tx_hash<>''")
        if not c.execute("SELECT id FROM users WHERE role='admin'").fetchone():
            username = os.getenv("ADMIN_USERNAME", "admin")
            email = os.getenv("ADMIN_EMAIL", "admin@example.com")
            password = os.getenv("ADMIN_PASSWORD", "ChangeMe123!")
            c.execute("INSERT OR IGNORE INTO users(username,email,password_hash,role,created_at) VALUES(?,?,?,?,?)",
                      (username, email, generate_password_hash(password), "admin", now()))
def now(): return datetime.now(timezone.utc).isoformat(timespec="seconds")
def utc(ts): return datetime.fromtimestamp(int(ts), timezone.utc).isoformat() if ts else ""
def money(amount): return f"{CURRENCY_SYMBOL}{amount/100:.2f}"
def token_amount(wei):
    text=f"{Web3.from_wei(int(wei),'ether'):f}"
    return f"{text.rstrip('0').rstrip('.') if '.' in text else text} {chain.network.currency}"
def payment_amount(payment): return token_amount(payment["crypto_amount"]) if payment["provider"]=="mst" else money(payment["amount"])
def mst_payments_enabled(): return chain.network.key=="testnet" and bool(MST_PAYMENT_ADDRESS)
def mst_reference_data(reference): return "0x"+reference.encode().hex()
def billing_summary(c, user_id):
    u=c.execute("SELECT role,evidence_credits,subscription_until FROM users WHERE id=?",(user_id,)).fetchone()
    used=c.execute("SELECT COUNT(*) n FROM evidence WHERE user_id=?",(user_id,)).fetchone()["n"]
    return {"admin":u["role"]=="admin","used":used,"free_left":max(0,FREE_EVIDENCE_LIMIT-used),"credits":u["evidence_credits"],
            "subscription_until":u["subscription_until"],"subscribed":bool(u["subscription_until"]) and u["subscription_until"]>now()}
def upload_entitlement(c, user_id):
    s=billing_summary(c,user_id)
    if s["admin"]: return "admin"
    if s["subscribed"]: return "subscription"
    if s["free_left"]: return "free"
    if s["credits"]: return "credit"
    return None
def allowed(filename): return "." in filename and filename.rsplit(".",1)[1].lower() in ALLOWED_EXTENSIONS
def log_action(user_id, evidence_id, action, details=""):
    with db() as c: c.execute("INSERT INTO audit_log(user_id,evidence_id,action,details,created_at) VALUES(?,?,?,?,?)",(user_id,evidence_id,action,details,now()))
def login_required(f):
    @wraps(f)
    def wrapped(*a, **kw):
        if not session.get("user_id"):
            flash("Please log in to continue.", "warning"); return redirect(url_for("login"))
        return f(*a, **kw)
    return wrapped
def admin_required(f):
    @wraps(f)
    @login_required
    def wrapped(*a, **kw):
        if current_user()["role"]!="admin": abort(403)
        return f(*a, **kw)
    return wrapped
def current_user():
    if not session.get("user_id"): return None
    with db() as c: return c.execute("SELECT id,username,email,role FROM users WHERE id=?",(session["user_id"],)).fetchone()
def csrf_token():
    if "csrf" not in session: session["csrf"]=secrets.token_urlsafe(32)
    return session["csrf"]
def explorer_tx_url(e):
    return chain.network.tx_url(e["tx_hash"]) if e["tx_hash"] and e["chain_network"]==chain.network.name else ""
app.jinja_env.globals.update(csrf_token=csrf_token, explorer_tx_url=explorer_tx_url, status_labels=STATUS_LABELS, review_labels=REVIEW_LABELS)
app.jinja_env.filters["utc"]=utc
app.jinja_env.filters["money"]=money
app.jinja_env.filters["token_amount"]=token_amount
app.jinja_env.globals.update(payment_amount=payment_amount)
@app.context_processor
def inject_user(): return {"current_user": current_user(), "network": chain.network, "chain": chain}
@app.before_request
def csrf_protect():
    if request.method=="POST" and request.path not in CSRF_EXEMPT_PATHS:
        sent=request.form.get("csrf_token") or request.headers.get("X-CSRF-Token","")
        expected=session.get("csrf","")
        if not sent or not expected or not secrets.compare_digest(sent,expected): abort(400)
@app.after_request
def security_headers(response):
    response.headers.setdefault("X-Content-Type-Options","nosniff")
    response.headers.setdefault("X-Frame-Options","DENY")
    response.headers.setdefault("Referrer-Policy","same-origin")
    response.headers.setdefault("Content-Security-Policy","default-src 'self'; script-src 'self'; style-src 'self' https://fonts.googleapis.com; "
                                "font-src https://fonts.gstatic.com; img-src 'self' data:; frame-ancestors 'none'; form-action 'self'")
    return response
@app.route("/")
def index():
    if session.get("user_id"): return redirect(url_for("dashboard"))
    return render_template("landing.html")
@app.route("/register", methods=["GET","POST"])
def register():
    if request.method=="POST":
        username=request.form.get("username","").strip()
        email=request.form.get("email","").strip().lower()
        password=request.form.get("password","")
        if len(username)<3 or len(password)<10 or "@" not in email:
            flash("Use a username of 3+ characters, a valid email, and a password of at least 10 characters.","danger")
        else:
            try:
                with db() as c:
                    c.execute("INSERT INTO users(username,email,password_hash,created_at) VALUES(?,?,?,?)",
                              (username,email,generate_password_hash(password),now()))
                flash("Account created. Please log in.","success"); return redirect(url_for("login"))
            except sqlite3.IntegrityError: flash("Username or email already exists.","danger")
    return render_template("register.html")
def login_locked(key):
    since=(datetime.now(timezone.utc)-LOCKOUT).isoformat(timespec="seconds")
    with db() as c: n=c.execute("SELECT COUNT(*) n FROM login_failures WHERE attempt_key=? AND created_at>=?",(key,since)).fetchone()["n"]
    return n>=MAX_LOGIN_FAILURES
@app.route("/login", methods=["GET","POST"])
def login():
    if request.method=="POST":
        identity=request.form.get("identity","").strip()
        key=f"{request.remote_addr}|{identity.lower()}"
        if login_locked(key):
            flash("Too many failed login attempts. Try again in 15 minutes.","danger"); return render_template("login.html")
        with db() as c: user=c.execute("SELECT * FROM users WHERE username=? OR email=?",(identity,identity.lower())).fetchone()
        if user and check_password_hash(user["password_hash"],request.form.get("password","")):
            with db() as c: c.execute("DELETE FROM login_failures WHERE attempt_key=?",(key,))
            session.clear(); session.permanent=True; session["user_id"]=user["id"]
            log_action(user["id"],None,"login")
            return redirect(url_for("dashboard"))
        with db() as c: c.execute("INSERT INTO login_failures(attempt_key,created_at) VALUES(?,?)",(key,now()))
        flash("Invalid username/email or password.","danger")
    return render_template("login.html")
@app.post("/logout")
def logout():
    if session.get("user_id"): log_action(session["user_id"],None,"logout")
    session.clear(); flash("You have been logged out.","success"); return redirect(url_for("index"))
@app.route("/account/password", methods=["GET","POST"])
@login_required
def change_password():
    if request.method=="POST":
        with db() as c: user=c.execute("SELECT password_hash FROM users WHERE id=?",(session["user_id"],)).fetchone()
        new=request.form.get("new_password","")
        if not check_password_hash(user["password_hash"],request.form.get("current_password","")):
            flash("Current password is incorrect.","danger")
        elif len(new)<10: flash("The new password must be at least 10 characters.","danger")
        elif new!=request.form.get("confirm_password",""): flash("The new passwords do not match.","danger")
        else:
            with db() as c: c.execute("UPDATE users SET password_hash=? WHERE id=?",(generate_password_hash(new),session["user_id"]))
            log_action(session["user_id"],None,"password_changed")
            flash("Password changed.","success"); return redirect(url_for("dashboard"))
    return render_template("account_password.html")
@app.route("/dashboard")
@login_required
def dashboard():
    u=current_user()
    q=request.args.get("q","").strip(); status=request.args.get("status","")
    where,params=[],[]
    if u["role"]!="admin": where.append("e.user_id=?"); params.append(u["id"])
    scope_sql=" AND ".join(where) or "1=1"; scope_params=list(params)
    if q:
        like=f"%{q}%"
        where.append("(e.title LIKE ? OR e.description LIKE ? OR e.original_name LIKE ? OR e.sha256 LIKE ?)")
        params+=[like,like,like,f"{q.lower()}%"]
    if status in STATUS_LABELS: where.append("e.chain_status=?"); params.append(status)
    with db() as c:
        rows=c.execute(f"SELECT e.*,u.username FROM evidence e JOIN users u ON u.id=e.user_id WHERE {' AND '.join(where) or '1=1'} ORDER BY e.id DESC LIMIT 200",params).fetchall()
        total=c.execute(f"SELECT COUNT(*) n FROM evidence e WHERE {scope_sql}",scope_params).fetchone()["n"]
        anchored=c.execute(f"SELECT COUNT(*) n FROM evidence e WHERE {scope_sql} AND e.chain_status='confirmed'",scope_params).fetchone()["n"]
        users=c.execute("SELECT COUNT(*) n FROM users").fetchone()["n"] if u["role"]=="admin" else None
        plan=billing_summary(c,u["id"])
    return render_template("dashboard.html", evidence=rows, total=total, anchored=anchored, users=users, q=q, status=status, plan=plan, free_limit=FREE_EVIDENCE_LIMIT)
def parent_from_request():
    parent_id=request.values.get("parent_id","").strip()
    if not parent_id: return None
    if not parent_id.isdigit(): abort(400)
    return get_owned_evidence(int(parent_id))
def payment_required():
    flash(f"You have used your {FREE_EVIDENCE_LIMIT} free evidence records. Buy an evidence credit or subscribe to register more.","warning")
    return redirect(url_for("billing"))
@app.route("/upload", methods=["GET","POST"])
@login_required
def upload():
    parent=parent_from_request()
    with db() as c: entitlement=upload_entitlement(c,session["user_id"])
    if not entitlement: return payment_required()
    if request.method=="POST":
        f=request.files.get("file"); title=request.form.get("title","").strip(); desc=request.form.get("description","").strip()
        retry=redirect(url_for("upload",parent_id=parent["id"]) if parent else url_for("upload"))
        if not f or not f.filename or not title:
            flash("Choose a file and enter a title.","danger"); return retry
        original=secure_filename(f.filename)
        if not allowed(original): flash("File extension is not allowed.","danger"); return retry
        data=f.read()
        if not data: flash("The uploaded file is empty.","danger"); return retry
        digest=hashlib.sha256(data).hexdigest()
        stored=f"{secrets.token_hex(12)}_{original}"
        (ORIGINALS/stored).write_bytes(data)
        watermark_name=""
        if (f.mimetype or "").startswith("image/"):
            try:
                im=Image.open(io.BytesIO(data)).convert("RGBA")
                overlay=Image.new("RGBA", im.size, (255,255,255,0)); draw=ImageDraw.Draw(overlay)
                text=f"IP Evidence Hub | {digest[:16]}"
                font=ImageFont.load_default()
                bbox=draw.textbbox((0,0),text,font=font)
                x=max(8,im.width-bbox[2]-16); y=max(8,im.height-bbox[3]-16)
                draw.rounded_rectangle((x-6,y-5,x+bbox[2]+6,y+bbox[3]+5),radius=5,fill=(0,0,0,130))
                draw.text((x,y),text,font=font,fill=(255,255,255,220))
                out=Image.alpha_composite(im,overlay).convert("RGB")
                watermark_name=f"wm_{stored.rsplit('.',1)[0]}.jpg"
                out.save(WATERMARKS/watermark_name,quality=92)
            except Exception: watermark_name=""
        with db() as c:
            entitlement=upload_entitlement(c,session["user_id"])
            if entitlement=="credit" and not c.execute("UPDATE users SET evidence_credits=evidence_credits-1 WHERE id=? AND evidence_credits>0",(session["user_id"],)).rowcount:
                entitlement=None
            if entitlement:
                duplicate=c.execute("SELECT id,created_at FROM evidence WHERE sha256=? ORDER BY id LIMIT 1",(digest,)).fetchone()
                root_id=parent["root_id"] if parent else None
                version=c.execute("SELECT MAX(version_no)+1 n FROM evidence WHERE root_id=?",(root_id,)).fetchone()["n"] if parent else 1
                cur=c.execute("""INSERT INTO evidence(user_id,title,description,original_name,stored_name,sha256,file_size,mime_type,created_at,watermark_name,root_id,parent_id,version_no,billing)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",(session["user_id"],title,desc,original,stored,digest,len(data),f.mimetype or "",now(),watermark_name,root_id,parent["id"] if parent else None,version,entitlement))
                eid=cur.lastrowid
                if not parent: c.execute("UPDATE evidence SET root_id=id WHERE id=?",(eid,))
        if not entitlement:
            (ORIGINALS/stored).unlink(missing_ok=True)
            if watermark_name: (WATERMARKS/watermark_name).unlink(missing_ok=True)
            return payment_required()
        log_action(session["user_id"],eid,"evidence_uploaded",f"sha256={digest} billing={entitlement}" + (f" version={version} of #{root_id}" if parent else ""))
        if duplicate:
            flash(f"This file is identical to one already recorded as evidence #{duplicate['id']} on {duplicate['created_at'][:10]}.","warning")
        flash(f"Evidence saved. SHA-256: {digest}","success"); return redirect(url_for("evidence_detail", evidence_id=eid))
    return render_template("upload.html",parent=parent)
def get_evidence(eid, user=None):
    with db() as c: row=c.execute("SELECT e.*,u.username FROM evidence e JOIN users u ON u.id=e.user_id WHERE e.id=?",(eid,)).fetchone()
    if not row: abort(404)
    u=current_user()
    if not u or (row["user_id"] != u["id"] and u["role"]!="admin"): abort(403)
    return row
def get_owned_evidence(eid):
    row=get_evidence(eid)
    if row["user_id"]!=session.get("user_id"): abort(403)
    return row
def reference_id(e): return f"IPEH-{e['id']}"
@app.route("/evidence/<int:evidence_id>")
@login_required
def evidence_detail(evidence_id):
    e=get_evidence(evidence_id)
    path=ORIGINALS/e["stored_name"]
    current_hash=hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else "MISSING"
    with db() as c:
        logs=c.execute("SELECT * FROM audit_log WHERE evidence_id=? ORDER BY id DESC",(evidence_id,)).fetchall()
        versions=c.execute("SELECT id,title,version_no,created_at,sha256 FROM evidence WHERE root_id=? ORDER BY version_no",(e["root_id"],)).fetchall()
        reviews=c.execute("SELECT r.*,u.username FROM evidence_reviews r JOIN users u ON u.id=r.admin_id WHERE r.evidence_id=? ORDER BY r.id DESC",(evidence_id,)).fetchall()
    is_owner=e["user_id"]==session["user_id"]
    can_anchor=is_owner and chain.configured and e["chain_status"]!="confirmed" and e["review_status"]=="approved"
    calldata=chain.calldata(e["sha256"],reference_id(e)) if can_anchor else ""
    return render_template("evidence_detail.html",e=e,current_hash=current_hash,logs=logs,versions=versions,reviews=reviews,is_owner=is_owner,calldata=calldata)
@app.route("/evidence/<int:evidence_id>/new-version")
@login_required
def new_version(evidence_id):
    return render_template("upload.html",parent=get_owned_evidence(evidence_id))
@app.route("/evidence/<int:evidence_id>/download")
@login_required
def download(evidence_id):
    e=get_evidence(evidence_id); path=ORIGINALS/e["stored_name"]
    if not path.exists(): abort(404)
    log_action(session["user_id"],evidence_id,"original_downloaded")
    return send_file(path,as_attachment=True,download_name=e["original_name"])
@app.route("/evidence/<int:evidence_id>/watermark")
@login_required
def watermark_download(evidence_id):
    e=get_evidence(evidence_id)
    if not e["watermark_name"] or not (WATERMARKS/e["watermark_name"]).exists(): abort(404)
    log_action(session["user_id"],evidence_id,"watermarked_downloaded")
    return send_file(WATERMARKS/e["watermark_name"],as_attachment=True,download_name=e["watermark_name"])
def save_anchor(e, result):
    with db() as c:
        c.execute("""UPDATE evidence SET tx_hash=?,chain_status=?,chain_network=?,contract_address=?,registrant_address=?,onchain_recorded_at=?,block_number=?
                     WHERE id=?""",(result.tx_hash,result.status,chain.network.name,chain.contract_address,result.registrant,result.recorded_at,result.block_number,e["id"]))
    log_action(session["user_id"],e["id"],f"anchor_{result.status}",f"network={chain.network.name} tx={result.tx_hash}")
    if result.status=="confirmed": flash(f"Evidence anchored on {chain.network.name} in block {result.block_number}.","success")
    elif result.status=="pending": flash("Transaction submitted and waiting for confirmation. Use Refresh status in a moment.","warning")
    else: flash(f"Transaction {result.tx_hash} did not register this evidence in the registry contract.","danger")
def not_approved(e):
    if e["review_status"]=="approved": return False
    flash("Blockchain anchoring is available only after an admin approves this evidence.","warning")
    return True
def chain_failure(action):
    app.logger.exception("MST %s failed", action)
    flash("Could not complete the request on the MST network. Check the RPC, contract address and signer balance, then try again.","danger")
@app.post("/evidence/<int:evidence_id>/anchor/server")
@login_required
def anchor_server(evidence_id):
    e=get_owned_evidence(evidence_id); back=redirect(url_for("evidence_detail",evidence_id=evidence_id))
    if e["chain_status"] in ("confirmed","pending"): flash("This evidence already has an on-chain transaction.","warning"); return back
    if not_approved(e): return back
    if not chain.server_signer: flash("Server signing is not configured. Use MetaMask, or set CONTRACT_ADDRESS and MST_SIGNER_PRIVATE_KEY.","danger"); return back
    try:
        existing=chain.lookup(e["sha256"])
        if existing:
            flash(f"This file hash is already registered on-chain since {utc(existing.recorded_at)} by {existing.registrant}.","danger"); return back
        result=chain.register(e["sha256"],reference_id(e))
    except Exception:
        chain_failure("server anchoring"); return back
    save_anchor(e,result); return back
@app.post("/evidence/<int:evidence_id>/anchor/wallet")
@login_required
def anchor_wallet(evidence_id):
    e=get_owned_evidence(evidence_id); back=redirect(url_for("evidence_detail",evidence_id=evidence_id))
    tx_hash=request.form.get("tx_hash","").strip()
    if not TX_RE.match(tx_hash): flash("Invalid transaction hash.","danger"); return back
    if e["chain_status"]=="confirmed": flash("This evidence is already anchored.","warning"); return back
    if not_approved(e): return back
    if not chain.configured: flash("No registry contract is configured.","danger"); return back
    try: result=chain.verify(tx_hash.lower(),e["sha256"],timeout=45)
    except Exception:
        chain_failure("wallet transaction verification"); return back
    save_anchor(e,result); return back
@app.post("/evidence/<int:evidence_id>/anchor/refresh")
@login_required
def anchor_refresh(evidence_id):
    e=get_owned_evidence(evidence_id); back=redirect(url_for("evidence_detail",evidence_id=evidence_id))
    if not e["tx_hash"] or e["chain_status"]=="confirmed": return back
    try: result=chain.verify(e["tx_hash"],e["sha256"],timeout=10)
    except Exception:
        chain_failure("status refresh"); return back
    save_anchor(e,result); return back
@app.route("/verify", methods=["GET","POST"])
def verify():
    result=None
    if request.method=="POST":
        f=request.files.get("file"); claimed=request.form.get("sha256","").strip().lower()
        if f and f.filename:
            digest=hashlib.sha256(f.read()).hexdigest()
            result={"hash":digest,"matches":bool(claimed and secrets.compare_digest(digest,claimed)),"claimed":claimed}
        else: flash("Select a file to hash.","warning")
    return render_template("verify.html",result=result)
@app.route("/lookup", methods=["GET","POST"])
def lookup():
    digest=None; records=[]; onchain=None; chain_error=False
    if request.method=="POST":
        f=request.files.get("file")
        if f and f.filename: digest=hashlib.sha256(f.read()).hexdigest()
        else: flash("Select a file to look up.","warning")
    else:
        raw=request.args.get("sha256","").strip().lower()
        if raw and HASH_RE.match(raw): digest=raw
        elif raw: flash("A SHA-256 hash is 64 hexadecimal characters.","danger")
    if digest:
        with db() as c: records=c.execute("SELECT id,title,created_at,chain_status FROM evidence WHERE sha256=? ORDER BY id",(digest,)).fetchall()
        if chain.configured:
            try: onchain=chain.lookup(digest)
            except Exception:
                app.logger.exception("MST lookup failed"); chain_error=True
    return render_template("lookup.html",digest=digest,records=records,onchain=onchain,chain_error=chain_error)
@app.route("/evidence/<int:evidence_id>/report")
@login_required
def report(evidence_id):
    e=get_evidence(evidence_id)
    verify_url=request.url_root.rstrip("/") + url_for("public_verify", evidence_id=evidence_id)
    qr=qrcode.make(verify_url); qr_path=REPORTS/f"qr_{evidence_id}.png"; qr.save(qr_path)
    path=REPORTS/f"evidence_{evidence_id}.pdf"
    c=canvas.Canvas(str(path),pagesize=A4); w,h=A4
    c.setTitle(f"IP Evidence Certificate {evidence_id}")
    c.setFont("Helvetica-Bold",20); c.drawString(48,h-58,"IP Evidence Hub")
    c.setFont("Helvetica-Bold",14); c.drawString(48,h-90,"Evidence Integrity Certificate")
    c.setFont("Helvetica",10)
    fields=[("Evidence ID",str(e["id"])),("Title",e["title"]),("Version",str(e["version_no"])),("Submitted by",e["username"]),("Original filename",e["original_name"]),
            ("Recorded (UTC)",e["created_at"]),("File size",f'{e["file_size"]} bytes'),("SHA-256",e["sha256"]),("Review status",REVIEW_LABELS[e["review_status"]]),
            ("Blockchain status",STATUS_LABELS.get(e["chain_status"],e["chain_status"])),("Network",e["chain_network"] or "Local-only")]
    if e["tx_hash"]: fields+=[("Transaction",e["tx_hash"]),("Contract",e["contract_address"])]
    if e["chain_status"]=="confirmed":
        fields+=[("On-chain time (UTC)",utc(e["onchain_recorded_at"])),("Block",str(e["block_number"])),("Registrant",e["registrant_address"])]
    y=h-130
    for label,value in fields:
        c.setFont("Helvetica-Bold",9); c.drawString(48,y,label+":")
        c.setFont("Helvetica",9)
        chunks=[value[i:i+85] for i in range(0,len(value),85)] or [""]
        c.drawString(150,y,chunks[0]); y-=16
        for chunk in chunks[1:]: c.drawString(150,y,chunk); y-=13
    c.drawImage(ImageReader(str(qr_path)),w-145,65,width=90,height=90, preserveAspectRatio=True,mask="auto")
    c.setFont("Helvetica",8); c.drawString(48,82,"Scan QR code to view public integrity status.")
    c.drawString(48,65,"This certificate records a hash; it is not a legal ownership determination.")
    c.save()
    log_action(session["user_id"],evidence_id,"report_generated")
    return send_file(path,as_attachment=True,download_name=f"IP_Evidence_{evidence_id}.pdf")
@app.route("/public/verify/<int:evidence_id>")
def public_verify(evidence_id):
    with db() as c: e=c.execute("""SELECT id,title,original_name,sha256,file_size,created_at,version_no,chain_network,chain_status,tx_hash,contract_address,
                                   registrant_address,onchain_recorded_at,block_number FROM evidence WHERE id=?""",(evidence_id,)).fetchone()
    if not e: abort(404)
    return render_template("public_verify.html",e=e)
@app.route("/admin/users")
@admin_required
def admin_users():
    with db() as c: users=c.execute("""SELECT u.id,u.username,u.email,u.role,u.created_at,u.evidence_credits,u.subscription_until,
                                       (SELECT COUNT(*) FROM evidence e WHERE e.user_id=u.id) evidence_count FROM users u ORDER BY u.id""").fetchall()
    return render_template("admin_users.html",users=users,now=now())
@app.post("/admin/users/<int:user_id>/role")
@admin_required
def admin_set_role(user_id):
    role=request.form.get("role","")
    if role not in ("user","admin"): abort(400)
    if user_id==session["user_id"]: flash("You cannot change your own role.","danger"); return redirect(url_for("admin_users"))
    with db() as c:
        target=c.execute("SELECT username FROM users WHERE id=?",(user_id,)).fetchone()
        if not target: abort(404)
        c.execute("UPDATE users SET role=? WHERE id=?",(role,user_id))
    log_action(session["user_id"],None,"role_changed",f"user={target['username']} role={role}")
    flash(f"{target['username']} is now {role}.","success"); return redirect(url_for("admin_users"))
@app.route("/billing")
@login_required
def billing():
    with db() as c:
        summary=billing_summary(c,session["user_id"])
        payments=c.execute("SELECT * FROM payments WHERE user_id=? ORDER BY id DESC",(session["user_id"],)).fetchall()
    return render_template("billing.html",summary=summary,payments=payments,plans=PLANS,free_limit=FREE_EVIDENCE_LIMIT,
                           mst_enabled=mst_payments_enabled(),mst_prices=MST_PRICES)
@app.post("/billing/checkout")
@login_required
def checkout_start():
    kind=request.form.get("kind","")
    if kind not in PLANS: abort(400)
    reference=f"pay_{secrets.token_hex(12)}"; provider="razorpay" if gateway else "simulated"
    with db() as c:
        c.execute("INSERT INTO payments(user_id,reference,kind,amount,provider,created_at) VALUES(?,?,?,?,?,?)",(session["user_id"],reference,kind,PLANS[kind]["amount"],provider,now()))
    log_action(session["user_id"],None,"payment_started",f"{reference} {kind} {money(PLANS[kind]['amount'])} via {provider}")
    if gateway:
        try: order_id=gateway.create_order(PLANS[kind]["amount"],reference)
        except Exception:
            app.logger.exception("Razorpay order creation failed")
            with db() as c: c.execute("UPDATE payments SET status='failed' WHERE reference=?",(reference,))
            flash("Could not start the Razorpay payment. Check the Razorpay keys and try again.","danger"); return redirect(url_for("billing"))
        with db() as c: c.execute("UPDATE payments SET provider_order_id=? WHERE reference=?",(order_id,reference))
    return redirect(url_for("checkout_page",reference=reference))
def own_payment(c, reference):
    row=c.execute("SELECT * FROM payments WHERE reference=? AND user_id=?",(reference,session["user_id"])).fetchone()
    if not row: abort(404)
    return row
@app.route("/billing/checkout/<reference>")
@login_required
def checkout_page(reference):
    with db() as c: payment=own_payment(c,reference)
    if payment["status"]!="pending": return redirect(url_for("billing"))
    response=make_response(render_template("checkout.html",payment=payment,plan=PLANS[payment["kind"]],razorpay_key=gateway.key_id if gateway else "",razorpay_mode=gateway.mode if gateway else "",
                                           mst_to=MST_PAYMENT_ADDRESS,mst_data=mst_reference_data(payment["reference"])))
    if payment["provider"]=="razorpay": response.headers["Content-Security-Policy"]=RAZORPAY_CSP
    return response
def settle_payment(c, payment, status, provider_payment_id=""):
    """Move a pending payment to paid/failed and grant what was bought; False if it was already settled."""
    if not c.execute("UPDATE payments SET status=?,paid_at=?,provider_payment_id=? WHERE id=? AND status='pending'",
                     (status,now() if status=="paid" else "",provider_payment_id,payment["id"])).rowcount:
        return False
    if status=="paid" and payment["kind"]=="evidence":
        c.execute("UPDATE users SET evidence_credits=evidence_credits+1 WHERE id=?",(payment["user_id"],))
    elif status=="paid":
        current=c.execute("SELECT subscription_until FROM users WHERE id=?",(payment["user_id"],)).fetchone()["subscription_until"]
        start=max(datetime.now(timezone.utc),datetime.fromisoformat(current)) if current else datetime.now(timezone.utc)
        c.execute("UPDATE users SET subscription_until=? WHERE id=?",((start+timedelta(days=SUBSCRIPTION_DAYS)).isoformat(timespec="seconds"),payment["user_id"]))
    return True
def finalize_payment(reference, status, provider_payment_id=""):
    with db() as c:
        payment=own_payment(c,reference)
        if not settle_payment(c,payment,status,provider_payment_id):
            flash("This payment has already been processed.","warning"); return redirect(url_for("billing"))
    log_action(session["user_id"],None,f"payment_{status}",f"{reference} {payment['kind']} {payment_amount(payment)} {provider_payment_id}".strip())
    if status=="paid": flash(f"Payment of {payment_amount(payment)} received for {PLANS[payment['kind']]['label']}.","success")
    return redirect(url_for("billing"))
@app.post("/billing/checkout/<reference>/complete")
@login_required
def checkout_complete(reference):
    outcome=request.form.get("outcome","")
    if outcome not in ("success","fail"): abort(400)
    with db() as c: payment=own_payment(c,reference)
    if payment["provider"]!="simulated": abort(400)
    if outcome=="fail": flash("Payment failed. You have not been charged.","danger")
    return finalize_payment(reference,"paid" if outcome=="success" else "failed")
@app.post("/billing/checkout/<reference>/razorpay")
@login_required
def checkout_razorpay(reference):
    with db() as c: payment=own_payment(c,reference)
    if payment["provider"]!="razorpay" or not gateway: abort(400)
    order_id=request.form.get("razorpay_order_id",""); payment_id=request.form.get("razorpay_payment_id","")
    if order_id!=payment["provider_order_id"] or not payment_id or not gateway.verify_signature(order_id,payment_id,request.form.get("razorpay_signature","")):
        flash("The Razorpay payment could not be verified. You have not been given credit; contact the administrator if you were charged.","danger")
        return finalize_payment(reference,"failed")
    return finalize_payment(reference,"paid",payment_id)
@app.post("/billing/checkout/mst")
@login_required
def checkout_start_mst():
    kind=request.form.get("kind","")
    if kind not in PLANS or not mst_payments_enabled(): abort(400)
    reference=f"pay_{secrets.token_hex(12)}"
    with db() as c:
        c.execute("INSERT INTO payments(user_id,reference,kind,amount,crypto_amount,provider,created_at) VALUES(?,?,?,?,?,?,?)",
                  (session["user_id"],reference,kind,0,str(MST_PRICES[kind]),"mst",now()))
    log_action(session["user_id"],None,"payment_started",f"{reference} {kind} {token_amount(MST_PRICES[kind])} via MetaMask")
    return redirect(url_for("checkout_page",reference=reference))
def verify_mst_payment(payment, tx_hash, timeout):
    with db() as c:
        if c.execute("SELECT 1 FROM payments WHERE provider_payment_id=? AND id<>?",(tx_hash,payment["id"])).fetchone():
            flash("This transaction has already been used for another payment.","danger"); return redirect(url_for("billing"))
        c.execute("UPDATE payments SET provider_payment_id=? WHERE id=? AND status='pending'",(tx_hash,payment["id"]))
    try: result=chain.verify_payment(tx_hash,MST_PAYMENT_ADDRESS,int(payment["crypto_amount"]),mst_reference_data(payment["reference"]),timeout=timeout)
    except Exception:
        app.logger.exception("MST payment verification failed")
        flash("Could not reach the MST network to verify the payment. Use Refresh status on the Billing page shortly.","danger"); return redirect(url_for("billing"))
    if result.status=="pending":
        flash("Transaction sent and waiting for confirmation. Use Refresh status on the Billing page in a moment.","warning"); return redirect(url_for("billing"))
    if result.status=="failed":
        with db() as c: c.execute("UPDATE payments SET provider_payment_id='' WHERE id=? AND status='pending'",(payment["id"],))
        flash("The transaction does not match this payment (wrong wallet, amount or reference). You have not been given credit.","danger")
        return redirect(url_for("checkout_page",reference=payment["reference"]))
    return finalize_payment(payment["reference"],"paid",tx_hash)
@app.post("/billing/checkout/<reference>/mst")
@login_required
def checkout_mst(reference):
    with db() as c: payment=own_payment(c,reference)
    if payment["provider"]!="mst" or not mst_payments_enabled(): abort(400)
    tx_hash=request.form.get("tx_hash","").strip()
    if not TX_RE.match(tx_hash):
        flash("Invalid transaction hash.","danger"); return redirect(url_for("checkout_page",reference=reference))
    return verify_mst_payment(payment,tx_hash.lower(),timeout=45)
@app.post("/billing/checkout/<reference>/mst/refresh")
@login_required
def checkout_mst_refresh(reference):
    with db() as c: payment=own_payment(c,reference)
    if payment["provider"]!="mst" or not mst_payments_enabled() or payment["status"]!="pending" or not payment["provider_payment_id"]:
        return redirect(url_for("billing"))
    return verify_mst_payment(payment,payment["provider_payment_id"],timeout=10)
@app.post("/webhooks/razorpay")
def razorpay_webhook():
    """Credits payments Razorpay confirms server-to-server, e.g. when the user closed the browser before returning."""
    if not gateway or not gateway.webhooks_enabled: abort(404)
    body=request.get_data()
    if not gateway.verify_webhook(body,request.headers.get("X-Razorpay-Signature","")): abort(400)
    try: event=json.loads(body)
    except ValueError: abort(400)
    if event.get("event") not in ("payment.captured","order.paid"): return {"status":"ignored"}
    entity=((event.get("payload") or {}).get("payment") or {}).get("entity") or {}
    with db() as c:
        payment=c.execute("SELECT * FROM payments WHERE provider='razorpay' AND provider_order_id=?",(str(entity.get("order_id","")),)).fetchone()
        if not payment or entity.get("amount")!=payment["amount"]: return {"status":"ignored"}
        credited=settle_payment(c,payment,"paid",str(entity.get("id","")))
    if credited: log_action(payment["user_id"],None,"payment_paid",f"{payment['reference']} {payment['kind']} {money(payment['amount'])} {entity.get('id','')} via webhook")
    return {"status":"ok"}
@app.route("/admin/payments")
@admin_required
def admin_payments():
    with db() as c:
        rows=c.execute("SELECT p.*,u.username FROM payments p JOIN users u ON u.id=p.user_id ORDER BY p.id DESC LIMIT 300").fetchall()
        revenue=c.execute("SELECT COALESCE(SUM(amount),0) n FROM payments WHERE status='paid'").fetchone()["n"]
        subscribers=c.execute("SELECT COUNT(*) n FROM users WHERE subscription_until>?",(now(),)).fetchone()["n"]
        mst_received=sum(int(r["crypto_amount"]) for r in c.execute("SELECT crypto_amount FROM payments WHERE provider='mst' AND status='paid'"))
    return render_template("admin_payments.html",rows=rows,revenue=revenue,subscribers=subscribers,plans=PLANS,gateway_enabled=bool(gateway),
                           mst_enabled=mst_payments_enabled(),mst_received=mst_received)
@app.post("/admin/users/<int:user_id>/credits")
@admin_required
def admin_grant_credits(user_id):
    credits=request.form.get("credits","")
    if not credits.isdigit() or not 1<=int(credits)<=100: abort(400)
    with db() as c:
        target=c.execute("SELECT username FROM users WHERE id=?",(user_id,)).fetchone()
        if not target: abort(404)
        c.execute("UPDATE users SET evidence_credits=evidence_credits+? WHERE id=?",(int(credits),user_id))
    log_action(session["user_id"],None,"credits_granted",f"user={target['username']} credits={credits}")
    flash(f"Granted {credits} evidence credit(s) to {target['username']}.","success"); return redirect(url_for("admin_users"))
@app.route("/admin/review")
@admin_required
def admin_review():
    status=request.args.get("status","")
    with db() as c:
        counts={r["review_status"]:r["n"] for r in c.execute("SELECT review_status,COUNT(*) n FROM evidence GROUP BY review_status")}
        sql="SELECT e.*,u.username FROM evidence e JOIN users u ON u.id=e.user_id"
        if status in REVIEW_LABELS: rows=c.execute(sql+" WHERE e.review_status=? ORDER BY e.id",(status,)).fetchall()
        elif status=="all": rows=c.execute(sql+" ORDER BY e.id DESC LIMIT 300").fetchall()
        else:
            status=""
            rows=c.execute(sql+f" WHERE e.review_status IN ({','.join('?'*len(OPEN_REVIEW_STATUSES))}) ORDER BY e.id",OPEN_REVIEW_STATUSES).fetchall()
    return render_template("admin_review.html",rows=rows,counts=counts,status=status)
@app.post("/admin/evidence/<int:evidence_id>/review")
@admin_required
def admin_set_review(evidence_id):
    status=request.form.get("status",""); note=request.form.get("note","").strip()[:1000]
    if status not in REVIEW_LABELS: abort(400)
    e=get_evidence(evidence_id)
    back=redirect(url_for("admin_review",status=request.form.get("queue_status","")) if request.form.get("from")=="queue" else url_for("evidence_detail",evidence_id=evidence_id))
    if status in NOTE_REQUIRED_STATUSES and not note:
        flash(f"Add a note explaining why the evidence is marked '{REVIEW_LABELS[status]}'.","danger"); return back
    with db() as c:
        c.execute("INSERT INTO evidence_reviews(evidence_id,admin_id,status,note,created_at) VALUES(?,?,?,?,?)",(evidence_id,session["user_id"],status,note,now()))
        c.execute("UPDATE evidence SET review_status=?,review_note=?,reviewed_at=? WHERE id=?",(status,note,now(),evidence_id))
    log_action(session["user_id"],evidence_id,f"review_{status}",note)
    flash(f"Evidence #{evidence_id} ({e['title']}) marked as {REVIEW_LABELS[status]}.","success"); return back
@app.route("/admin/audit")
@admin_required
def admin_audit():
    action=request.args.get("action","")
    with db() as c:
        actions=[r["action"] for r in c.execute("SELECT DISTINCT action FROM audit_log ORDER BY action")]
        sql="SELECT a.*,u.username FROM audit_log a LEFT JOIN users u ON u.id=a.user_id"
        rows=c.execute(sql+(" WHERE a.action=?" if action else "")+" ORDER BY a.id DESC LIMIT 300",(action,) if action else ()).fetchall()
    return render_template("admin_audit.html",rows=rows,actions=actions,action=action)
POLICY_PAGES = {"terms": "Terms & Conditions", "privacy": "Privacy Policy", "refunds": "Refund & Cancellation Policy", "contact": "Contact us"}
def policy_page(page):
    return render_template(f"policy_{page}.html",title=POLICY_PAGES[page],business=BUSINESS,plans=PLANS,free_limit=FREE_EVIDENCE_LIMIT,updated="2026-10-29",
                           biz=BUSINESS["name"] or "[Set BUSINESS_NAME in .env]",email=BUSINESS["email"] or "[Set SUPPORT_EMAIL in .env]")
for _page in POLICY_PAGES: app.add_url_rule(f"/{_page}",_page,lambda _page=_page: policy_page(_page))
@app.route("/health")
def health(): return {"status":"ok","network":chain.network.key,"chain_id":chain.network.chain_id,"contract_configured":chain.configured}
configure(BASE / os.getenv("DATABASE_PATH", "ip_evidence.db"), BASE / os.getenv("STORAGE_PATH", "storage"))
if __name__=="__main__":
    app.run(host=os.getenv("HOST","127.0.0.1"),port=int(os.getenv("PORT","5000")),debug=os.getenv("FLASK_DEBUG","false").lower()=="true")
