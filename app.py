import os
import requests
from flask import Flask, request, jsonify

app = Flask(__name__)

# --- CONFIGURATION FROM ENVIRONMENT ---
NTFY_HOST = os.environ.get("NTFY_HOST_URL", "https://ntfy.tekgnosis.net")
AUTH_TOKEN = os.environ.get("NTFY_AUTH_TOKEN", "fallback_token_if_not_set")
NTFY_TOPIC = os.environ.get("NTFY_TOPIC", "omada")

# For post, build URL
NTFY_URL = NTFY + "/" + NTFY_TOPIC
# -------------------------------------

@app.route("/omada-webhook", methods=["POST"])
def handle_omada_webhook():
    try:
        omada_data = request.json
        if not omada_data:
            return jsonify({"error": "No data received"}), 400

        events = omada_data if isinstance(omada_data, list) else [omada_data]
        
        for item in events:
            event = item.get("event", {}) if "event" in item else item
            
            msg = event.get("text", "No message content")
            target = event.get("target", "System")
            category = event.get("category", "Notification")
            level = event.get("level", "INFO").upper()
            
            priority = "default"
            tags = "omada,network"
            if level in ["WARN", "WARNING"]:
                priority = "high"
                tags += ",warning"
            elif level in ["ALERT", "ERROR", "CRITICAL"]:
                priority = "urgent"
                tags += ",rotating_light,fire"
            elif level in ["NOTICE", "INFO"]:
                tags += ",information_source"

            notification_text = f"[{category}] {target}: {msg}"

            headers = {
                "Authorization": f"Bearer {AUTH_TOKEN}",
                "Title": f"Omada: {category} ({level})",
                "Priority": priority,
                "Tags": tags
            }

            requests.post(
                NTFY_URL, 
                data=notification_text.encode("utf-8"), 
                headers=headers
            )

        return jsonify({"status": "success", "processed_events": len(events)}), 200

    except Exception as e:
        return jsonify({"status": "exception", "details": str(e)}), 500

if __name__ == "__main__":
    # Internal port inside container must match the port mapped in compose
    app.run(host="0.0.0.0", port=5000)
