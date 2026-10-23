from flask import Flask, render_template, request, redirect, url_for, flash, jsonify, abort, session
from werkzeug.utils import secure_filename
from pathlib import Path
from datetime import datetime
import sqlite3, os, uuid, json, hmac, hashlib, base64, urllib.request, urllib.error

BASE_DIR = Path(__file__).resolve().parent

def load_local_env():
    """Load optional local settings without overriding the process environment."""
    env_file = BASE_DIR / ".env"
    if not env_file.is_file():
        return
    for raw_line in env_file.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key, value = key.strip().lstrip("\ufeff"), value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        if key:
            os.environ.setdefault(key, value)

load_local_env()
DB_PATH = BASE_DIR / "laundry.db"
UPLOAD_DIR = BASE_DIR / "uploads"
UPLOAD_DIR.mkdir(exist_ok=True)
ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "webp"}
app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY") or os.urandom(32)
app.config["MAX_CONTENT_LENGTH"] = 8 * 1024 * 1024
app.config["PERMANENT_SESSION_LIFETIME"] = 60 * 60 * 8
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SESSION_COOKIE_SECURE"] = os.environ.get("COOKIE_SECURE", "0").lower() in ("1", "true", "yes")

@app.before_request
def require_admin_login():
    # LINE calls this endpoint directly; static assets and login must also remain reachable.
    if request.endpoint in ("line_webhook", "admin_login", "static", "liff_order", "liff_products", "liff_create_order"):
        return None
    if not session.get("admin_authenticated"):
        return redirect(url_for("admin_login", next=request.path))

@app.route("/login", methods=["GET", "POST"])
def admin_login():
    # Credentials explicitly requested for this local shop setup.
    configured_user = "admin"
    configured_password = "123"
    configured = bool(configured_user and configured_password)
    error = ""
    if request.method == "POST":
        if not configured:
            error = "ยังไม่ได้ตั้ง ADMIN_USERNAME และ ADMIN_PASSWORD ใน environment"
        else:
            username = request.form.get("username", "")
            password = request.form.get("password", "")
            valid_user = hmac.compare_digest(username.encode("utf-8"), configured_user.encode("utf-8"))
            valid_password = hmac.compare_digest(password.encode("utf-8"), configured_password.encode("utf-8"))
            if valid_user and valid_password:
                session.clear()
                session.permanent = True
                session["admin_authenticated"] = True
                destination = request.args.get("next", "")
                if not destination.startswith("/") or destination.startswith("//"):
                    destination = url_for("dashboard")
                return redirect(destination)
            error = "ชื่อผู้ใช้หรือรหัสผ่านไม่ถูกต้อง"
    return render_template("login.html", error=error, configured=configured)

@app.route("/logout", methods=["POST"])
def admin_logout():
    session.clear()
    return redirect(url_for("admin_login"))

SERVICES = {
    "wash": "ซักอย่างเดียว",
    "wash_dry": "ซักและอบแห้ง",
    "wash_iron": "ซักและรีด",
}
ORDER_STATUSES = {
    "pending_review": "รอกำหนดราคา",
    "awaiting_payment": "รอชำระเงิน",
    "accepted": "รับออเดอร์แล้ว",
    "washing": "กำลังซัก",
    "drying": "กำลังอบแห้ง",
    "ironing": "กำลังรีด",
    "ready": "พร้อมรับผ้า",
    "completed": "รับผ้าแล้ว",
    "cancelled": "ยกเลิก",
}
PAYMENT_STATUSES = {
    "unpaid": "ยังไม่ชำระ",
    "pending_verification": "รอตรวจสอบ",
    "paid": "ชำระแล้ว",
    "rejected": "ตรวจสอบไม่ผ่าน",
    "refunded": "คืนเงินแล้ว",
}

def now_iso():
    return datetime.now().isoformat(timespec="seconds")

def create_order_code():
    return "BW-" + uuid.uuid4().hex[:10].upper()

def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn

