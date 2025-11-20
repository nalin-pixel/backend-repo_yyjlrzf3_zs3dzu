"""
Database Schemas for RTU Canteen

Each Pydantic model represents a collection in your database.
Model name is converted to lowercase for the collection name:
- Order -> "order" collection
- MenuItem -> typically static (not stored), kept here for validation if needed
"""

from pydantic import BaseModel, Field
from typing import Optional, List

class MenuItem(BaseModel):
    name: str = Field(..., description="Item name")
    category: str = Field(..., description="beverage | fast-food")
    price: float = Field(..., ge=0, description="Price in INR")
    size: Optional[str] = Field(None, description="Optional size/variant, e.g., 1 litre")

class OrderItem(BaseModel):
    name: str
    unit_price: float = Field(..., ge=0)
    quantity: int = Field(..., ge=1)
    subtotal: float = Field(..., ge=0)

class Order(BaseModel):
    customer_name: str
    hostel_block: str
    room_number: str
    phone: str
    items: List[OrderItem]
    subtotal: float = Field(..., ge=0)
    discount: float = Field(..., ge=0)
    total: float = Field(..., ge=0)
    notes: Optional[str] = None
    status: str = Field("placed", description="placed | preparing | out-for-delivery | delivered | cancelled")
