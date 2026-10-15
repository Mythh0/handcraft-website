
import hashlib
import hmac
import json
import os
import sqlite3

import requests
from flask import Flask, g, jsonify, render_template, request, Response

app = Flask(__name__)

DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "shop.db")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "changeme")
GIFT_WRAP = 99

# Razorpay configuration
RZP_KEY_ID = os.environ.get("RAZORPAY_KEY_ID", "")
RZP_KEY_SECRET = os.environ.get("RAZORPAY_KEY_SECRET", "")
RZP_WEBHOOK_SECRET = os.environ.get("RAZORPAY_WEBHOOK_SECRET", "")
RZP_API = "https://api.razorpay.com/v1"
ONLINE_ENABLED = bool(RZP_KEY_ID and RZP_KEY_SECRET)
PENDING = "Pending payment"

from products_data import PRODUCTS


def db():
    if "db" not in g:
        g.db = sqlite3.connect(DB)
        g.db.row_factory = sqlite3.Row
    return g.db


@app.teardown_appcontext
def close_db(_):
    d = g.pop("db", None)
    if d:
        d.close()


def init_db():
    c = sqlite3.connect(DB)

    cols = [r[1] for r in c.execute("PRAGMA table_info(products)")]
    if cols and "description" not in cols:
        c.execute("DROP TABLE products")

    c.executescript("""
    CREATE TABLE IF NOT EXISTS products(
        id INTEGER PRIMARY KEY,
        name TEXT,
        category TEXT,
        price INTEGER,
        blurb TEXT,
        image TEXT,
        description TEXT,
        details TEXT
    );

    CREATE TABLE IF NOT EXISTS orders(
        id INTEGER PRIMARY KEY,
        name TEXT,
        phone TEXT,
        address TEXT,
        note TEXT,
        gift_wrap INTEGER,
        total INTEGER,
        status TEXT DEFAULT 'New',
        created TEXT DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS order_items(
        order_id INTEGER,
        product_id INTEGER,
        name TEXT,
        qty INTEGER,
        price INTEGER
    );
    """)

    ocols = [r[1] for r in c.execute("PRAGMA table_info(orders)")]

    for col, ddl in (
        ("payment_method", "TEXT DEFAULT 'cod'"),
        ("payment_status", "TEXT DEFAULT 'COD'"),
        ("razorpay_order_id", "TEXT"),
        ("razorpay_payment_id", "TEXT"),
    ):
        if col not in ocols:
            c.execute(f"ALTER TABLE orders ADD COLUMN {col} {ddl}")

    # Insert products only when the products table is empty.
    if not c.execute("SELECT 1 FROM products").fetchone():
        c.executemany(
            "INSERT INTO products(name, category, price, blurb, image, description, details) "
            "VALUES(?,?,?,?,?,?,?)",
            PRODUCTS
        )

    # Update existing product image filenames without deleting orders.
    image_updates = {
        "Beeswax Pillar Candle": "candle.webp",
        "Lavender Soy Jar": "lavender.webp",
        "Glazed Tea Cup Pair": "cups.webp",
        "Trinket Dish": "dish.webp",
        "Brass Leaf Earrings": "earrings.webp",
        "Beaded Anklet": "anklet.jpg",
        "Linen Bound Journal": "journal.webp",
        "Pressed Flower Bookmark": "bookmark.webp",
        "The Little Joy Hamper": "hamper_small.jpg",
        "The Celebration Hamper": "hamper_big.jpg",
    }

    for name, image in image_updates.items():
        c.execute(
            "UPDATE products SET image = ? WHERE name = ?",
            (image, name)
        )

    c.commit()
    c.close()


@app.get("/")
def home():
    return render_template(
        "index.html",
        gift_wrap=GIFT_WRAP,
        online=ONLINE_ENABLED
    )


@app.get("/api/products")
def products():
    rows = db().execute("SELECT * FROM products ORDER BY id").fetchall()
    return jsonify([dict(r) for r in rows])


