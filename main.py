import os
from typing import List, Dict, Any, Optional
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from schemas import Order, OrderItem, MenuItem
from database import create_document, get_documents, db

app = FastAPI(title="RTU Canteen API", version="1.2.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Static menu definition as source of truth
BEVERAGES: List[Dict[str, Any]] = [
    {"name": "Tea", "category": "beverage", "price": 10.0},
    {"name": "Coffee", "category": "beverage", "price": 10.0},
    {"name": "Cold Coffee", "category": "beverage", "price": 30.0},
    {"name": "Banana Shake", "category": "beverage", "price": 90.0, "size": "1 litre"},
]
FAST_FOOD: List[Dict[str, Any]] = [
    {"name": "Patties", "category": "fast-food", "price": 20.0},
    {"name": "Cold Drink", "category": "fast-food", "price": 100.0, "size": "2 litre"},
]

DISCOUNT_THRESHOLD = 299.0
DISCOUNT_RATE = 0.20  # 20%

class MenuResponse(BaseModel):
    beverages: List[MenuItem]
    fast_food: List[MenuItem]
    discount_threshold: float
    discount_rate: float
    note: str

@app.get("/")
def root():
    return {"service": "RTU Canteen API", "status": "ok"}

@app.get("/api/menu", response_model=MenuResponse)
def get_menu():
    beverages = [MenuItem(**i) for i in BEVERAGES]
    fast_food = [MenuItem(**i) for i in FAST_FOOD]
    note = f"Get {int(DISCOUNT_RATE*100)}% off on orders above ₹{int(DISCOUNT_THRESHOLD)}!"
    return MenuResponse(
        beverages=beverages,
        fast_food=fast_food,
        discount_threshold=DISCOUNT_THRESHOLD,
        discount_rate=DISCOUNT_RATE,
        note=note,
    )

class CreateOrderRequest(BaseModel):
    customer_name: str
    hostel_block: str
    room_number: str
    phone: str
    items: List[Dict[str, Any]]  # {name, unit_price, quantity}
    notes: str | None = None

class CreateOrderResponse(BaseModel):
    order_id: str
    total: float
    subtotal: float
    discount: float
    status: str


def calculate_totals(items: List[Dict[str, Any]]):
    # Validate items against menu prices to prevent tampering
    menu_price_map: Dict[str, float] = {}
    for it in BEVERAGES + FAST_FOOD:
        menu_price_map[it["name"].lower()] = float(it["price"])

    order_items: List[OrderItem] = []
    subtotal = 0.0
    for raw in items:
        name = str(raw.get("name", "")).strip()
        quantity = int(raw.get("quantity", 0))
        if not name or quantity < 1:
            raise HTTPException(status_code=400, detail="Invalid item in order")
        key = name.lower()
        if key not in menu_price_map:
            raise HTTPException(status_code=400, detail=f"Unknown menu item: {name}")
        unit_price = menu_price_map[key]
        # If client sent unit_price, ignore and use server price
        sub = unit_price * quantity
        subtotal += sub
        order_items.append(OrderItem(name=name, unit_price=unit_price, quantity=quantity, subtotal=sub))

    discount = 0.0
    if subtotal > DISCOUNT_THRESHOLD:
        discount = round(subtotal * DISCOUNT_RATE, 2)
    total = round(subtotal - discount, 2)
    subtotal = round(subtotal, 2)
    return order_items, subtotal, discount, total

@app.post("/api/orders", response_model=CreateOrderResponse)
def create_order(payload: CreateOrderRequest):
    order_items, subtotal, discount, total = calculate_totals(payload.items)

    order = Order(
        customer_name=payload.customer_name,
        hostel_block=payload.hostel_block,
        room_number=payload.room_number,
        phone=payload.phone,
        items=order_items,
        subtotal=subtotal,
        discount=discount,
        total=total,
        notes=payload.notes,
    )

    try:
        order_id = create_document("order", order)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")

    return CreateOrderResponse(order_id=order_id, total=total, subtotal=subtotal, discount=discount, status=order.status)

@app.get("/api/orders")
def list_orders():
    try:
        docs = get_documents("order", limit=20)
        for d in docs:
            d["_id"] = str(d.get("_id"))
        return {"orders": docs}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")

# --- SMS Notifications (Twilio) ---
class SMSOrdersRequest(BaseModel):
    phone: Optional[str] = None  # E.164 format preferred (e.g. +919166585401)
    limit: int = 10

class SMSOrdersResponse(BaseModel):
    sent: bool
    to: str
    message_sid: Optional[str] = None
    preview: str


def _format_orders_sms(orders: List[Dict[str, Any]]) -> str:
    if not orders:
        return "RTU Canteen: No recent orders."
    lines: List[str] = ["RTU Canteen Orders:"]
    total_sum = 0.0
    for i, o in enumerate(orders[:10], start=1):
        name = o.get("customer_name", "?")
        total = o.get("total", 0)
        total_sum += float(total or 0)
        items = o.get("items", [])
        # Build short items string: Tea×2, Patties×1
        short_items = ", ".join([f"{it.get('name','')}×{it.get('quantity',1)}" for it in items][:3])
        lines.append(f"{i}. {name} - ₹{int(round(total))} ({short_items})")
    lines.append(f"Total Orders: {len(orders)} | Sum: ₹{int(round(total_sum))}")
    txt = "\n".join(lines)
    # SMS length safety
    return (txt[:157] + "…") if len(txt) > 160 else txt

@app.post("/api/notify/orders", response_model=SMSOrdersResponse)
def sms_recent_orders(payload: SMSOrdersRequest):
    try:
        docs = get_documents("order", limit=payload.limit or 10)
        for d in docs:
            d["_id"] = str(d.get("_id"))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")

    text = _format_orders_sms(docs)

    # Resolve phone number
    target_phone = payload.phone or os.getenv("NOTIFY_PHONE")
    if not target_phone:
        raise HTTPException(status_code=400, detail="Target phone not provided and NOTIFY_PHONE not set")

    # Twilio credentials
    account_sid = os.getenv("TWILIO_ACCOUNT_SID")
    auth_token = os.getenv("TWILIO_AUTH_TOKEN")
    from_number = os.getenv("TWILIO_FROM_NUMBER")

    if not (account_sid and auth_token and from_number):
        # If creds missing, return preview without sending
        return SMSOrdersResponse(sent=False, to=target_phone, preview=text)

    try:
        from twilio.rest import Client  # type: ignore
        client = Client(account_sid, auth_token)
        msg = client.messages.create(body=text, from_=from_number, to=target_phone)
        return SMSOrdersResponse(sent=True, to=target_phone, message_sid=getattr(msg, 'sid', None), preview=text)
    except Exception as e:
        # Surface error but include preview for visibility
        raise HTTPException(status_code=500, detail=f"SMS send failed: {str(e)}")

# Browser-friendly GET wrapper so you can trigger from a link
@app.get("/api/notify/orders")
def sms_recent_orders_get(phone: Optional[str] = Query(default=None, description="E.164 number e.g. +919166585401"), limit: int = 10):
    payload = SMSOrdersRequest(phone=phone, limit=limit)
    # Reuse the same logic by calling the function directly
    resp = sms_recent_orders(payload)  # type: ignore
    return resp

@app.get("/test")
def test_database():
    response = {
        "backend": "✅ Running",
        "database": "❌ Not Available",
        "database_url": None,
        "database_name": None,
        "connection_status": "Not Connected",
        "collections": []
    }
    try:
        if db is not None:
            response["database"] = "✅ Available"
            response["database_url"] = "✅ Configured"
            response["database_name"] = db.name if hasattr(db, 'name') else "✅ Connected"
            response["connection_status"] = "Connected"
            try:
                collections = db.list_collection_names()
                response["collections"] = collections[:10]
                response["database"] = "✅ Connected & Working"
            except Exception as e:
                response["database"] = f"⚠️  Connected but Error: {str(e)[:50]}"
        else:
            response["database"] = "⚠️  Available but not initialized"
    except Exception as e:
        response["database"] = f"❌ Error: {str(e)[:50]}"

    import os as _os
    response["database_url"] = "✅ Set" if _os.getenv("DATABASE_URL") else "❌ Not Set"
    response["database_name"] = "✅ Set" if _os.getenv("DATABASE_NAME") else "❌ Not Set"
    return response


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
