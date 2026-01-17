import asyncio
import base64
import os
from typing import Annotated

from fastapi import APIRouter, Form, Query, Request, Response, WebSocket, WebSocketDisconnect
from fastapi.params import Depends
from fastapi.responses import HTMLResponse
from twilio.rest import Client
from twilio.twiml.voice_response import Connect, Stream, VoiceResponse

from agents.runtime.live_messaging import (
    AgentEvent,
    agent_to_client_messaging,
    send_pcm_to_agent,
    start_agent_session,
    text_to_content,
)

from entities.twilio import (
    TwilioStreamCallbackPayload,
    TwilioVoiceWebhookPayload,
)
from utils.audio import (
    adk_pcm24k_to_twilio_ulaw8k,
    twilio_ulaw8k_to_adk_pcm16k,
)
from utils.env import is_local
from utils.logging import logger
from utils.security import validate_twilio
from utils.db import get_db_connection


twilio_path = "/twilio"
callback_path = "/callback"
stream_path = "/stream"
router = APIRouter(prefix=twilio_path, tags=["Twilio Webhooks"])

# Twilio client setup - load lazily to ensure env vars are set
def get_twilio_client():
    """Get Twilio client (lazy load to ensure env vars are set)"""
    sid = os.getenv("TWILIO_SID")
    auth = os.getenv("TWILIO_AUTH")
    if not sid or not auth:
        raise ValueError("TWILIO_SID or TWILIO_AUTH not set")
    return Client(sid, auth)

TWILIO_NUMBER = os.getenv("TWILIO_NUMBER")
DOMAIN = os.getenv("DOMAIN")


@router.post("/connect", dependencies=[Depends(validate_twilio)])
def create_call(req: Request, payload: Annotated[TwilioVoiceWebhookPayload, Form()]):
    """Generate TwiML to connect a call to a Twilio Media Stream"""

    host = req.url.hostname
    ws_protocol = "ws" if is_local else "wss"
    http_protocol = "http" if is_local else "https"
    ws_url = f"{ws_protocol}://{host}{twilio_path}{stream_path}"
    callback_url = f"{http_protocol}://{host}{twilio_path}{callback_path}"

    stream = Stream(url=ws_url, statusCallback=callback_url)
    stream.parameter(name="from_phone", value=payload.From)
    stream.parameter(name="to_phone", value=payload.To)
    connect = Connect()
    connect.append(stream)
    response = VoiceResponse()
    response.append(connect)

    logger.info(response)

    return HTMLResponse(content=str(response), media_type="application/xml")


@router.post(callback_path, status_code=204)
def twilio_callback(payload: Annotated[TwilioStreamCallbackPayload, Form()]):
    """Handle Twilio status callbacks"""

    logger.info(f"✓ Callback received: {payload.CallSid}")

    return Response(status_code=204)


@router.post("/outbound-call")
async def make_call():
    """Make an outbound call by picking the next target from the database"""
    if not DOMAIN:
        return {"error": "DOMAIN environment variable not set"}
    
    clean_domain = DOMAIN.replace("https://", "").replace("http://", "")
    
    conn = get_db_connection()
    if not conn:
        return {"error": "Database connection failed"}

    try:
        cur = conn.cursor()
        
        # 1. Fetch next 'Not Called' member (Queue logic)
        cur.execute("""
            SELECT member_id, phone_number, campaign_name 
            FROM campaign_target_member_call_list 
            WHERE call_status = 'Not Called' 
            LIMIT 1 
            FOR UPDATE SKIP LOCKED
        """)
        row = cur.fetchone()
        
        if not row:
            conn.rollback() # Release lock if no rows
            return {"status": "info", "message": "No numbers to call"}
        
        member_id, phone_number, campaign_name = row
        phone_number = phone_number.strip()
        
        # 2. Mark as Calling
        cur.execute("""
            UPDATE campaign_target_member_call_list 
            SET call_status = 'Calling' 
            WHERE member_id = %s
        """, (member_id,))
        
        conn.commit()
        logger.info(f"Picked member {member_id} ({phone_number}) for campaign {campaign_name}")

        # 3. Initiate Call
        try:
            twilio_client = get_twilio_client()
            call = twilio_client.calls.create(
                to=phone_number,
                from_=TWILIO_NUMBER,
                url=f"https://{clean_domain}{twilio_path}/voice-entry?phone={phone_number}"
            )
            return {
                "status": "queued", 
                "call_sid": call.sid, 
                "member_id": member_id, 
                "campaign": campaign_name
            }
        except Exception as twilio_ex:
            logger.error(f"Twilio Call Failed: {twilio_ex}")
            # Revert status if call fails to launch
            # Note: We create a new cursor/connection or reuse judiciously. 
            # Re-using 'cur' here is fine since previous transaction committed.
            try:
                cur.execute("""
                    UPDATE campaign_target_member_call_list 
                    SET call_status = 'Failed' 
                    WHERE member_id = %s
                """, (member_id,))
                conn.commit()
            except:
                pass
            return {"error": f"Twilio start failed: {str(twilio_ex)}"}

    except Exception as ex:
        if conn:
            conn.rollback()
        logger.exception(f"DB Error in make_call: {ex}")
        return {"error": str(ex)}
    finally:
        if conn:
            conn.close()