@app.post("/api/orders")
def create_order():
    d = request.get_json(silent=True) or {}

    name, phone, address = (
        str(d.get(k, "")).strip()
        for k in ("name", "phone", "address")
    )

    items = d.get("items") or []

    if len(name) < 2 or len(phone) < 7 or len(address) < 8:
        return jsonify(
            error="Enter your name, phone number and full address."
        ), 400

    if not items:
        return jsonify(error="Your basket is empty."), 400

    total, lines = 0, []

    for it in items:
        try:
            pid = int(it["id"])
            qty = max(1, min(int(it["qty"]), 20))
        except (KeyError, ValueError, TypeError):
            return jsonify(error="Invalid basket."), 400

        p = db().execute(
            "SELECT * FROM products WHERE id = ?", (pid,)
        ).fetchone()

        if not p:
            return jsonify(
                error="A product in your basket is no longer available."
            ), 400

        total += p["price"] * qty
        lines.append((p["id"], p["name"], qty, p["price"]))

    wrap = 1 if d.get("gift_wrap") else 0
    total += GIFT_WRAP * wrap

    online = d.get("payment_method") == "online"

    if online and not ONLINE_ENABLED:
        return jsonify(
            error="Online payment isn't available right now. Choose cash on delivery."
        ), 400

    cur = db().execute(
        "INSERT INTO orders(name, phone, address, note, gift_wrap, total, status, "
        "payment_method, payment_status) VALUES(?,?,?,?,?,?,?,?,?)",
        (
            name,
            phone,
            address,
            str(d.get("note", ""))[:300],
            wrap,
            total,
            PENDING if online else "New",
            "online" if online else "cod",
            "Pending" if online else "COD",
        )
    )

    oid = cur.lastrowid

    db().executemany(
        "INSERT INTO order_items VALUES(?,?,?,?,?)",
        [(oid, *line) for line in lines]
    )

    if not online:
        db().commit()
        return jsonify(order_id=oid, total=total), 201

    try:
        r = requests.post(
            f"{RZP_API}/orders",
            auth=(RZP_KEY_ID, RZP_KEY_SECRET),
            timeout=15,
            json={
                "amount": total * 100,
                "currency": "INR",
                "receipt": f"order_{oid}",
                "notes": {"shop_order_id": str(oid)},
            },
        )
        r.raise_for_status()
        rzp_id = r.json()["id"]

    except (requests.RequestException, KeyError, ValueError):
        db().rollback()
        return jsonify(
            error="Could not start the payment. Please try again or choose cash on delivery."
        ), 502

    db().execute(
        "UPDATE orders SET razorpay_order_id = ? WHERE id = ?",
        (rzp_id, oid)
    )
    db().commit()

    return jsonify(
        order_id=oid,
        total=total,
        payment={
            "key": RZP_KEY_ID,
            "razorpay_order_id": rzp_id,
            "amount": total * 100,
            "currency": "INR",
        },
    ), 201


def mark_paid(rzp_order_id, rzp_payment_id):
    """Idempotent: called by both the browser callback and webhook."""

    o = db().execute(
        "SELECT * FROM orders WHERE razorpay_order_id = ?",
        (rzp_order_id,)
    ).fetchone()

    if not o:
        return None

    if o["payment_status"] in ("Paid", "Refund due", "Refunded"):
        return o["id"]

    if o["status"] == "Cancelled":
        db().execute(
            "UPDATE orders SET payment_status='Refund due', "
            "razorpay_payment_id=? WHERE id=?",
            (rzp_payment_id, o["id"])
        )
    else:
        db().execute(
            "UPDATE orders SET payment_status='Paid', razorpay_payment_id=?, "
            "status=CASE WHEN status=? THEN 'New' ELSE status END WHERE id=?",
            (rzp_payment_id, PENDING, o["id"])
        )

    db().commit()
    return o["id"]


@app.post("/api/payment/verify")
def verify_payment():
    d = request.get_json(silent=True) or {}

    rzp_order, rzp_pay, sig = (
        str(d.get(k, ""))
        for k in (
            "razorpay_order_id",
            "razorpay_payment_id",
            "razorpay_signature",
        )
    )

    expected = hmac.new(
        RZP_KEY_SECRET.encode(),
        f"{rzp_order}|{rzp_pay}".encode(),
        hashlib.sha256,
    ).hexdigest()

    if not ONLINE_ENABLED or not hmac.compare_digest(expected, sig):
        return jsonify(
            error="Payment could not be verified. If money was deducted, call us with your order number."
        ), 400

    oid = mark_paid(rzp_order, rzp_pay)

    if not oid:
        return jsonify(error="Order not found."), 404

    return jsonify(order_id=oid, status="Paid")


