from fastapi import APIRouter, Request, HTTPException
import os
from controllers.node_controller import send_test_to_node,send_user_to_node

router = APIRouter()

@router.get("/test-send")
def test_send():
    return send_test_to_node()

@router.post("/from-node")
async def receive_from_node(request: Request):
    SHARED_SECRET = os.getenv("SHARED_SECRET")
    key = request.headers.get("x-api-key")

    if key != SHARED_SECRET:
        raise HTTPException(status_code=401, detail="Unauthorized")

    data = await request.json()
    print("📩 Data received from Node:", data)

    return {"status": "python_received", "data": data}

@router.post("/test-save-user")
async def test_save_user():
    result = send_user_to_node(
        fullName="Python User",
        email="python@example.com",
        phone="9990001111"
    )
    return result