import json
import os
import uuid
from datetime import datetime, UTC

from flask import Flask, jsonify, request
from flask_cors import CORS

import firebase_admin
from firebase_admin import credentials, firestore, messaging

app = Flask(__name__)
CORS(app)

# ─────────────────────────────────────────────
# FIREBASE INIT
# ─────────────────────────────────────────────
if not firebase_admin._apps:
    firebase_config = os.environ.get("FIREBASE_KEY")

    if firebase_config:
        cred = credentials.Certificate(json.loads(firebase_config))
    else:
        cred = credentials.Certificate("serviceAccountKey.json")

    firebase_admin.initialize_app(cred)

db = firestore.client()

# ─────────────────────────────────────────────
# HEALTH CHECK
# ─────────────────────────────────────────────
@app.route("/")
def home():
    return "FreshMart API running 🚀"


# ─────────────────────────────────────────────
# OWNER LOGIN
# ─────────────────────────────────────────────
@app.route("/owner/login", methods=["POST"])
def owner_login():
    data = request.json or {}
    mobile = (data.get("mobile") or "").strip()
    password = (data.get("password") or "").strip()

    owner_ref = db.collection("owners").document(mobile)
    owner_doc = owner_ref.get()

    if not owner_doc.exists:
        return jsonify({"status": "error", "message": "Owner not found"}), 404

    owner_data = owner_doc.to_dict()

    if owner_data.get("password") != password:
        return jsonify({"status": "error", "message": "Invalid password"}), 401

    return jsonify({
        "status": "success",
        "owner": {
            "mobile": mobile,
            "name": owner_data.get("name"),
            "shop": owner_data.get("shopName")
        }
    })


# ─────────────────────────────────────────────
# SAVE FCM TOKEN
# ─────────────────────────────────────────────
@app.route("/owner/save-fcm-token", methods=["POST"])
def save_fcm_token():
    data = request.json or {}
    mobile = data.get("mobile")
    token = data.get("fcmToken")

    if not mobile or not token:
        return jsonify({"status": "error"}), 400

    db.collection("owners").document(mobile).update({
        "fcmToken": token
    })

    return jsonify({"status": "success"})


# ─────────────────────────────────────────────
# SEND NOTIFICATION FUNCTION
# ─────────────────────────────────────────────
def send_notification(title, body):
    try:
        for owner in db.collection("owners").stream():
            token = owner.to_dict().get("fcmToken")

            if not token:
                continue

            message = messaging.Message(
                notification=messaging.Notification(
                    title=title,
                    body=body
                ),
                token=token,
            )

            messaging.send(message)

    except Exception as e:
        print("FCM ERROR:", e)


# ─────────────────────────────────────────────
# PLACE ORDER
# ─────────────────────────────────────────────
@app.route("/customer/place-order", methods=["POST"])
def place_order():
    data = request.json or {}

    mobile = data.get("mobile")
    items = data.get("items")
    total = data.get("totalPrice")

    if not mobile or not items:
        return jsonify({"status": "error"}), 400

    order_id = str(uuid.uuid4())[:8]

    db.collection("orders").add({
        "orderId": order_id,
        "mobile": mobile,
        "items": items,
        "totalPrice": total,
        "status": "Pending",
        "createdAt": datetime.now(UTC)
    })

    # 🔔 SEND NOTIFICATION
    send_notification(
        "🛒 New Order Received!",
        f"Order #{order_id} from {mobile}"
    )

    return jsonify({
        "status": "success",
        "orderId": order_id
    })


# ─────────────────────────────────────────────
# GET ORDERS (OWNER)
# ─────────────────────────────────────────────
@app.route("/owner/orders", methods=["GET"])
def get_orders():
    result = []
    for doc in db.collection("orders").stream():
        d = doc.to_dict()
        d["id"] = doc.id
        result.append(d)

    return jsonify(result)


# ─────────────────────────────────────────────
# UPDATE ORDER STATUS
# ─────────────────────────────────────────────
@app.route("/owner/order/<order_id>/status", methods=["PUT"])
def update_status(order_id):
    data = request.json or {}
    status = data.get("status")

    db.collection("orders").document(order_id).update({
        "status": status
    })

    return jsonify({"status": "success"})


# ─────────────────────────────────────────────
# RUN (RAILWAY)
# ─────────────────────────────────────────────
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
