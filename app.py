import json
import os
import uuid
from datetime import datetime, UTC

from flask import Flask, jsonify, make_response, request
from flask_cors import CORS

import firebase_admin
from firebase_admin import credentials, firestore, messaging
from google.cloud.firestore import ArrayUnion
from google.cloud.firestore_v1.base_query import FieldFilter

app = Flask(__name__)
CORS(app)

# ─────────────────────────────────────────────
# FIREBASE INIT (Railway ENV supported)
# ─────────────────────────────────────────────
if not firebase_admin._apps:
    firebase_config = os.environ.get("FIREBASE_KEY")

    if firebase_config:
        cred = credentials.Certificate(json.loads(firebase_config))
    else:
        cred = credentials.Certificate("serviceAccountKey.json")  # local only

    firebase_admin.initialize_app(cred)

db = firestore.client()

# ─────────────────────────────────────────────
# HEALTH CHECK
# ─────────────────────────────────────────────
@app.route("/")
def home():
    return "FreshMart API running 🚀"
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
# CORS
# ─────────────────────────────────────────────
@app.after_request
def add_cors_headers(response):
    origin = request.headers.get("Origin")
    response.headers["Access-Control-Allow-Origin"] = origin or "*"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization"
    response.headers["Access-Control-Allow-Methods"] = "GET, POST, PUT, DELETE, OPTIONS"
    return response

@app.route("/<path:_path>", methods=["OPTIONS"])
def generic_preflight(_path):
    return make_response("", 204)

# ─────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────
def normalise_unit(unit_value, unit_type):
    try:
        v = float(unit_value)
    except:
        return unit_value, unit_type

    ut = (unit_type or "").lower()
    if ut == "kg":
        return v * 1000, "g"
    if ut == "l":
        return v * 1000, "ml"
    return v, unit_type


def send_order_notification(order_id, mobile, total):
    try:
        for owner_doc in db.collection("owners").stream():
            token = owner_doc.to_dict().get("fcmToken")
            if not token:
                continue

            message = messaging.Message(
                notification=messaging.Notification(
                    title="New Order",
                    body=f"Order #{order_id} Rs.{total} from {mobile}"
                ),
                token=token,
            )
            messaging.send(message)
    except Exception as e:
        print("FCM Error:", e)

# ─────────────────────────────────────────────
# AUTH
# ─────────────────────────────────────────────
@app.route("/register", methods=["POST"])
def register():
    data = request.json or {}
    mobile = data.get("mobile")
    name = data.get("name")

    if not mobile or not name:
        return jsonify({"error": "missing data"}), 400

    ref = db.collection("users").document(mobile)

    if ref.get().exists:
        return jsonify({"error": "already exists"}), 409

    ref.set({
        "name": name,
        "mobile": mobile,
        "approved": False,
        "createdAt": datetime.now(UTC)
    })

    return jsonify({"message": "registered"}), 201


@app.route("/login", methods=["POST"])
def login():
    mobile = request.json.get("mobile")

    doc = db.collection("users").document(mobile).get()

    if not doc.exists:
        return jsonify({"error": "not found"}), 404

    return jsonify(doc.to_dict())


# ─────────────────────────────────────────────
# PRODUCTS
# ─────────────────────────────────────────────
@app.route("/products", methods=["GET"])
def products():
    result = []
    for doc in db.collection("products").stream():
        d = doc.to_dict()
        d["id"] = doc.id
        result.append(d)
    return jsonify(result)


@app.route("/owner/add-product", methods=["POST"])
def add_product():
    data = request.json or {}

    value, unit = normalise_unit(data.get("unitValue"), data.get("unitType"))

    db.collection("products").add({
        "name": data.get("name"),
        "price": float(data.get("price")),
        "quantity": int(data.get("quantity")),
        "unitValue": value,
        "unitType": unit,
        "createdAt": datetime.now(UTC)
    })

    return jsonify({"message": "added"})


# ─────────────────────────────────────────────
# ORDER
# ─────────────────────────────────────────────
@app.route("/customer/place-order", methods=["POST"])
def place_order():
    data = request.json or {}

    order_id = str(uuid.uuid4())[:8]

    db.collection("orders").add({
        "orderId": order_id,
        "mobile": data.get("mobile"),
        "items": data.get("items"),
        "totalPrice": data.get("totalPrice"),
        "status": "Pending",
        "createdAt": datetime.now(UTC)
    })

    send_order_notification(order_id, data.get("mobile"), data.get("totalPrice"))

    return jsonify({"orderId": order_id})


# ─────────────────────────────────────────────
# RUN (RAILWAY FIX)
# ─────────────────────────────────────────────
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))  # IMPORTANT
    app.run(host="0.0.0.0", port=port)
