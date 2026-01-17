import os
import logging
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime
from google.adk.tools import ToolContext
import sys
import os

# Ensure we can import from project root 'utils' even if running from subfolder
current_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.abspath(os.path.join(current_dir, "../../.."))

# Use insert(0) to prioritize our project root over other paths (like potentially a site-packages utils)
if project_root not in sys.path:
    sys.path.insert(0, project_root)
    # print(f"DEBUG: Added {project_root} to sys.path", file=sys.stderr)

# Fallback: try one level deeper if 'agents' is root
agent_root = os.path.abspath(os.path.join(current_dir, "../.."))
if agent_root not in sys.path:
    sys.path.insert(0, agent_root)

from utils.db import get_db_connection

def _get_member_data(phone_number: str, member_id: str = None, campaign_name: str = None):
    """Raw helper to fetch member data dict from DB using strict matching."""
    conn = get_db_connection()
    if not conn:
        return None
    try:
        cur = conn.cursor()
        
        # Build query dynamically based on available filters, but prioritize strictness
        if member_id and campaign_name:
            query = """
                SELECT member_id, member_first_name, member_last_name, member_email, campaign_name, csr_name, csr_phone_number
                FROM target_members_detail
                WHERE phone_number = %s AND member_id = %s AND campaign_name = %s
            """
            params = (phone_number, member_id, campaign_name)
        else:
            # Fallback for legacy calls (though we should avoid this)
            logging.warning("Loose lookup performed (missing member_id/campaign)")
            query = """
                SELECT member_id, member_first_name, member_last_name, member_email, campaign_name, csr_name, csr_phone_number
                FROM target_members_detail
                WHERE phone_number = %s
            """
            params = (phone_number,)

        cur.execute(query, params)
        result = cur.fetchone()
        
        if result:
            member_id, first_name, last_name, email, campaign, csr_name, csr_phone = result
            return {
                "member_id": member_id,
                "first_name": first_name,
                "last_name": last_name,
                "name": f"{first_name} {last_name}",
                "email": email,
                "campaign_name": campaign,
                "csr_name": csr_name,
                "csr_phone": csr_phone
            }
        return None
    except Exception as e:
        logging.error(f"DB Lookup Error: {e}")
        return None
    finally:
        if conn:
            conn.close()

def lookup_member_info(phone_number: str, tool_context: ToolContext):
    """Fetches member data. Note: Tool usage typically lacks extra context, so this might rely on loose lookup or state."""
    logging.info(f"lookup_member_info called with phone: {phone_number}")
    
    # Try to extract extra context from state if available
    member_id = tool_context.state.get("member_id")
    campaign_name = tool_context.state.get("campaign_name")
    
    data = _get_member_data(phone_number, member_id, campaign_name)
    
    if data:
        tool_context.state["member_name"] = data["name"]
        tool_context.state["first_name"] = data["first_name"]
        tool_context.state["email"] = data["email"]
        tool_context.state["campaign_name"] = data["campaign_name"]
        tool_context.state["csr_name"] = data["csr_name"]
        
        logging.info(f"Member found: {data['name']}, Campaign: {data['campaign_name']}")
        return {"status": "success", "message": "Member data loaded into state."}
    else:
        logging.warning(f"No member found for phone: {phone_number}")
        tool_context.state["member_name"] = "Valued Member"
        tool_context.state["is_data_missing"] = True
        return {"status": "error", "message": "No member found."}

def trigger_dtmf_payment(amount: float, tool_context: ToolContext):
    """Signals the system to hijack the audio for secure PCI card entry."""
    tool_context.state["handoff_active"] = True
    tool_context.state["payment_amount"] = amount
    return {"instruction": "REDIRECT_TO_SECURE_PAY", "amount": amount}

