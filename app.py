import os
import json
import time
from flask import Flask, request, jsonify
import requests
import slack_sdk
from slack_sdk import WebClient
from slack_sdk.socket_mode import SocketModeClient
from slack_sdk.socket_mode.response import SocketModeResponse
from slack_sdk.socket_mode.request import SocketModeRequest
from threading import Thread, Timer
import logging
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Set up logging
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

def validate_config():
    """Validate required configuration"""
    required_vars = {
        'SLACK_APP_TOKEN': 'xapp-',
        'SLACK_BOT_TOKEN': 'xoxb-',
        'BOX_API_URL': None,
        'BOX_DEV_TOKEN': None,
        'BOX_HUB_ID': None
    }
    
    missing_vars = []
    invalid_vars = []
    
    for var, prefix in required_vars.items():
        value = os.environ.get(var)
        if not value:
            missing_vars.append(var)
        elif prefix and not value.startswith(prefix):
            invalid_vars.append(f"{var} (should start with {prefix})")
        else:
            logger.debug(f"✓ {var} is properly configured")
    
    if missing_vars:
        raise ValueError(f"Missing required environment variables: {', '.join(missing_vars)}")
    if invalid_vars:
        raise ValueError(f"Invalid token format for: {', '.join(invalid_vars)}")

app = Flask(__name__)

# Configuration
BOX_API_URL = os.environ.get("BOX_API_URL")
BOX_DEV_TOKEN = os.environ.get("BOX_DEV_TOKEN")
BOX_HUB_ID = os.environ.get("BOX_HUB_ID")
SLACK_BOT_TOKEN = os.environ.get("SLACK_BOT_TOKEN")
SLACK_APP_TOKEN = os.environ.get("SLACK_APP_TOKEN")
BOX_DEV_TOKEN = 'YOUR_BOX_DEV_TOKEN'
# Initialize Slack clients
web_client = WebClient(token=SLACK_BOT_TOKEN)
socket_mode_client = SocketModeClient(
    app_token=SLACK_APP_TOKEN,
    web_client=web_client
)

def query_box_ai(prompt):
    """Query Box AI with the given prompt"""
    logger.debug(f"Querying Box AI with prompt: {prompt}")
    
    headers = {
        "Content-Type": "application/json",
        "Authorization": "Bearer ********",
        "Cookie": "csrf-token=*******"
    }
    
    payload = {
        "mode": "multiple_item_qa", 
        "items": [
            {
                "type": "hubs",
                "id": "129786028"
            }
        ],
        "prompt": prompt,
        "includes_citations": "True"
    }
    
    logger.debug("=== Box API Request Details ===")
    logger.debug(f"URL: {BOX_API_URL}")
    logger.debug("Headers:")
    for key, value in headers.items():
        if key == "Authorization":
            logger.debug(f"  {key}: Bearer [last 4 digits: {value[-4:]}]")
        else:
            logger.debug(f"  {key}: {value}")
    logger.debug(f"Payload: {json.dumps(payload, indent=2)}")
    
    response = requests.post(BOX_API_URL, headers=headers, json=payload)
    
    logger.debug("=== Box API Response Details ===")
    logger.debug(f"Status Code: {response.status_code}")
    logger.debug("Response Headers:")
    for key, value in response.headers.items():
        logger.debug(f"  {key}: {value}")
    logger.debug(f"Response Body: {response.text}")
    
    response.raise_for_status()
    return response.json().get("answer")

def check_thread_replies(client, channel, thread_ts):
    """Check if thread has any replies"""
    try:
        # Get thread replies
        response = client.web_client.conversations_replies(
            channel=channel,
            ts=thread_ts
        )
        # Check if there are more than 1 message (original + replies)
        return len(response["messages"]) > 1
    except Exception as e:
        logger.error(f"Error checking thread replies: {str(e)}")
        return False

def delayed_box_response(client, channel, text, thread_ts):
    """Function to execute after delay"""
    try:
        # Check if anyone has replied in the thread
        if check_thread_replies(client, channel, thread_ts):
            logger.info(f"Thread {thread_ts} already has replies, skipping Box AI response")
            return

        # Query Box AI
        answer = query_box_ai(text)
        
        # Send response in thread
        client.web_client.chat_postMessage(
            channel=channel,
            thread_ts=thread_ts,
            text=f"Here's what I found:\n{answer}"
        )
    except Exception as e:
        logger.error(f"Error in delayed response: {str(e)}")