@router.post("/voice-entry")
def voice_entry(req: Request, phone: str = None):
    """Return TwiML that connects the call to a Media Stream"""
    logger.info("=" * 80)
    logger.info(f"📞 /voice-entry called with phone={phone}")
    
    try:
        host = req.headers.get("x-forwarded-host", req.url.hostname)
        logger.info(f"   Host: {host}")
        
        ws_protocol = "ws" if is_local else "wss"
        http_protocol = "http" if is_local else "https"
        ws_url = f"{ws_protocol}://{host}{twilio_path}{stream_path}"
        callback_url = f"{http_protocol}://{host}{twilio_path}{callback_path}"
        
        logger.info(f"   WebSocket URL: {ws_url}")
        logger.info(f"   Callback URL: {callback_url}")

        stream = Stream(url=ws_url, statusCallback=callback_url)
        stream.parameter(name="from_phone", value=phone or "")
        stream.parameter(name="to_phone", value=TWILIO_NUMBER)
        connect = Connect()
        connect.append(stream)
        response = VoiceResponse()
        response.append(connect)

        twiml_response = str(response)
        logger.info(f"✓ TwiML generated successfully:")
        logger.info(f"{twiml_response}")

        return HTMLResponse(content=twiml_response, media_type="application/xml")
    except Exception as ex:
        logger.error(f"✗ /voice-entry FAILED: {ex}", exc_info=True)
        raise


# TODO: Figure out how to validate Twilio signature in a WebSocket
# https://www.twilio.com/docs/usage/webhooks/webhooks-security
# Headers({'host': 'amazing-sincere-grouse.ngrok-free.app', 'user-agent': 'Twilio.TmeWs/1.0', 'connection': 'Upgrade', 'sec-websocket-key': '', 'sec-websocket-version': '13', 'upgrade': 'websocket', 'x-forwarded-for': '98.84.178.199', 'x-forwarded-host': 'amazing-sincere-grouse.ngrok-free.app', 'x-forwarded-proto': 'https', 'x-twilio-signature': '', 'accept-encoding': 'gzip'})


