"""
FreshMart Notification Backend
================================
Cloud-ready Flask app with FCM push notifications.
Deploy to Railway / Render / Fly.io

ENV VARS NEEDED:
  FIREBASE_KEY  — JSON string of serviceAccountKey.json
"""

import json, os, uuid
from datetime import datetime, UTC

from flask import Flask, jsonify, request
from flask_cors import CORS

import firebase_admin
from firebase_admin import credentials, firestore, messaging
from google.cloud.firestore_v1.base_query import FieldFilter

app = Flask(__name__)
CORS(app)

# ── Firebase init ──────────────────────────────────────────────────────────────
if not firebase_admin._apps:
    cfg = os.environ.get("FIREBASE_KEY")
    cred = credentials.Certificate(json.loads(cfg)) if cfg else credentials.Certificate("serviceAccountKey.json")
    firebase_admin.initialize_app(cred)

db = firestore.client()


# ── Helpers ────────────────────────────────────────────────────────────────────
def _order_dict(doc):
    d = doc.to_dict()
    d["id"] = doc.id
    for k in ("createdAt", "updatedAt", "deliveredAt"):
        if k in d and hasattr(d[k], "isoformat"):
            d[k] = d[k].isoformat()
    return d


def send_push(title: str, body: str, data: dict = None):
    """Send FCM push to ALL owner tokens stored in the owners collection."""
    sent = 0
    for owner_doc in db.collection("owners").stream():
        token = owner_doc.to_dict().get("fcmToken")
        if not token:
            continue
        try:
            msg = messaging.Message(
                notification=messaging.Notification(title=title, body=body),
                data={str(k): str(v) for k, v in (data or {}).items()},
                android=messaging.AndroidConfig(
                    priority="high",
                    notification=messaging.AndroidNotification(
                        sound="default",
                        channel_id="freshmart_orders",
                    ),
                ),
                apns=messaging.APNSConfig(
                    payload=messaging.APNSPayload(
                        aps=messaging.Aps(sound="default", badge=1)
                    )
                ),
                token=token,
            )
            messaging.send(msg)
            sent += 1
        except Exception as e:
            print(f"[FCM] send failed for token {token[:20]}…: {e}")
    print(f"[FCM] Sent to {sent} owner(s): {title}")
    return sent


# ══════════════════════════════════════════════════════════════════════════════
# HEALTH
# ══════════════════════════════════════════════════════════════════════════════
@app.route("/")
def home():
    return jsonify({"status": "ok", "app": "FreshMart Notify API 🚀"})


# ══════════════════════════════════════════════════════════════════════════════
# OWNER AUTH
# ══════════════════════════════════════════════════════════════════════════════
@app.route("/owner/login", methods=["POST"])
def owner_login():
    data = request.json or {}
    mobile = (data.get("mobile") or "").strip()
    password = (data.get("password") or "").strip()

    ref = db.collection("owners").document(mobile)
    doc = ref.get()
    if not doc.exists:
        return jsonify({"status": "error", "message": "Owner not found"}), 404

    owner_data = doc.to_dict()
    if owner_data.get("password") != password:
        return jsonify({"status": "error", "message": "Invalid password"}), 401

    safe = {k: v for k, v in owner_data.items() if k not in ("password", "createdAt", "updatedAt")}
    safe["mobile"] = mobile
    return jsonify({"status": "success", "owner": safe})


# ══════════════════════════════════════════════════════════════════════════════
# FCM TOKEN — save per owner
# ══════════════════════════════════════════════════════════════════════════════
@app.route("/owner/save-fcm-token", methods=["POST"])
def save_fcm_token():
    data = request.json or {}
    mobile = (data.get("mobile") or "").strip()
    token  = (data.get("fcmToken") or "").strip()

    if not mobile or not token:
        return jsonify({"status": "error", "message": "mobile and fcmToken required"}), 400

    ref = db.collection("owners").document(mobile)
    if not ref.get().exists:
        return jsonify({"status": "error", "message": "Owner not found"}), 404

    ref.update({"fcmToken": token, "tokenUpdatedAt": datetime.now(UTC)})
    print(f"[FCM] Token saved for owner {mobile}: {token[:20]}…")
    return jsonify({"status": "success", "message": "Token saved"})


