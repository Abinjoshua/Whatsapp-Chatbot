import os
import requests

# Load from environment
NODE_API_URL = os.getenv("NODE_API_URL", "http://localhost:3000/python/from-python")
SHARED_SECRET = os.getenv("SHARED_SECRET")


def send_appointment_to_node(record: dict):
    """
    Sends confirmed appointment data from Python → Node backend.
    """

    try:
        response = requests.post(
            NODE_API_URL,
            json=record,
            headers={"x-api-key": SHARED_SECRET},
            timeout=5
        )

        print("📤 Sent to Node. Response:", response.text)
        return True

    except Exception as e:
        print("❌ Error sending appointment to Node:", e)
        return False
