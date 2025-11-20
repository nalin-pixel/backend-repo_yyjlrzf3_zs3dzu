import os
from typing import List, Dict, Any
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from schemas import Order, OrderItem, MenuItem
from database import create_document, get_documents, db

app = FastAPI(title="RTU Canteen API", version="1.0.0")

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