def init_db():
    with db() as con:
        con.executescript("""
        CREATE TABLE IF NOT EXISTS orders (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          order_code TEXT UNIQUE NOT NULL,
          customer_name TEXT NOT NULL,
          line_user_id TEXT DEFAULT '',
          phone TEXT DEFAULT '',
          service TEXT NOT NULL,
          detergent TEXT DEFAULT 'ร้านจัดให้',
          softener TEXT DEFAULT 'ไม่ระบุ',
          weight REAL,
          price REAL,
          payment_method TEXT DEFAULT '',
          payment_status TEXT NOT NULL DEFAULT 'unpaid',
          status TEXT NOT NULL DEFAULT 'pending_review',
          customer_photo TEXT DEFAULT '',
          payment_slip TEXT DEFAULT '',
          notes TEXT DEFAULT '',
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS products (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          category TEXT NOT NULL CHECK(category IN ('detergent','softener')),
          name TEXT NOT NULL,
          active INTEGER NOT NULL DEFAULT 1,
          created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS status_history (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          order_id INTEGER NOT NULL,
          old_status TEXT,
          new_status TEXT NOT NULL,
          note TEXT DEFAULT '',
          changed_by TEXT DEFAULT 'พนักงาน',
          created_at TEXT NOT NULL,
          FOREIGN KEY(order_id) REFERENCES orders(id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS staff_users (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          username TEXT UNIQUE NOT NULL,
          display_name TEXT NOT NULL,
          role TEXT NOT NULL DEFAULT 'staff',
          active INTEGER NOT NULL DEFAULT 1
        );
        CREATE TABLE IF NOT EXISTS settings (
          key TEXT PRIMARY KEY,
          value TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS line_events (
          event_id TEXT PRIMARY KEY,
          processed_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS line_sessions (
          line_user_id TEXT PRIMARY KEY,
          state TEXT NOT NULL,
          data TEXT NOT NULL DEFAULT '{}',
          updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS line_link_codes (
          code TEXT PRIMARY KEY,
          order_id INTEGER NOT NULL,
          expires_at TEXT NOT NULL,
          used_at TEXT,
          FOREIGN KEY(order_id) REFERENCES orders(id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS line_customers (
          line_user_id TEXT PRIMARY KEY,
          display_name TEXT DEFAULT '',
          linked_at TEXT NOT NULL
        );
        """)
        now = datetime.now().isoformat(timespec="seconds")
        count = con.execute("SELECT COUNT(*) FROM products").fetchone()[0]
        if count == 0:
            products = [
              ("detergent", "สูตรมาตรฐาน"), ("detergent", "สูตรสำหรับผิวบอบบาง"),
              ("detergent", "สูตรเข้มข้น"), ("detergent", "สูตรถนอมสีผ้า"),
              ("detergent", "สูตรกลิ่นหอม"),
              ("softener", "กลิ่นดอกไม้"), ("softener", "กลิ่นสะอาดสดชื่น"),
              ("softener", "กลิ่นแป้งเด็ก"), ("softener", "กลิ่นพรีเมียม"),
              ("softener", "กลิ่นธรรมชาติ")
            ]
            con.executemany("INSERT INTO products(category,name,created_at) VALUES(?,?,?)",
                            [(c,n,now) for c,n in products])
        con.execute("INSERT OR IGNORE INTO settings(key,value) VALUES('shop_name','BlueWash Laundry')")
        con.execute("INSERT OR IGNORE INTO settings(key,value) VALUES('currency','THB')")
        if con.execute("SELECT COUNT(*) FROM staff_users").fetchone()[0] == 0:
            con.execute("INSERT INTO staff_users(username,display_name,role) VALUES(?,?,?)",
                        ("demo-staff", "ผู้ดูแลระบบตัวอย่าง", "admin"))
        if con.execute("SELECT COUNT(*) FROM orders").fetchone()[0] == 0:
            demo = [
              ("BW-1001","คุณพิมพ์","wash_dry","สูตรมาตรฐาน","กลิ่นดอกไม้",3.2,256,"promptpay","pending_verification","awaiting_payment",""),
              ("BW-1002","คุณนนท์","wash","สูตรถนอมสีผ้า","ไม่ใช้",2.0,160,"cash","unpaid","washing",""),
              ("BW-1003","คุณเมย์","wash_iron","นำมาเอง","กลิ่นสะอาดสดชื่น",1.5,225,"promptpay","paid","ironing",""),
              ("BW-1004","คุณต้น","wash_dry","สูตรเข้มข้น","นำมาเอง",4.0,320,"cash","unpaid","ready","")
            ]
            for code,name,service,det,soft,weight,price,method,paystat,status,notes in demo:
                cur = con.execute("""INSERT INTO orders(order_code,customer_name,service,detergent,softener,weight,price,
                  payment_method,payment_status,status,notes,created_at,updated_at)
                  VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                  (code,name,service,det,soft,weight,price,method,paystat,status,notes,now,now))
                con.execute("INSERT INTO status_history(order_id,old_status,new_status,note,created_at) VALUES(?,?,?,?,?)",
                            (cur.lastrowid,None,status,"ข้อมูลตัวอย่างสำหรับทดสอบหน้าจอ",now))

init_db()

def line_api(path, payload=None, token=None):
    access_token = token or os.environ.get("LINE_CHANNEL_ACCESS_TOKEN", "")
    if not access_token:
        raise RuntimeError("LINE_CHANNEL_ACCESS_TOKEN is not configured")
    data = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request("https://api.line.me" + path, data=data, method="GET" if data is None else "POST",
        headers={"Authorization": "Bearer " + access_token, "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as response:
        raw = response.read()
    return json.loads(raw.decode("utf-8")) if raw else {}

def line_reply(reply_token, messages):
    return line_api("/v2/bot/message/reply", {"replyToken": reply_token, "messages": messages})

def line_push(user_id, messages):
    if user_id:
        return line_api("/v2/bot/message/push", {"to": user_id, "messages": messages})

def text_message(text):
    return {"type": "text", "text": str(text)[:5000]}

def flex_buttons(heading, description, actions):
    buttons = [{"type":"button", "style":"primary", "action":a, "height":"sm"} for a in actions]
    return {"type":"flex", "altText":heading[:400], "contents":{"type":"bubble", "size":"mega",
        "body":{"type":"box", "layout":"vertical", "spacing":"md", "contents":[
          {"type":"text", "text":heading, "weight":"bold", "size":"xl", "wrap":True},
          {"type":"text", "text":description, "size":"sm", "wrap":True, "color":"#52647A"}]},
        "footer":{"type":"box", "layout":"vertical", "spacing":"sm", "contents":buttons}}}

def order_status_flex(order):
    """Compact, color-coded order status card shared by pushes and status checks."""
    status = order["status"]
    label = ORDER_STATUSES.get(status, status)
    status_colors = {
        "pending_review": "#D99118", "awaiting_payment": "#D99118",
        "accepted": "#3679D8", "washing": "#3679D8",
        "drying": "#7958C8", "ironing": "#7958C8",
        "ready": "#27966E", "completed": "#68788B", "cancelled": "#C84C55",
    }
    service = SERVICES.get(order["service"], order["service"])
    weight = "รอชั่งน้ำหนัก" if order["weight"] is None else f"{float(order['weight']):g} กก."
    price = "รอกำหนดราคา" if order["price"] is None else f"฿{float(order['price']):,.2f}"
    try:
        updated = datetime.fromisoformat(order["updated_at"])
        minutes = max(0, int((datetime.now() - updated).total_seconds() // 60))
        if minutes < 1: ago = "เมื่อสักครู่"
        elif minutes < 60: ago = f"เมื่อ {minutes} นาทีที่แล้ว"
        elif minutes < 1440: ago = f"เมื่อ {minutes // 60} ชั่วโมงที่แล้ว"
        else: ago = f"เมื่อ {minutes // 1440} วันที่แล้ว"
    except (TypeError, ValueError):
        ago = "ไม่ทราบเวลา"

    def row(title, value, emphasize=False):
        return {"type":"box", "layout":"baseline", "spacing":"sm", "contents":[
            {"type":"text", "text":title, "size":"sm", "color":"#8292A5", "flex":2},
            {"type":"text", "text":str(value), "size":"sm", "weight":"bold" if emphasize else "regular",
             "color":"#243E5B", "flex":5, "wrap":True},
        ]}

    bubble = {
        "type":"bubble", "size":"mega",
        "header":{"type":"box", "layout":"vertical", "backgroundColor":status_colors.get(status,"#3679D8"),
                  "paddingAll":"16px", "contents":[
                      {"type":"text", "text":label, "size":"xl", "weight":"bold", "color":"#FFFFFF", "wrap":True},
                      {"type":"text", "text":"สถานะออเดอร์", "size":"xs", "color":"#FFFFFF", "margin":"sm"},
                  ]},
        "body":{"type":"box", "layout":"vertical", "spacing":"md", "paddingAll":"16px", "contents":[
            row("เลขออเดอร์", order["order_code"], True),
            {"type":"separator", "color":"#E8EDF3"},
            row("บริการ", service), row("น้ำหนัก", weight), row("ราคา", price, True),
            {"type":"text", "text":f"อัปเดตล่าสุด {ago}", "size":"xs", "color":"#91A0B2", "margin":"md", "wrap":True},
        ]},
    }
    if status == "ready" and order["line_user_id"] and order["id"] is not None:
        bubble["footer"] = {"type":"box", "layout":"vertical", "paddingAll":"14px", "contents":[
            {"type":"button", "style":"primary", "color":"#27966E", "height":"sm",
             "action":{"type":"postback", "label":"ยืนยันรับผ้าแล้ว",
                       "data":f"action=pickup_confirm&order_id={order['id']}",
                       "displayText":f"ยืนยันรับผ้าแล้ว ออเดอร์ {order['order_code']}"}},
        ]}
    return {"type":"flex", "altText":f"สถานะออเดอร์ {order['order_code']}: {label}"[:400], "contents":bubble}

def menu_message():
    liff_id = os.environ.get("LIFF_ID", "").strip()
    start_action = ({"type":"uri", "label":"เริ่มใช้บริการซักผ้า", "uri":f"https://liff.line.me/{liff_id}"}
                    if liff_id else {"type":"postback", "label":"เริ่มใช้บริการซักผ้า", "data":"action=start_order", "displayText":"เริ่มใช้บริการซักผ้า"})
    return flex_buttons("ซักทีซะบ้อ", "เลือกบริการที่ต้องการได้เลย", [
        start_action,
        {"type":"postback", "label":"ตรวจสอบสถานะออเดอร์", "data":"action=status", "displayText":"ตรวจสอบสถานะออเดอร์"},
        {"type":"postback", "label":"ชำระเงิน", "data":"action=payment", "displayText":"ชำระเงิน"},
        {"type":"postback", "label":"ติดต่อร้าน", "data":"action=contact", "displayText":"ติดต่อร้าน"},
    ])

def set_line_session(user_id, state, data=None):
    with db() as con:
        con.execute("INSERT INTO line_sessions(line_user_id,state,data,updated_at) VALUES(?,?,?,?) "
                     "ON CONFLICT(line_user_id) DO UPDATE SET state=excluded.state,data=excluded.data,updated_at=excluded.updated_at",
                     (user_id,state,json.dumps(data or {},ensure_ascii=False),now_iso()))

def get_line_session(user_id):
    with db() as con:
        row = con.execute("SELECT state,data FROM line_sessions WHERE line_user_id=?",(user_id,)).fetchone()
    return (row["state"],json.loads(row["data"] or "{}")) if row else ("",{})

def active_products(category):
    with db() as con:
        return [r[0] for r in con.execute("SELECT name FROM products WHERE category=? AND active=1 ORDER BY name",(category,))]

def create_line_order(user_id, data, display_name=None):
    now = now_iso()
    # LIFF supplies a server-verified profile name; chat orders fall back to the Messaging API profile.
    display_name = display_name or line_profile_name(user_id)
    if display_name == "ลูกค้า LINE" and data.get("customer_name"):
        display_name = data["customer_name"]
    with db() as con:
        con.execute("INSERT INTO line_customers(line_user_id,display_name,linked_at) VALUES(?,?,?) "
                    "ON CONFLICT(line_user_id) DO UPDATE SET display_name=excluded.display_name",
                    (user_id,display_name,now))
        cur = con.execute("""INSERT INTO orders(order_code,customer_name,line_user_id,service,detergent,softener,
          customer_photo,status,notes,created_at,updated_at) VALUES(?,?,?,?,?,?,?,'pending_review',?,?,?)""",
          (create_order_code(), display_name, user_id, data["service"],
           data["detergent"], data["softener"], data.get("photo", ""), "รับออเดอร์จาก LINE", now, now))
        order_id=cur.lastrowid
        con.execute("INSERT INTO status_history(order_id,old_status,new_status,note,changed_by,created_at) VALUES(?,?,?,?,?,?)",
                    (order_id,None,"pending_review","รับออเดอร์จาก LINE","LINE Chatbot",now))
        return con.execute("SELECT order_code FROM orders WHERE id=?",(order_id,)).fetchone()[0]

def status_notification(order):
    if not order["line_user_id"]: return
    try: line_push(order["line_user_id"],[order_status_flex(order)])
    except Exception: app.logger.exception("LINE notification failed for order %s",order["order_code"])

def price_notification(order):
    """Send the staff-confirmed weight and quote, then let the customer choose payment."""
    if not order["line_user_id"] or order["weight"] is None or order["price"] is None: return
    weight = f"{float(order['weight']):g}"
    msg = flex_buttons(
        f"ราคาออเดอร์ {order['order_code']}",
        f"ชั่งได้ {weight} กก.\nยอดชำระ ฿{float(order['price']):,.0f}\nกรุณาเลือกวิธีชำระเงิน",
        [
            {"type":"postback", "label":"เงินสด", "data":f"pay=cash&order_id={order['id']}", "displayText":"ชำระเงินสด"},
            {"type":"postback", "label":"PromptPay", "data":f"pay=promptpay&order_id={order['id']}", "displayText":"ชำระด้วย PromptPay"},
        ],
    )
    try: line_push(order["line_user_id"], [msg])
    except Exception: app.logger.exception("LINE price quote failed for order %s",order["order_code"])

def line_content(message_id):
    token=os.environ.get("LINE_CHANNEL_ACCESS_TOKEN","")
    if not token: raise RuntimeError("LINE_CHANNEL_ACCESS_TOKEN is not configured")
    req=urllib.request.Request("https://api-data.line.me/v2/bot/message/"+message_id+"/content",
        headers={"Authorization":"Bearer "+token})
    with urllib.request.urlopen(req,timeout=15) as response:
        content=response.read(8*1024*1024+1)
        mime=response.headers.get("Content-Type","").split(";")[0].lower()
    if len(content)>8*1024*1024: raise ValueError("image exceeds upload limit")
    ext={"image/jpeg":"jpg","image/png":"png","image/webp":"webp"}.get(mime)
    if not ext: raise ValueError("unsupported LINE image type")
    name=uuid.uuid4().hex+"."+ext
    (UPLOAD_DIR/name).write_bytes(content)
    return name

def line_profile_name(user_id):
    token=os.environ.get("LINE_CHANNEL_ACCESS_TOKEN","")
    if not token:return "ลูกค้า LINE"
    req=urllib.request.Request("https://api.line.me/v2/bot/profile/"+user_id,headers={"Authorization":"Bearer "+token})
    try:
        with urllib.request.urlopen(req,timeout=5) as response:
            return json.loads(response.read().decode("utf-8")).get("displayName") or "ลูกค้า LINE"
    except Exception:
        return "ลูกค้า LINE"

def verify_liff_id_token(id_token):
    channel_id = os.environ.get("LINE_LOGIN_CHANNEL_ID", "").strip()
    if not channel_id:
        raise RuntimeError("LINE_LOGIN_CHANNEL_ID is not configured")
    from urllib.parse import urlencode
    payload = urlencode({"id_token": id_token, "client_id": channel_id}).encode("utf-8")
    req = urllib.request.Request("https://api.line.me/oauth2/v2.1/verify", data=payload,
        headers={"Content-Type": "application/x-www-form-urlencoded"}, method="POST")
    with urllib.request.urlopen(req, timeout=8) as response:
        profile = json.loads(response.read().decode("utf-8"))
    if profile.get("aud") != channel_id or not profile.get("sub"):
        raise ValueError("invalid LINE identity token")
    return profile

@app.route("/liff/order")
def liff_order():
    return render_template("liff_order.html", liff_id=os.environ.get("LIFF_ID", "").strip())

@app.route("/api/liff/products")
def liff_products():
    return jsonify({
        "services": SERVICES,
        "detergents": active_products("detergent"),
        "softeners": active_products("softener"),
    })

@app.route("/api/liff/orders", methods=["POST"])
def liff_create_order():
    id_token = request.form.get("id_token", "").strip()
    if not id_token:
        return jsonify({"error": "กรุณาเปิดหน้านี้จาก LINE แล้วลองใหม่"}), 401
    try:
        profile = verify_liff_id_token(id_token)
    except RuntimeError:
        return jsonify({"error": "ร้านยังตั้งค่า LINE Login channel ไม่ครบ"}), 503
    except Exception:
        return jsonify({"error": "ยืนยันบัญชี LINE ไม่สำเร็จ กรุณาปิดแล้วเปิด LIFF ใหม่"}), 401

    service = request.form.get("service", "")
    detergent = request.form.get("detergent", "")
    softener = request.form.get("softener", "")
    if service not in SERVICES:
        return jsonify({"error": "กรุณาเลือกบริการที่ร้านเปิดให้บริการ"}), 400
    if detergent != "bring" and detergent not in active_products("detergent"):
        return jsonify({"error": "กรุณาเลือกน้ำยาซักผ้าจากรายการที่เปิดใช้งาน หรือนำมาเอง"}), 400
    if softener not in ("bring", "none") and softener not in active_products("softener"):
        return jsonify({"error": "กรุณาเลือกน้ำยาปรับผ้านุ่มจากรายการที่เปิดใช้งาน หรือนำมาเอง/ไม่ใช้"}), 400
    if not request.files.get("customer_photo") or not request.files["customer_photo"].filename:
        return jsonify({"error": "กรุณาแนบรูปตะกร้าหรือถุงผ้าก่อนส่งออเดอร์"}), 400
    photo = save_upload("customer_photo")
    if not photo:
        return jsonify({"error": "รองรับรูป JPG, PNG หรือ WEBP เท่านั้น"}), 400
    data = {
        "service": service,
        "detergent": "นำมาเอง" if detergent == "bring" else detergent,
        "softener": {"bring": "นำมาเอง", "none": "ไม่ใช้"}.get(softener, softener),
        "photo": photo,
    }
    try:
        order_code = create_line_order(profile["sub"], data, profile.get("name"))
    except Exception:
        (UPLOAD_DIR / photo).unlink(missing_ok=True)
        app.logger.exception("Unable to create LIFF order")
        return jsonify({"error": "บันทึกออเดอร์ไม่สำเร็จ กรุณาลองอีกครั้ง"}), 500
    return jsonify({"order_code": order_code, "status": "pending_review"}), 201

def choices_message(title, prompt, options, key):
    actions=[{"type":"postback","label":str(label)[:20],"data":f"{key}={value}","displayText":str(label)}
             for value,label in options[:10]]
    return flex_buttons(title,prompt,actions)

def latest_line_order(user_id):
    with db() as con:
        return con.execute("SELECT * FROM orders WHERE line_user_id=? AND status!='completed' AND status!='cancelled' ORDER BY id DESC LIMIT 1",(user_id,)).fetchone()

def most_recent_line_order(user_id):
    with db() as con:
        return con.execute("SELECT * FROM orders WHERE line_user_id=? ORDER BY id DESC LIMIT 1",(user_id,)).fetchone()

def liff_entry_message():
    liff_id = os.environ.get("LIFF_ID", "").strip()
    if not liff_id:
        return text_message("หน้าสั่งซักยังอยู่ระหว่างตั้งค่า LIFF กรุณาติดต่อร้านชั่วคราวค่ะ")
    return flex_buttons("เริ่มใช้บริการซักผ้า", "แตะปุ่มเพื่อเลือกบริการและน้ำยาในหน้าแบบฟอร์ม", [
        {"type":"uri", "label":"เปิดหน้าสั่งซัก", "uri":f"https://liff.line.me/{liff_id}"}
    ])

def handle_line_event(event):
    source=event.get("source",{})
    user_id=source.get("userId")
    if not user_id or not event.get("replyToken"): return
    reply=event["replyToken"]
    if event.get("type")=="follow":
        line_reply(reply,[menu_message()]); return
    if event.get("type")=="postback":
        from urllib.parse import parse_qs
        data=parse_qs(event.get("postback",{}).get("data",""))
        action=next(iter(data),"")
        value=data.get(action,[""])[0]
        if action=="action" and value=="start_order":
            line_reply(reply,[liff_entry_message()]); return
        if action=="action" and value=="status":
            order=most_recent_line_order(user_id)
            line_reply(reply,[order_status_flex(order) if order else text_message("ยังไม่พบออเดอร์ที่ผูกกับบัญชี LINE นี้ค่ะ")]); return
        if action=="action" and value=="pickup_confirm":
            order_id=data.get("order_id",[""])[0]
            try: order_id=int(order_id)
            except (TypeError,ValueError): order_id=0
            with db() as con:
                order=con.execute("SELECT * FROM orders WHERE id=? AND line_user_id=?",(order_id,user_id)).fetchone()
                if order and order["status"]=="ready":
                    now=now_iso()
                    changed=con.execute("UPDATE orders SET status='completed',updated_at=? WHERE id=? AND line_user_id=? AND status='ready'",
                                        (now,order_id,user_id)).rowcount
                    if changed:
                        con.execute("INSERT INTO status_history(order_id,old_status,new_status,note,changed_by,created_at) VALUES(?,?,?,?,?,?)",
                                    (order_id,"ready","completed","ลูกค้ายืนยันรับผ้าผ่าน LINE","ลูกค้า LINE",now))
                    order=con.execute("SELECT * FROM orders WHERE id=?",(order_id,)).fetchone()
                elif order and order["status"]=="completed":
                    pass
                else:
                    order=None
            if not order:
                line_reply(reply,[text_message("ไม่พบออเดอร์นี้ หรือออเดอร์ยังไม่อยู่ในสถานะพร้อมรับค่ะ")]); return
            line_reply(reply,[order_status_flex(order)]); return
        if action=="action" and value=="payment":
            order=latest_line_order(user_id)
            if not order: line_reply(reply,[text_message("ยังไม่พบออเดอร์ที่กำลังดำเนินการค่ะ")]); return
            if order["price"] is None: line_reply(reply,[text_message(f"ออเดอร์ {order['order_code']} ยังรอพนักงานตรวจผ้าและกำหนดราคาค่ะ")]); return
            set_line_session(user_id,"payment_choice",{"order_id":order["id"]})
            line_reply(reply,[choices_message(f"ชำระออเดอร์ {order['order_code']}",f"ยอด ฿{float(order['price']):,.2f} เลือกวิธีชำระเงิน",[("cash","เงินสดที่ร้าน"),("promptpay","PromptPay")],"pay")]); return
        if action=="action" and value=="contact":
            contact=os.environ.get("SHOP_CONTACT_TEXT","กรุณาติดต่อร้านโดยตรงได้ที่หน้าร้าน")
            line_reply(reply,[text_message(contact)]); return
        state,form=get_line_session(user_id)
        if action=="service" and state=="choose_service" and value in SERVICES:
            form["service"]=value
            products=active_products("detergent")
            set_line_session(user_id,"choose_detergent",form)
            line_reply(reply,[choices_message("เลือกน้ำยาซักผ้า","เลือกหนึ่งรายการ หรือเลือกนำมาเอง",[(x,x) for x in products]+[("bring","นำมาเอง")],"detergent")]); return
        if action=="detergent" and state=="choose_detergent":
            products=active_products("detergent")
            if value!="bring" and value not in products:
                line_reply(reply,[text_message("ตัวเลือกนี้ไม่พร้อมให้บริการแล้ว กรุณาเริ่มใหม่ค่ะ")]);set_line_session(user_id,"",{});return
            form["detergent"]="นำมาเอง" if value=="bring" else value
            softeners=active_products("softener")
            set_line_session(user_id,"choose_softener",form)
            line_reply(reply,[choices_message("เลือกน้ำยาปรับผ้านุ่ม","เลือกหนึ่งรายการ หรือเลือกนำมาเอง/ไม่ใช้",[(x,x) for x in softeners]+[("bring","นำมาเอง"),("none","ไม่ใช้")],"softener")]);return
        if action=="softener" and state=="choose_softener":
            products=active_products("softener")
            if value not in products and value not in ("bring","none"):
                line_reply(reply,[text_message("ตัวเลือกนี้ไม่พร้อมให้บริการแล้ว กรุณาเริ่มใหม่ค่ะ")]);set_line_session(user_id,"",{});return
            form["softener"]={"bring":"นำมาเอง","none":"ไม่ใช้"}.get(value,value)
            set_line_session(user_id,"confirm_order",form)
            summary=f"บริการ: {SERVICES[form['service']]}\nน้ำยาซักผ้า: {form['detergent']}\nน้ำยาปรับผ้านุ่ม: {form['softener']}\nรูปถุงผ้า: แนบแล้ว\n\nยืนยันส่งออเดอร์หรือไม่? พนักงานจะตรวจผ้า ชั่งน้ำหนัก และแจ้งราคาจริงภายหลังค่ะ"
            line_reply(reply,[choices_message("สรุปออเดอร์",summary,[("yes","ยืนยันออเดอร์"),("no","ยกเลิก")],"confirm")]);return
        if action=="confirm" and state=="confirm_order":
            if value!="yes": set_line_session(user_id,"",{});line_reply(reply,[text_message("ยกเลิกการทำรายการแล้วค่ะ"),menu_message()]);return
            code=create_line_order(user_id,form)
            set_line_session(user_id,"",{})
            line_reply(reply,[text_message(f"รับออเดอร์ {code} แล้วค่ะ พนักงานจะตรวจผ้า ชั่งน้ำหนัก และกำหนดราคาจริง จากนั้นจะแจ้งราคาและสถานะผ่าน LINE นี้ค่ะ")]);return
        if action=="pay" and (state=="payment_choice" or "order_id" in data):
            order_id=data.get("order_id",[form.get("order_id")])[0]
            if value not in ("cash", "promptpay"):
                line_reply(reply,[text_message("กรุณาเลือกเงินสดหรือ PromptPay จากปุ่มในแชตค่ะ")]);return
            with db() as con:
                order=con.execute("SELECT * FROM orders WHERE id=? AND line_user_id=?",(order_id,user_id)).fetchone()
                if order:
                    next_status = "accepted" if value=="cash" else "awaiting_payment"
                    con.execute("UPDATE orders SET payment_method=?,payment_status='unpaid',status=CASE WHEN status IN ('pending_review','awaiting_payment') THEN ? ELSE status END,updated_at=? WHERE id=?",
                                (value,next_status,now_iso(),order_id))
            set_line_session(user_id,"",{})
            if not order: line_reply(reply,[text_message("ไม่พบออเดอร์ กรุณาตรวจสอบสถานะอีกครั้งค่ะ")]);return
            if value=="cash": line_reply(reply,[text_message(f"บันทึกชำระเงินสดที่ร้านสำหรับ {order['order_code']} แล้วค่ะ กรุณาชำระเมื่อมารับผ้า")])
            else:
                set_line_session(user_id,"wait_slip",{"order_id":order_id})
                instructions=os.environ.get("PROMPTPAY_INSTRUCTIONS","กรุณาโอนผ่าน PromptPay ตามช่องทางที่ร้านแจ้ง")
                line_reply(reply,[text_message(f"ยอดชำระ ฿{float(order['price']):,.2f}\n{instructions}\nหลังโอนแล้วส่งรูปสลิปในแชตนี้ได้เลยค่ะ พนักงานจะตรวจสอบก่อนยืนยัน")])
            return
        if action=="action" and value=="menu": line_reply(reply,[menu_message()]);return
    if event.get("type")!="message": return
    msg=event.get("message",{})
    if msg.get("type")=="text":
        text=msg.get("text","").strip()
        if text.lower() in ("เมนู","menu","เริ่ม","สวัสดี"):
            line_reply(reply,[menu_message()]);return
        if text in ("เริ่มใช้บริการซักผ้า", "เริ่มใช้บริการ", "สั่งซัก"):
            line_reply(reply,[liff_entry_message()]);return
        if text in ("ตรวจสอบสถานะออเดอร์", "ตรวจสอบสถานะ", "สถานะออเดอร์", "สถานะ"):
            order=most_recent_line_order(user_id)
            line_reply(reply,[order_status_flex(order) if order else text_message("ยังไม่พบออเดอร์ที่ผูกกับบัญชี LINE นี้ค่ะ")]);return
        if text in ("ชำระเงิน", "วิธีชำระเงิน"):
            order=latest_line_order(user_id)
            if not order:
                line_reply(reply,[text_message("ยังไม่พบออเดอร์ที่กำลังดำเนินการค่ะ")]);return
            if order["price"] is None:
                line_reply(reply,[text_message(f"ออเดอร์ {order['order_code']} ยังรอพนักงานตรวจผ้าและกำหนดราคาค่ะ")]);return
            set_line_session(user_id,"payment_choice",{"order_id":order["id"]})
            line_reply(reply,[choices_message(f"ชำระออเดอร์ {order['order_code']}",f"ยอด ฿{float(order['price']):,.2f} เลือกวิธีชำระเงิน",[("cash","เงินสดที่ร้าน"),("promptpay","PromptPay")],"pay")]);return
        if text in ("ติดต่อร้าน", "ติดต่อ"):
            line_reply(reply,[text_message(os.environ.get("SHOP_CONTACT_TEXT","กรุณาติดต่อร้านโดยตรงได้ที่หน้าร้าน"))]);return
        if text.startswith("ผูก "):
            code=text.split(maxsplit=1)[1].strip().upper()
            display_name=line_profile_name(user_id)
            with db() as con:
                row=con.execute("SELECT l.order_id,o.line_user_id FROM line_link_codes l JOIN orders o ON o.id=l.order_id WHERE l.code=? AND l.used_at IS NULL AND l.expires_at>?",(code,now_iso())).fetchone()
                if row and (not row["line_user_id"] or row["line_user_id"]==user_id):
                    con.execute("UPDATE orders SET line_user_id=?,customer_name=CASE WHEN ?='ลูกค้า LINE' THEN customer_name ELSE ? END,updated_at=? WHERE id=?",
                                (user_id,display_name,display_name,now_iso(),row["order_id"]))
                    con.execute("UPDATE line_link_codes SET used_at=? WHERE code=?",(now_iso(),code))
                    con.execute("INSERT INTO line_customers(line_user_id,display_name,linked_at) VALUES(?,?,?) ON CONFLICT(line_user_id) DO UPDATE SET display_name=excluded.display_name,linked_at=excluded.linked_at",(user_id,display_name,now_iso()))
                    linked=True
                else: linked=False
            line_reply(reply,[text_message("เชื่อมออเดอร์เรียบร้อยแล้วค่ะ ต่อไปจะได้รับแจ้งสถานะผ่าน LINE" if linked else "รหัสไม่ถูกต้อง หมดอายุ หรือออเดอร์นี้เชื่อมกับบัญชีอื่นแล้วค่ะ")]);return
    state,form=get_line_session(user_id)
    if msg.get("type")=="image":
        try: filename=line_content(msg["id"])
        except Exception:
            app.logger.exception("Unable to download LINE image")
            line_reply(reply,[text_message("รับรูปไม่สำเร็จ กรุณาลองส่งใหม่เป็น JPG หรือ PNG ค่ะ")]);return
        if state=="need_photo":
            form["photo"]=filename
            set_line_session(user_id,"choose_service",form)
            line_reply(reply,[choices_message("เลือกบริการ","เลือกบริการได้หนึ่งรายการ",[(k,v) for k,v in SERVICES.items()],"service")]);return
        if state=="wait_slip":
            with db() as con:
                order=con.execute("SELECT id,order_code FROM orders WHERE id=? AND line_user_id=? AND payment_method='promptpay'",(form.get("order_id"),user_id)).fetchone()
                if order:
                    con.execute("UPDATE orders SET payment_slip=?,payment_status='pending_verification',updated_at=? WHERE id=?",(filename,now_iso(),order["id"]))
            if order:
                set_line_session(user_id,"",{})
                line_reply(reply,[text_message(f"รับสลิปของออเดอร์ {order['order_code']} แล้วค่ะ สถานะยังรอตรวจสอบ พนักงานจะยืนยันหลังตรวจยอดเงินจริง")]);return
        line_reply(reply,[text_message("ได้รับรูปแล้วค่ะ หากต้องการส่งสลิป กรุณาเลือกเมนูชำระเงินก่อน")]);return
    if msg.get("type")=="text" and state=="need_photo":
        line_reply(reply,[text_message("กรุณาส่งรูปตะกร้าหรือถุงผ้าก่อนเลือกบริการค่ะ")]);return
    if msg.get("type")=="text" and state=="confirm_order":
        line_reply(reply,[text_message("กดยืนยันหรือยกเลิกจากปุ่มด้านล่างนะคะ")]);return
    line_reply(reply,[menu_message()])

@app.route("/webhook/line", methods=["POST"])
def line_webhook():
    body=request.get_data(cache=False)
    signature=request.headers.get("X-Line-Signature","")
    secret=os.environ.get("LINE_CHANNEL_SECRET","")
    if not secret: return jsonify({"error":"LINE_CHANNEL_SECRET is not configured"}),503
    expected=base64.b64encode(hmac.new(secret.encode("utf-8"),body,hashlib.sha256).digest()).decode("ascii")
    if not hmac.compare_digest(signature,expected): return jsonify({"error":"invalid signature"}),401
    try: payload=json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError,json.JSONDecodeError): return jsonify({"error":"invalid JSON"}),400
    for event in payload.get("events",[]):
        event_id=event.get("webhookEventId") or hashlib.sha256(json.dumps(event,sort_keys=True,ensure_ascii=False).encode("utf-8")).hexdigest()
        with db() as con:
            cur=con.execute("INSERT OR IGNORE INTO line_events(event_id,processed_at) VALUES(?,?)",(event_id,now_iso()))
            fresh=cur.rowcount==1
        if fresh:
            try: handle_line_event(event)
            except Exception: app.logger.exception("LINE event failed: %s",event_id)
    return jsonify({"ok":True})

def money(value):
    if value is None: return "—"
    return f"฿{float(value):,.2f}".replace(".00","")
app.jinja_env.filters["money"] = money

def statuses_for_service(service):
    allowed={"pending_review","awaiting_payment","accepted","washing","ready","completed","cancelled"}
    if service=="wash_dry": allowed.add("drying")
    if service=="wash_iron": allowed.add("ironing")
    return {key:value for key,value in ORDER_STATUSES.items() if key in allowed}

def order_query(where="", params=()):
    with db() as con:
        return con.execute("SELECT * FROM orders " + where + " ORDER BY created_at DESC, id DESC", params).fetchall()

@app.route("/")
def dashboard():
    with db() as con:
        total = con.execute("SELECT COUNT(*) FROM orders WHERE status != 'cancelled'").fetchone()[0]
        waiting = con.execute("SELECT COUNT(*) FROM orders WHERE status IN ('pending_review','awaiting_payment')").fetchone()[0]
        processing = con.execute("SELECT COUNT(*) FROM orders WHERE status IN ('accepted','washing','drying','ironing')").fetchone()[0]
        ready = con.execute("SELECT COUNT(*) FROM orders WHERE status='ready'").fetchone()[0]
        revenue = con.execute("SELECT COALESCE(SUM(price),0) FROM orders WHERE payment_status='paid'").fetchone()[0]
        recent = con.execute("SELECT * FROM orders ORDER BY created_at DESC, id DESC LIMIT 6").fetchall()
        pending_payments = con.execute("SELECT COUNT(*) FROM orders WHERE payment_status='pending_verification'").fetchone()[0]
    return render_template("dashboard.html", total=total, waiting=waiting, processing=processing,
                           ready=ready, revenue=revenue, recent=recent, pending_payments=pending_payments,
                           statuses=ORDER_STATUSES, payments=PAYMENT_STATUSES)

@app.route("/orders")
def orders():
    q = request.args.get("q","").strip()
    status = request.args.get("status","")
    payment = request.args.get("payment","")
    clauses, params = [], []
    if q:
        clauses.append("(order_code LIKE ? OR customer_name LIKE ? OR phone LIKE ?)")
        params += [f"%{q}%"]*3
    if status in ORDER_STATUSES:
        clauses.append("status=?"); params.append(status)
    if payment in PAYMENT_STATUSES:
        clauses.append("payment_status=?"); params.append(payment)
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    rows = order_query(where, params)
    return render_template("orders.html", orders=rows, statuses=ORDER_STATUSES, payments=PAYMENT_STATUSES,
                           services=SERVICES, q=q, selected_status=status, selected_payment=payment)

@app.route("/orders/new", methods=["GET","POST"])
def new_order():
    with db() as con:
        detergents = con.execute("SELECT * FROM products WHERE category='detergent' AND active=1 ORDER BY name").fetchall()
        softeners = con.execute("SELECT * FROM products WHERE category='softener' AND active=1 ORDER BY name").fetchall()
    if request.method == "POST":
        customer = request.form.get("customer_name","").strip()
        service = request.form.get("service","")
        if not customer or service not in SERVICES:
            flash("กรุณากรอกชื่อลูกค้าและเลือกบริการให้ถูกต้อง", "error")
            return render_template("order_form.html", order=None, services=SERVICES, detergents=detergents, softeners=softeners)
        now = datetime.now().isoformat(timespec="seconds")
        code = create_order_code()
        photo = save_upload("customer_photo")
        with db() as con:
            cur = con.execute("""INSERT INTO orders(order_code,customer_name,line_user_id,phone,service,detergent,softener,
                customer_photo,notes,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                (code,customer,request.form.get("line_user_id","").strip(),request.form.get("phone","").strip(),
                 service,request.form.get("detergent","ร้านจัดให้"),request.form.get("softener","ไม่ใช้"),
                 photo,request.form.get("notes","").strip(),now,now))
            con.execute("INSERT INTO status_history(order_id,old_status,new_status,note,created_at) VALUES(?,?,?,?,?)",
                        (cur.lastrowid,None,"pending_review","สร้างออเดอร์จาก Dashboard",now))
        flash(f"สร้างออเดอร์ {code} แล้ว", "success")
        return redirect(url_for("order_detail", order_id=cur.lastrowid))
    return render_template("order_form.html", order=None, services=SERVICES, detergents=detergents, softeners=softeners)

@app.route("/orders/<int:order_id>")
def order_detail(order_id):
    with db() as con:
        order = con.execute("SELECT * FROM orders WHERE id=?", (order_id,)).fetchone()
        if not order: abort(404)
        history = con.execute("SELECT * FROM status_history WHERE order_id=? ORDER BY id DESC", (order_id,)).fetchall()
        link_code=con.execute("SELECT code,expires_at FROM line_link_codes WHERE order_id=? AND used_at IS NULL AND expires_at>? ORDER BY expires_at DESC LIMIT 1",(order_id,now_iso())).fetchone()
    return render_template("order_detail.html", order=order, history=history, link_code=link_code, statuses=statuses_for_service(order["service"]),
                           payments=PAYMENT_STATUSES, services=SERVICES)

@app.route("/orders/<int:order_id>/line-link",methods=["POST"])
def create_line_link(order_id):
    with db() as con:
        order=con.execute("SELECT id,line_user_id FROM orders WHERE id=?",(order_id,)).fetchone()
        if not order:abort(404)
        if order["line_user_id"]:
            flash("ออเดอร์นี้เชื่อมกับ LINE แล้ว", "error")
        else:
            code=uuid.uuid4().hex[:10].upper()
            from datetime import timedelta
            expires_at=(datetime.now().replace(microsecond=0)+timedelta(minutes=15)).isoformat(timespec="seconds")
            con.execute("INSERT INTO line_link_codes(code,order_id,expires_at) VALUES(?,?,?)",(code,order_id,expires_at))
            flash("สร้างรหัสเชื่อมแล้ว (หมดอายุใน 15 นาที)","success")
    return redirect(url_for("order_detail",order_id=order_id))

@app.route("/orders/<int:order_id>/update", methods=["POST"])
def update_order(order_id):
    with db() as con:
        order = con.execute("SELECT * FROM orders WHERE id=?", (order_id,)).fetchone()
        if not order: abort(404)
        old_status = order["status"]
        old_price = order["price"]
        old_weight = order["weight"]
        weight_raw = request.form.get("weight","").strip()
        price_raw = request.form.get("price","").strip()
        weight = float(weight_raw) if weight_raw else None
        if weight is not None and weight.is_integer() and 1 <= int(weight) <= 6:
            price = 80 + (int(weight) - 1) * 60
        else:
            price = float(price_raw) if price_raw else None
        status = request.form.get("status", old_status)
        if status not in statuses_for_service(order["service"]): status = old_status
        method = request.form.get("payment_method", order["payment_method"])
        if method not in ("", "cash", "promptpay"): method = order["payment_method"]
        payment_status = request.form.get("payment_status", order["payment_status"])
        if payment_status not in PAYMENT_STATUSES: payment_status = order["payment_status"]
        now = datetime.now().isoformat(timespec="seconds")
        con.execute("""UPDATE orders SET weight=?,price=?,status=?,payment_method=?,payment_status=?,
            notes=?,updated_at=? WHERE id=?""",
            (weight,price,status,method,payment_status,request.form.get("notes","").strip(),now,order_id))
        if status != old_status:
            con.execute("INSERT INTO status_history(order_id,old_status,new_status,note,created_at) VALUES(?,?,?,?,?)",
                        (order_id,old_status,status,request.form.get("history_note","").strip(),now))
        weight_changed = weight != old_weight
        updated_order = con.execute("SELECT * FROM orders WHERE id=?",(order_id,)).fetchone()
    if price is not None and weight is not None and (price != old_price or weight_changed):
        price_notification(updated_order)
    if status != old_status:
        status_notification(updated_order)
    flash("บันทึกการเปลี่ยนแปลงแล้ว", "success")
    return redirect(url_for("order_detail", order_id=order_id))

@app.route("/orders/<int:order_id>/delete", methods=["POST"])
def delete_order(order_id):
    with db() as con:
        con.execute("DELETE FROM orders WHERE id=?", (order_id,))
    flash("ลบออเดอร์แล้ว", "success")
    return redirect(url_for("orders"))

def save_upload(field):
    file = request.files.get(field)
    if not file or not file.filename: return ""
    ext = file.filename.rsplit(".",1)[-1].lower() if "." in file.filename else ""
    if ext not in ALLOWED_EXTENSIONS: return ""
    name = f"{uuid.uuid4().hex}.{ext}"
    file.save(UPLOAD_DIR / secure_filename(name))
    return name

@app.route("/uploads/<path:filename>")
def uploaded_file(filename):
    # Prototype only: production should require staff authentication/authorization.
    from flask import send_from_directory
    return send_from_directory(UPLOAD_DIR, filename)

@app.route("/products", methods=["GET","POST"])
def products():
    if request.method == "POST":
        category = request.form.get("category")
        name = request.form.get("name","").strip()
        if category not in ("detergent","softener") or not name:
            flash("กรุณาระบุประเภทและชื่อผลิตภัณฑ์", "error")
        else:
            with db() as con:
                con.execute("INSERT INTO products(category,name,created_at) VALUES(?,?,?)",
                            (category,name,datetime.now().isoformat(timespec="seconds")))
            flash("เพิ่มตัวเลือกแล้ว", "success")
        return redirect(url_for("products"))
    with db() as con:
        rows = con.execute("SELECT * FROM products ORDER BY category,name").fetchall()
    return render_template("products.html", products=rows)

@app.route("/products/<int:product_id>/toggle", methods=["POST"])
def toggle_product(product_id):
    with db() as con:
        con.execute("UPDATE products SET active=CASE active WHEN 1 THEN 0 ELSE 1 END WHERE id=?", (product_id,))
    flash("อัปเดตสถานะตัวเลือกแล้ว", "success")
    return redirect(url_for("products"))

@app.route("/api/orders", methods=["GET","POST"])
def api_orders():
    # JSON API skeleton for the separate LINE integration.
    if request.method == "GET":
        rows = order_query()
        return jsonify([dict(r) for r in rows])
    payload = request.get_json(silent=True) or {}
    required = ("customer_name","service")
    if not all(payload.get(k) for k in required) or payload["service"] not in SERVICES:
        return jsonify({"error":"customer_name and a valid service are required"}), 400
    now = datetime.now().isoformat(timespec="seconds")
    code = create_order_code()
    with db() as con:
        cur = con.execute("""INSERT INTO orders(order_code,customer_name,line_user_id,service,detergent,softener,
            created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)""",
            (code,payload["customer_name"],payload.get("line_user_id",""),payload["service"],
             payload.get("detergent","ร้านจัดให้"),payload.get("softener","ไม่ใช้"),now,now))
        con.execute("INSERT INTO status_history(order_id,old_status,new_status,note,created_at) VALUES(?,?,?,?,?)",
                    (cur.lastrowid,None,"pending_review","สร้างผ่าน API",now))
        order_id = cur.lastrowid
    return jsonify({"id":order_id,"order_code":code,"status":"pending_review"}), 201

@app.route("/api/health")
def health():
    return jsonify({"ok":True,"service":"laundry-dashboard-api","time":datetime.now().isoformat(timespec="seconds")})

@app.route("/api/orders/<int:order_id>")
def api_order_detail(order_id):
    with db() as con:
        row = con.execute("SELECT * FROM orders WHERE id=?", (order_id,)).fetchone()
    if not row: return jsonify({"error":"order not found"}), 404
    return jsonify(dict(row))

if __name__ == "__main__":
    app.run(debug=True, host="127.0.0.1", port=5000)