# ══════════════════════════════════════════════════════════════════════════════
# TEST NOTIFICATION — manual trigger for testing
# ══════════════════════════════════════════════════════════════════════════════
@app.route("/owner/test-notification", methods=["POST"])
def test_notification():
    data   = request.json or {}
    mobile = (data.get("mobile") or "").strip()
    title  = data.get("title", "🧪 Test Notification")
    body   = data.get("body",  "Push notifications are working correctly!")

    # If mobile provided, send only to that owner
    if mobile:
        ref = db.collection("owners").document(mobile)
        doc = ref.get()
        if not doc.exists:
            return jsonify({"status": "error", "message": "Owner not found"}), 404
        token = doc.to_dict().get("fcmToken")
        if not token:
            return jsonify({
                "status": "error",
                "message": "No FCM token found. Please open the app first to register.",
            }), 400
        try:
            msg = messaging.Message(
                notification=messaging.Notification(title=title, body=body),
                android=messaging.AndroidConfig(
                    priority="high",
                    notification=messaging.AndroidNotification(
                        sound="default",
                        channel_id="freshmart_orders",
                    ),
                ),
                token=token,
            )
            messaging.send(msg)
            return jsonify({"status": "success", "message": "Test notification sent!"})
        except Exception as e:
            return jsonify({"status": "error", "message": str(e)}), 500

    # Else broadcast to all owners
    sent = send_push(title, body)
    return jsonify({"status": "success", "sent_to": sent})


# ══════════════════════════════════════════════════════════════════════════════
# CUSTOMER AUTH
# ══════════════════════════════════════════════════════════════════════════════
@app.route("/register", methods=["POST"])
def customer_register():
    data   = request.json or {}
    mobile = (data.get("mobile") or "").strip()
    name   = (data.get("name")   or "").strip()

    if not mobile or len(mobile) != 10 or not mobile.isdigit():
        return jsonify({"status": "error", "message": "Valid 10-digit mobile required"}), 400
    if not name:
        return jsonify({"status": "error", "message": "Name required"}), 400

    ref = db.collection("users").document(mobile)
    if ref.get().exists:
        return jsonify({"status": "error", "message": "Already registered. Please login."}), 409

    ref.set({"phone": mobile, "name": name, "role": "customer",
             "approved": False, "createdAt": datetime.now(UTC)})
    return jsonify({"status": "success", "user": {"phone": mobile, "name": name}}), 201


@app.route("/login", methods=["POST"])
def customer_login():
    data   = request.json or {}
    mobile = (data.get("mobile") or "").strip()

    if not mobile or len(mobile) != 10 or not mobile.isdigit():
        return jsonify({"status": "error", "message": "Valid 10-digit mobile required"}), 400

    doc = db.collection("users").document(mobile).get()
    if not doc.exists:
        return jsonify({"status": "error", "message": "Not registered. Please register first."}), 403

    user_data = {k: v for k, v in doc.to_dict().items() if k not in ("createdAt", "updatedAt")}
    return jsonify({"status": "success", "user": user_data})


# ══════════════════════════════════════════════════════════════════════════════
# OWNER — CUSTOMER LIST + APPROVAL
# ══════════════════════════════════════════════════════════════════════════════
@app.route("/owner/users", methods=["GET"])
def get_all_users():
    users = []
    for doc in db.collection("users").stream():
        d = doc.to_dict()
        if d.get("role") != "customer":
            continue
        mobile = doc.id
        created = d.get("createdAt")
        users.append({
            "mobile":   mobile,
            "name":     d.get("name", "Unknown"),
            "approved": d.get("approved", False),
            "createdAt": created.isoformat() if hasattr(created, "isoformat") else None,
        })
    users.sort(key=lambda x: x.get("createdAt") or "", reverse=True)
    return jsonify(users)


@app.route("/owner/users/<mobile>/approval", methods=["PUT"])
def set_user_approval(mobile):
    data     = request.json or {}
    approved = bool(data.get("approved", False))
    ref = db.collection("users").document(mobile)
    if not ref.get().exists:
        return jsonify({"status": "error", "message": "User not found"}), 404
    ref.update({"approved": approved, "updatedAt": datetime.now(UTC)})
    return jsonify({"status": "success", "approved": approved})


# ══════════════════════════════════════════════════════════════════════════════
# PRODUCTS
# ══════════════════════════════════════════════════════════════════════════════
@app.route("/products", methods=["GET"])
def get_products():
    result = []
    for p in db.collection("products").stream():
        d = p.to_dict()
        d["id"] = p.id
        d.pop("createdAt", None)
        result.append(d)
    return jsonify(result)


@app.route("/owner/add-product", methods=["POST"])
def add_product():
    data = request.json or {}
    name  = (data.get("name") or "").strip()
    price = data.get("price")
    if not name or price is None:
        return jsonify({"status": "error", "message": "name and price required"}), 400
    ref = db.collection("products").document()
    ref.set({
        "name":        name,
        "price":       float(price),
        "description": (data.get("description") or "").strip(),
        "unitValue":   float(data.get("unitValue", 1)),
        "unitType":    data.get("unitType", "pc"),
        "quantity":    float(data.get("quantity", 100)),
        "isActive":    True,
        "createdAt":   datetime.now(UTC),
    })
    return jsonify({"status": "success", "id": ref.id}), 201