def process_slack_event(client: SocketModeClient, req: SocketModeRequest):
    """Process incoming Slack events"""
    try:
        logger.debug(f"Received event type: {req.type}")
        logger.debug(f"Full request payload: {json.dumps(req.payload, indent=2)}")
        
        # Acknowledge the event immediately
        ack = SocketModeResponse(envelope_id=req.envelope_id)
        client.send_socket_mode_response(ack)
        
        if req.type == "events_api":
            event = req.payload.get("event", {})
            event_type = event.get("type")
            
            if event_type == "message":
                # Skip if message is from a bot or has certain subtypes
                if event.get("bot_id") or event.get("subtype"):
                    return

                # Skip if message is already in a thread
                if event.get("thread_ts"):
                    return
                
                text = event.get("text", "")
                channel = event.get("channel")
                message_ts = event.get("ts")  # This will be the thread_ts for replies
                
                logger.debug(f"Message received: {text} in channel: {channel}")
                
                # Add thinking reaction
                try:
                    client.web_client.reactions_add(
                        channel=channel,
                        timestamp=message_ts,
                        name="timer_clock"
                    )
                except Exception as e:
                    logger.error(f"Error adding reaction: {str(e)}")
                
                # Schedule delayed response
                timer = Timer(
                    1.0,  # 30 second delay
                    delayed_box_response,
                    args=[client, channel, text, message_ts]
                )
                timer.start()
                
        elif req.type == "slash_commands" and req.payload.get("command") == "/askboxhub":
            handle_slash_command(client, req)
            
    except Exception as e:
        logger.error(f"Error processing event: {str(e)}", exc_info=True)

def handle_slash_command(client, req):
    """Handle the /askboxhub slash command"""
    try:
        prompt = req.payload.get("text", "").strip()
        channel_id = req.payload.get("channel_id")
        user_id = req.payload.get("user_id")
        
        if not prompt:
            client.web_client.chat_postEphemeral(
                channel=channel_id,
                user=user_id,
                text="Please provide a prompt with your command."
            )
            return

        # Query Box AI
        answer = query_box_ai(prompt)
        
        # Send response
        client.web_client.chat_postMessage(
            channel=channel_id,
            text=f"*Question:* {prompt}\n*Answer:* {answer}"
        )
        
    except Exception as e:
        logger.error(f"Error processing slash command: {str(e)}", exc_info=True)
        client.web_client.chat_postEphemeral(
            channel=channel_id,
            user=user_id,
            text=f"An error occurred: {str(e)}"
        )

# Register the event handler
socket_mode_client.socket_mode_request_listeners.append(process_slack_event)

@app.route('/health', methods=['GET'])
def health():
    return jsonify({"status": "ok"})

def run_flask():
    port = int(os.environ.get("PORT", 3000))
    app.run(host='0.0.0.0', port=port)

if __name__ == "__main__":
    try:
        logger.info("Starting Box AI Assistant...")
        
        # Validate configuration before starting
        validate_config()
        logger.debug("Configuration validated successfully")
        
        # Test Slack connection
        logger.debug("Testing Slack connection...")
        auth_test = web_client.auth_test()
        logger.debug(f"Connected to Slack as: {auth_test['bot_id']}")
        
        # Start Flask in a separate thread
        flask_thread = Thread(target=run_flask)
        flask_thread.daemon = True
        flask_thread.start()
        
        # Start Socket Mode client
        logger.info("Connecting to Slack...")
        socket_mode_client.connect()
        
        # Keep the main thread running
        while True:
            import time
            time.sleep(1)
            
    except ValueError as e:
        logger.error(f"Configuration error: {str(e)}")
        exit(1)
    except KeyboardInterrupt:
        logger.info("Shutting down gracefully...")
    except Exception as e:
        logger.error(f"Error in main thread: {str(e)}", exc_info=True)
    finally:
        logger.info("Application stopped")