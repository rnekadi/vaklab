import os
import logging
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime
from google.adk.tools import ToolContext
from google.adk.agents.invocation_context import InvocationContext
from utils.db import get_db_connection

def _get_member_data(phone_number: str):
    """Raw helper to fetch member data dict from DB."""
    conn = get_db_connection()
    if not conn:
        return None
    try:
        cur = conn.cursor()
        query = """
            SELECT member_id, member_first_name, member_last_name, member_email, campaign_name, csr_name, csr_phone_number
            FROM target_members_detail
            WHERE phone_number = %s
        """
        cur.execute(query, (phone_number,))
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
    """Fetches member data from the 'target_members_detail' table using phone number."""
    logging.info(f"lookup_member_info called with phone: {phone_number}")
    
    data = _get_member_data(phone_number)
    
    if data:
        # Populate state for the Agent to use (if called as a tool)
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

# Initialize Twilio
account_sid = os.environ['TWILIO_ACCOUNT_SID']
auth_token = os.environ['TWILIO_AUTH_TOKEN']
client = Client(account_sid, auth_token)

def transfer_to_human(ctx: InvocationContext, human_number: str = "+18885550199"):
    """Initiates a warm transfer to a human agent.
    
    Args:
        human_number: The phone number of the human agent (E.164 format). 
                      Defaults to the main support line if not specified.
    """
    try:
        # Client is already initialized globally as 'client', but for safety in tools we can ensure it exists
        # or use the one from global scope if available. Relying on global 'client'.
        
        call_sid = ctx.session.state.get("call_sid") or ctx.user_id
        
        logging.info(f"Initiating transfer for Call SID: {call_sid} to {human_number}")

        # We initiate the transfer
        # Note: In a real warm transfer, we often dial the agent, wait for answer, then bridge.
        # This implementation does a 'conference' add or similar logic depending on the platform.
        # Assuming we are adding a participant to the conference:
        participant = client.conferences(call_sid).participants.create(
            from_=os.environ.get('TWILIO_NUMBER', '+15551234567'), # Fallback for safety
            to=human_number,
            early_media=True,
            # This URL is hit by Twilio when the agent actually picks up
            status_callback="https://your-api.com/transfer-status",
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

def end_call(ctx: InvocationContext):
    """Terminates the AI agent's participation in the call (stops listening/speaking).
    
    does NOT hang up the phone line, to ensure the customer stays connected 
    to the human agent in the conference.
    """
    try:
        call_sid = ctx.session.state.get("call_sid") or ctx.user_id
        logging.info(f"MetnaAgent leaving conversation for Call SID: {call_sid}")
        
        # update state ensuring loop breaks so the AI stops processing audio
        ctx.session.state["call_ended"] = True
        
        return "MetnaAgent session ended. Goodbye."
    except Exception as e:
        logging.error(f"Failed to end agent session: {e}")
        ctx.session.state["call_ended"] = True
        return "MetnaAgent session ended with error."