@router.websocket(stream_path)
async def twilio_websocket(ws: WebSocket):
    """Handle Twilio Media Stream WebSocket connection"""

    logger.info("=" * 80)
    logger.info("🔵 STEP 1: WebSocket connection received from Twilio")
    
    await ws.accept()
    logger.info("✓ STEP 2: WebSocket accepted")
    
    try:
        connected_event = await ws.receive_json()
        logger.info(f"✓ STEP 3: Connected event received")
    except Exception as ex:
        logger.error(f"✗ STEP 3 FAILED: Could not receive connected event: {ex}", exc_info=True)
        await ws.close()
        return

    try:
        start_event = await ws.receive_json()
        logger.info(f"✓ STEP 4: Start event received")
    except Exception as ex:
        logger.error(f"✗ STEP 4 FAILED: Could not receive start event: {ex}", exc_info=True)
        await ws.close()
        return

    if start_event.get("event") != "start":
        logger.error(f"✗ STEP 5 FAILED: Expected 'start' event, got: {start_event.get('event')}")
        await ws.close()
        return
    
    logger.info(f"✓ STEP 5: Start event validated")

    try:
        call_sid = start_event["start"]["callSid"]
        from_phone = start_event["start"]["customParameters"].get("from_phone", "unknown")
        stream_sid = start_event["streamSid"]
        
        logger.info(f"✓ STEP 6: Extracted call info - Call SID: {call_sid}, Phone: {from_phone}")
    except Exception as ex:
        logger.error(f"✗ STEP 6 FAILED: Could not extract call info: {ex}", exc_info=True)
        await ws.close()
        return

    try:
        logger.info(f"✓ STEP 7: Starting agent session for {from_phone}...")
        live_events, live_request_queue = await start_agent_session(from_phone, call_sid)
        logger.info(f"✓ STEP 8: Agent session started successfully")
    except Exception as ex:
        logger.error(f"✗ STEP 7-8 FAILED: Could not start agent session: {ex}", exc_info=True)
        await ws.close()
        return

    try:
        initial_message = text_to_content(
            "You're a customer service chatbot. Introduce yourself.", "user"
        )
        live_request_queue.send_content(initial_message)
        logger.info(f"✓ STEP 9: Initial message sent to agent")
    except Exception as ex:
        logger.error(f"✗ STEP 9 FAILED: Could not send initial message: {ex}", exc_info=True)
        await ws.close()
        return

    async def handle_agent_event(event: AgentEvent):
        """Handle outgoing AgentEvent to Twilio WebSocket"""

        if event.type == "complete":
            logger.info(f"✓ Agent turn complete at {event.timestamp}")
            return

        if event.type == "interrupted":
            logger.info(f"✓ Agent interrupted at {event.timestamp}")
            return await ws.send_json({"event": "clear", "streamSid": stream_sid})

        # Agent audio comes in the payload as PCM24k bytes
        if hasattr(event, 'payload') and event.payload:
            try:
                ulaw_bytes = adk_pcm24k_to_twilio_ulaw8k(event.payload)
                payload = base64.b64encode(ulaw_bytes).decode("ascii")
                logger.debug(f"→ Sending {len(ulaw_bytes)} bytes to Twilio")

                await ws.send_json(
                    {
                        "event": "media",
                        "streamSid": stream_sid,
                        "media": {"payload": payload},
                    }
                )
            except Exception as ex:
                logger.error(f"✗ Error sending audio to Twilio: {ex}")
        else:
            logger.debug(f"→ Agent event (no audio payload): {event.type}")

    async def websocket_loop():
        """
        Handle incoming WebSocket messages to Agent.
        """
        while True:
            event = await ws.receive_json()
            event_type = event["event"]

            if event_type == "stop":
                logger.info(f"✓ Call ended by Twilio. Stream SID: {stream_sid}")
                break

            if event_type == "start" or event_type == "connected":
                logger.debug(f"→ Twilio Initialization event: {event_type}")
                continue

            elif event_type == "dtmf":
                digit = event["dtmf"]["digit"]
                logger.info(f"→ DTMF received: {digit}")
                continue

            elif event_type == "mark":
                logger.debug(f"→ Mark event from Twilio")
                continue

            elif event_type == "media":
                payload = event["media"]["payload"]
                mulaw_bytes = base64.b64decode(payload)
                pcm_bytes = twilio_ulaw8k_to_adk_pcm16k(mulaw_bytes)
                logger.debug(f"← Received {len(pcm_bytes)} bytes from Twilio")
                send_pcm_to_agent(pcm_bytes, live_request_queue)
            else:
                logger.warning(f"? Unknown event type: {event_type}")

    try:
        logger.info("✓ STEP 10: Starting dual async tasks (WebSocket loop + Agent handler)")
        websocket_coro = websocket_loop()
        websocket_task = asyncio.create_task(websocket_coro)
        logger.info("  ✓ WebSocket loop task created")
        
        messaging_coro = agent_to_client_messaging(handle_agent_event, live_events)
        messaging_task = asyncio.create_task(messaging_coro)
        logger.info("  ✓ Agent messaging task created")
        
        tasks = [websocket_task, messaging_task]
        logger.info("✓ STEP 11: Waiting for first task to complete...")
        done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        logger.info(f"✓ STEP 12: First task completed. Done: {len(done)}, Pending: {len(pending)}")
        
        for p in pending:
            p.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
        
        for d in done:
            if d.cancelled():
                continue
            exception = d.exception()
            if exception:
                logger.error(f"✗ Task exception: {exception}", exc_info=exception)
                raise exception
        logger.info("✓ STEP 13: All tasks completed successfully")
    except (KeyboardInterrupt, asyncio.CancelledError, WebSocketDisconnect):
        logger.warning("⚠ STEP 13: Process interrupted by user or WebSocket disconnect")
    except Exception as ex:
        logger.exception(f"✗ STEP 13 FAILED: Unexpected error in main loop: {ex}")
    finally:
        logger.info("⏹ Cleaning up WebSocket connection...")
        if "live_request_queue" in locals() and live_request_queue is not None:
            try:
                live_request_queue.close()
                logger.info("✓ Request queue closed")
            except Exception as ex:
                logger.warning(f"⚠ Error closing request queue: {ex}")
        try:
            await ws.close()
            logger.info("✓ WebSocket closed")
        except Exception as ex:
            logger.warning(f"⚠ Error while closing WebSocket: {ex}")

    # https://www.twilio.com/docs/voice/media-streams/websocket-messages
    # {'event': 'connected', 'protocol': 'Call', 'version': '1.0.0'}
    # {'event': 'start', 'sequenceNumber': '1', 'start': {'accountSid': '', 'streamSid': '', 'callSid': '', 'tracks': ['inbound'], 'mediaFormat': {'encoding': 'audio/x-mulaw', 'sampleRate': 8000, 'channels': 1}, 'customParameters': {'caller': ''}}, 'streamSid': ''}
    # {'event': 'media', 'sequenceNumber': '2', 'media': {'track': 'inbound', 'chunk': '1', 'timestamp': '57', 'payload': '+33+/3t7/f3/fvv7fX3+/f5+/vv2fnv8ePt9ff59fn97/nr//3v9fH14+Hj+fv3++3x+/3j+fn35/f58fX3/e/15ff7+ff78+318/X99/P39/nx9f319+v3+fvp9///9/f5+/Pz/fX76//z+/Xx9+//9fv97fn79ev7//Xh9/3v+fP59/f///P7/+3p6/Hj7/Xz/eP59/X79f/7+/n77/g=='}, 'streamSid': ''}
    # {'event': 'stop', 'sequenceNumber': '50', 'streamSid': '', 'stop': {'accountSid': '', 'callSid': ''}}