@app.post("/api/payment/webhook")
def razorpay_webhook():
    body = request.get_data()
    sig = request.headers.get("X-Razorpay-Signature", "")

    expected = hmac.new(
        RZP_WEBHOOK_SECRET.encode(),
        body,
        hashlib.sha256,
    ).hexdigest()

    if not RZP_WEBHOOK_SECRET or not hmac.compare_digest(expected, sig):
        return "bad signature", 400

    try:
        ev = json.loads(body)
    except ValueError:
        return "bad json", 400

    if ev.get("event") in ("payment.captured", "order.paid"):
        pay = (
            ((ev.get("payload") or {}).get("payment") or {}).get("entity")
            or {}
        )

        if pay.get("order_id") and pay.get("id"):
            mark_paid(pay["order_id"], pay["id"])

    return "ok"


def digits(x):
    return "".join(ch for ch in str(x) if ch.isdigit())


@app.get("/api/track")
def track():
    miss = jsonify(
        error="No order matches that number and phone. Check both and try again."
    ), 404

    try:
        oid = int(str(request.args.get("id", "")).strip().lstrip("#"))
    except ValueError:
        return miss

    o = db().execute(
        "SELECT * FROM orders WHERE id = ?", (oid,)
    ).fetchone()

    ph = digits(request.args.get("phone", ""))

    if not o or len(ph) < 7 or digits(o["phone"])[-10:] != ph[-10:]:
        return miss

    items = db().execute(
        "SELECT name, qty, price FROM order_items WHERE order_id = ?",
        (oid,)
    ).fetchall()

    return jsonify(
        id=o["id"],
        status=o["status"],
        total=o["total"],
        created=o["created"],
        payment_status=o["payment_status"],
        gift_wrap=bool(o["gift_wrap"]),
        items=[dict(i) for i in items],
    )


@app.post("/api/cancel")
def cancel():
    d = request.get_json(silent=True) or {}

    try:
        oid = int(str(d.get("id", "")).strip().lstrip("#"))
    except ValueError:
        return jsonify(
            error="No order matches that number and phone."
        ), 404

    o = db().execute(
        "SELECT * FROM orders WHERE id = ?", (oid,)
    ).fetchone()

    ph = digits(d.get("phone", ""))

    if not o or len(ph) < 7 or digits(o["phone"])[-10:] != ph[-10:]:
        return jsonify(
            error="No order matches that number and phone."
        ), 404

    if o["status"] == "Cancelled":
        return jsonify(error="This order is already cancelled."), 400

    if o["status"] not in (PENDING, "New", "Packed"):
        return jsonify(
            error="This order has already shipped, so it can't be cancelled. Call us for a return."
        ), 400

    refund = o["payment_status"] == "Paid"

    db().execute(
        "UPDATE orders SET status='Cancelled', "
        "payment_status=CASE WHEN payment_status='Paid' "
        "THEN 'Refund due' ELSE payment_status END WHERE id=?",
        (oid,)
    )
    db().commit()

    return jsonify(status="Cancelled", refund=refund)


def authed():
    a = request.authorization
    return a and a.password == ADMIN_PASSWORD


@app.route("/admin", methods=["GET", "POST"])
def admin():
    if not authed():
        return Response(
            "Sign in with the admin password.",
            401,
            {"WWW-Authenticate": 'Basic realm="Admin"'},
        )

    if request.method == "POST":
        oid = request.form["id"]

        if "status" in request.form:
            db().execute(
                "UPDATE orders SET status = ? WHERE id = ?",
                (request.form["status"], oid)
            )

            if request.form["status"] == "Cancelled":
                db().execute(
                    "UPDATE orders SET payment_status='Refund due' "
                    "WHERE id=? AND payment_status='Paid'",
                    (oid,)
                )

        if "payment_status" in request.form:
            db().execute(
                "UPDATE orders SET payment_status = ? WHERE id = ?",
                (request.form["payment_status"], oid)
            )

        db().commit()

    orders = db().execute(
        "SELECT * FROM orders ORDER BY id DESC"
    ).fetchall()

    items = {}

    for r in db().execute("SELECT * FROM order_items"):
        items.setdefault(r["order_id"], []).append(
            f'{r["qty"]} × {r["name"]}'
        )

    return render_template("admin.html", orders=orders, items=items)


init_db()

if __name__ == "__main__":
    app.run(debug=True)