# ══════════════════════════════════════════════════════════════════════════════
# PLACE ORDER  ← This sends the push notification
# ══════════════════════════════════════════════════════════════════════════════
@app.route("/customer/place-order", methods=["POST"])
def place_order():
    data    = request.json or {}
    mobile  = (data.get("mobile") or "").strip()
    items   = data.get("items", [])
    total   = float(data.get("totalPrice", 0))
    address = (data.get("address") or "").strip()

    if not mobile or not items:
        return jsonify({"status": "error", "message": "mobile and items required"}), 400

    # Check user exists & approved
    user_doc = db.collection("users").document(mobile).get()
    if not user_doc.exists:
        return jsonify({"status": "error", "code": "NOT_REGISTERED", "message": "Not registered"}), 403
    if not user_doc.to_dict().get("approved", False):
        return jsonify({"status": "error", "code": "NOT_APPROVED",
                        "message": "Account not approved. Contact owner."}), 403

    order_id  = str(uuid.uuid4())[:8].upper()
    order_ref = db.collection("orders").document()
    order_ref.set({
        "orderId":    order_id,
        "mobile":     mobile,
        "items":      items,
        "totalPrice": total,
        "grandTotal": total,
        "address":    address,
        "status":     "Pending",
        "createdAt":  datetime.now(UTC),
    })

    # ── 🔔 FIRE PUSH NOTIFICATION ──────────────────────────────────────────
    customer_name = user_doc.to_dict().get("name", mobile)
    item_count    = sum(int(i.get("qty", 1)) for i in items)
    item_names    = ", ".join(i.get("name", "") for i in items[:3])
    if len(items) > 3:
        item_names += f" +{len(items)-3} more"

    send_push(
        title=f"🛒 New Order #{order_id}",
        body=f"{customer_name} ordered {item_count} item(s): {item_names} — ₹{total:.0f}",
        data={
            "orderId":    order_id,
            "docId":      order_ref.id,
            "mobile":     mobile,
            "totalPrice": str(total),
            "type":       "new_order",
        },
    )
    # ───────────────────────────────────────────────────────────────────────

    return jsonify({"status": "success", "orderId": order_id, "id": order_ref.id}), 201


# ══════════════════════════════════════════════════════════════════════════════
# ORDERS — owner view
# ══════════════════════════════════════════════════════════════════════════════
@app.route("/owner/orders", methods=["GET"])
def get_all_orders():
    orders = []
    for doc in db.collection("orders").stream():
        orders.append(_order_dict(doc))
    orders.sort(key=lambda x: x.get("createdAt", ""), reverse=True)
    return jsonify(orders)


@app.route("/owner/order/<order_id>/status", methods=["PUT"])
def update_order_status(order_id):
    data       = request.json or {}
    new_status = (data.get("status") or "").strip()
    valid = {"Pending", "Processing", "Out for Delivery", "Delivered", "Cancelled"}
    if new_status not in valid:
        return jsonify({"status": "error", "message": "Invalid status"}), 400

    ref = db.collection("orders").document(order_id)
    doc = ref.get()
    if not doc.exists:
        return jsonify({"status": "error", "message": "Order not found"}), 404

    if new_status == "Delivered":
        delivered_data = {**doc.to_dict(), "status": "Delivered", "deliveredAt": datetime.now(UTC)}
        db.collection("delivered_orders").document(order_id).set(delivered_data)
        ref.delete()
        return jsonify({"status": "success", "message": "Delivered and archived"})

    ref.update({"status": new_status, "updatedAt": datetime.now(UTC)})
    return jsonify({"status": "success"})


# ══════════════════════════════════════════════════════════════════════════════
# CUSTOMER — view own orders
# ══════════════════════════════════════════════════════════════════════════════
@app.route("/customer/orders", methods=["GET"])
def get_customer_orders():
    mobile = (request.args.get("mobile") or "").strip()
    if not mobile:
        return jsonify({"status": "error", "message": "mobile required"}), 400
    orders = []
    for doc in db.collection("orders").where(filter=FieldFilter("mobile", "==", mobile)).stream():
        orders.append(_order_dict(doc))
    for doc in db.collection("delivered_orders").where(filter=FieldFilter("mobile", "==", mobile)).stream():
        orders.append(_order_dict(doc))
    orders.sort(key=lambda x: x.get("createdAt", ""), reverse=True)
    return jsonify(orders)


# ══════════════════════════════════════════════════════════════════════════════
# RUN
# ══════════════════════════════════════════════════════════════════════════════
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