def send_payment_plan_email(tool_context: ToolContext):
    """Sends the 4-equal-payment plan details via email."""
    balance = tool_context.state.get("balance", 0)
    email_addr = tool_context.state.get("email")

    logging.info(f"send_payment_plan_email called with balance: {balance}, email: {email_addr}")
    
    if not balance or not email_addr:
        return "Error: Missing balance or email information."

    installment = balance / 4
    
    # SMTP Configuration
    smtp_server = os.environ.get("SMTP_SERVER", "smtp.gmail.com")
    smtp_port = int(os.environ.get("SMTP_PORT", 587))
    smtp_user = os.environ.get("SMTP_USER")
    smtp_password = os.environ.get("SMTP_PASSWORD")

    if not all([smtp_user, smtp_password]):
        logging.error("SMTP credentials missing.")
        return "Error: System email configuration is missing."

    try:
        # Create message
        msg = MIMEMultipart()
        msg['From'] = smtp_user
        msg['To'] = email_addr
        msg['Subject'] = "Your Payment Plan Details"

        body = f"""
        Dear Customer,

        As agreed, here are the details of your payment plan:
        
        Total Balance: ${balance:.2f}
        Number of Installments: 4
        Amount per Installment: ${installment:.2f}
        
        Please contact us if you have any questions.

        Sincerely,
        Debt Collection Team
        """
        msg.attach(MIMEText(body, 'plain'))

        # Send email
        with smtplib.SMTP(smtp_server, smtp_port) as server:
            server.starttls()
            server.login(smtp_user, smtp_password)
            server.send_message(msg)

        logging.info(f"Payment Plan Email successfully sent to {email_addr}")
        
        # Update State explicitly so Agent knows
        tool_context.state["is_email"] = True
        
        return f"Success. I have sent the details for 4 payments of ${installment:.2f} to {email_addr}."

    except Exception as e:
        logging.error(f"Failed to send email: {e}")
        return f"Error sending email: {str(e)}"

   

def confirm_verification(user_name: str, tool_context: ToolContext):
    """Marks the user's identity as successfully verified in the system.
    
    Args:
        user_name: The name of the user being verified (used for logging).
    """
    logging.info(f"confirm_verification tool called for {user_name}. Setting is_verified=True.")
    tool_context.state["is_verified"] = True
    return "Verification confirmed. Proceeding to collection."

from twilio.rest import Client
import os

# Initialize Twilio (removed global init to prevent import crashes)
# client = Client(account_sid, auth_token)

def _get_twilio_client():
    account_sid = os.environ.get('TWILIO_ACCOUNT_SID')
    auth_token = os.environ.get('TWILIO_AUTH_TOKEN')
    if not account_sid or not auth_token:
        logging.error("Twilio credentials missing in environment.")
        return None
    return Client(account_sid, auth_token)

def transfer_to_human(tool_context: ToolContext, human_number: str = "+18885550199"):
    """Initiates a warm transfer to a human agent.
    
    Args:
        human_number: The phone number of the human agent (E.164 format). 
                      Defaults to the main support line if not specified.
    """
    try:
        client = _get_twilio_client()
        if not client:
             return {"status": "error", "message": "Twilio configuration missing."}

        # Use explicitly stored call_sid from state
        call_sid = tool_context.state.get("call_sid")
        
        logging.info(f"Initiating transfer for Call SID: {call_sid} to {human_number}")

        if not call_sid:
             logging.error("No Call SID found in state. Cannot transfer.")
             return {"status": "error", "message": "Call context missing."}

        # We initiate the transfer
        # Note: In a real warm transfer, we often dial the agent, wait for answer, then bridge.
        # This implementation does a 'conference' add or similar logic depending on the platform.
        # Assuming we are adding a participant to the conference:
        
        # Build dynamic callback URL
        domain = os.environ.get("DOMAIN", "").replace("http://", "https://") 
        # Ensure https 
        if "https://" not in domain:
            domain = f"https://{domain}"
            
        callback_url = f"{domain}/twilio/transfer-status?parent_call_sid={call_sid}"
        
        participant = client.conferences(call_sid).participants.create(
            from_=os.environ.get('TWILIO_NUMBER', '+15551234567'), # Fallback for safety
            to=human_number,
            early_media=True,
            # This URL is hit by Twilio when the agent actually picks up
            status_callback=callback_url,
            status_callback_event=['answered'] 
        )
        
        # KEY FIX: Do NOT set is_agent_joined = True yet. 
        # The external webhook must set this when the agent actually answers.
        # This allows the "Small Talk" bridge phase to happen.
        
        return {
            "status": "initiated",
            "is_agent_joined": False, 
            "message": "Dialing colleague... bridge is active."
        }
    
    except Exception as e:
        logging.error(f"Transfer failed: {e}")
        return {"status": "error", "is_agent_joined": False, "message": str(e)}

def end_call(tool_context: ToolContext):
    """Terminates the AI agent's participation in the call (stops listening/speaking).
    
    does NOT hang up the phone line, to ensure the customer stays connected 
    to the human agent in the conference.
    """
    try:
        call_sid = tool_context.state.get("call_sid")
        logging.info(f"MetnaAgent leaving conversation for Call SID: {call_sid}")
        
        # update state ensuring loop breaks so the AI stops processing audio
        tool_context.state["call_ended"] = True
        
        return "MetnaAgent session ended. Goodbye."
    except Exception as e:
        logging.error(f"Failed to end agent session: {e}")
        tool_context.state["call_ended"] = True
        return "MetnaAgent session ended with error."
