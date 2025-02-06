import os
import json
from flask import Flask, request, jsonify
import requests
from slack_sdk import WebClient


app = Flask(__name__)

# --- Box Configuration (HTTPS Call) ---
BOX_API_URL = os.environ.get("BOX_API_URL")  # The full URL for your Box API endpoint
BOX_DEV_TOKEN = os.environ.get("BOX_DEV_TOKEN")
BOX_HUB_ID = os.environ.get("BOX_HUB_ID") # Your hardcoded hub ID

# --- Slack Configuration ---
SLACK_BOT_TOKEN = os.environ.get("SLACK_BOT_TOKEN")
slack_client = WebClient(token=SLACK_BOT_TOKEN)

# --- Flask Routes ---

@app.route('/askboxhub', methods=['POST'])
def askboxhub():
    data = request.form
    prompt = data.get('text')
    channel_id = data.get('channel_id')

    if not prompt:
        return jsonify({"response_type": "ephemeral", "text": "Please provide a prompt."})

    headers = {
        "Content-Type": "application/json",  # Important: Specify JSON content type
        "Authorization": f"Bearer {BOX_DEV_TOKEN}"  # Use Bearer token
    }

    payload = {
        "mode": "multiple_item_qa",  # Assuming this is consistent
        "prompt": prompt,
        "items": [{"type": "hub", "id": BOX_HUB_ID}]  # Include the hub ID
    }

    try:
        response = requests.post(BOX_API_URL, headers=headers, json=payload)
        response.raise_for_status()  # Raise HTTPError for bad responses (4xx or 5xx)
        box_data = response.json()

        answer = box_data.get("answer")  # Adjust based on your actual response structure

        # Post to Slack
        try:
            slack_client.chat_postMessage(
                channel=channel_id,
                text=answer,
            )
            return jsonify({})  # Important: Return empty body for slash commands

        except e:
            print(f"Error posting to Slack: {e}")
            return jsonify({"response_type": "ephemeral", "text": f"Error posting to Slack: {e}"})

    except requests.exceptions.RequestException as e:  # Catch HTTP errors
        print(f"Error calling Box API: {e}")
        return jsonify({"response_type": "ephemeral", "text": f"Error calling Box API: {e}"})
    except (KeyError, TypeError) as e: # Handle potential JSON parsing errors
        print(f"Error parsing Box API response: {e}, Response text: {response.text}")
        return jsonify({"response_type": "ephemeral", "text": "Error parsing Box API response."})



# ... (Interactive button handling would go here if needed)

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(debug=True, host='0.0.0.0', port=port)