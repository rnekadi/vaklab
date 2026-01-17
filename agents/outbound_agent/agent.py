import logging
from typing import AsyncGenerator
from typing_extensions import override
from google.adk.agents import LlmAgent, BaseAgent
from google.adk.agents.invocation_context import InvocationContext
from google.adk.events import Event
from .tools import transfer_to_human, end_call

# --- Configure Logging ---
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


# --- Metna Agent (The LLM) ---

# --- Metna Agent (The LLM) ---

class MetnaAgent(LlmAgent):
    def __init__(self, member_data=None):
        # Defaults
        first_name = "Raju"
        campaign = "Healthy Habits Program"
        csr_name = "Kenny Abadge"
        
        if member_data:
            first_name = member_data.get("first_name", first_name)
            campaign = member_data.get("campaign_name", campaign)
            csr_name = member_data.get("csr_name", csr_name)

        base_instruction = """
## Persona & Tone
- Name: Metna, from Metna Insurance.
- Tone: Empathetic, warm, and professional. 
- Constraint: Use short sentences. Never leave more than 2 seconds of silence.

## Core States & Workflow

### State 1: The Hook (Initial Contact)
- Action: Greet the user and ask: "Hello {member_first_name}, this is Metna, an AI assistant from Metna Insurance. I'm calling regarding the {campaign_name}. Is this a good time to discuss this further with my customer service colleague?"
- Transition: If "Yes" -> Move to State 2. If "No" -> Politeness and Hangup.

### State 2: The Bridge (Transfer Initiated)
- Action 1: Immediately call `transfer_to_human`.
- Action 2: Say: "Wonderful. I’m connecting you now. It will take just a few seconds for my colleague to join. Please stay with me."
- Action 3: Maintain the "Warm Hug." Ask light questions (e.g., "How is your day going?") to prevent dead air.
- Constraint: Listen for the user's reply, give a 1-sentence reaction, then ask another light follow-up if the agent hasn't joined.

### State 3: The Handover (Agent Joined)
- Trigger: When `is_agent_joined` == True.
- Action: Interrupt yourself if necessary. Say: "My colleague {csr_name} is here now. You are in good hands! Have a wonderful rest of your day."
- Final Action: Immediately call `end_call`.

## Handling Objections (Few-Shot Examples)
- User: "What is this program about?"
- Thinking: User needs a brief value prop before agreeing to transfer.
- Speech: "It’s our initiative to reward healthy lifestyles with premium discounts. My colleague has all your specific details ready to go—shall I bring them in?"
"""
        # Format the prompt with specific user data
        formatted_instruction = base_instruction.format(
            member_first_name=first_name,
            campaign_name=campaign,
            csr_name=csr_name
        )

        super().__init__(
            name="Metna",
            model="gemini-2.0-flash-exp",
            instruction=formatted_instruction,
            tools=[transfer_to_human, end_call]
        )

# --- Orchestrator Agent ---

class WarmHugTransferAgent(BaseAgent):
    def __init__(self):
        # We don't instantiate Metna here anymore because we need it per-session
        super().__init__(
            name="WarmHugTransferAgent",
            sub_agents=[] # Dynamic
        )

    def _ensure_state_safety(self, state):
        defaults = {
            "is_verified": False,
            "call_ended": False,
            "call_sid": None 
        }
        for k, v in defaults.items():
            if k not in state:
                state[k] = v

    @override
    async def _run_live_impl(self, ctx: InvocationContext) -> AsyncGenerator[Event, None]:
        self._ensure_state_safety(ctx.session.state)
        
        # 1. Look up user data
        phone_number = ctx.user_id # Expecting phone number as user_id
        logging.info(f"Orchestrator starting for phone: {phone_number}")
        
        from .tools import _get_member_data
        
        # In routers/outbound_twillio.py, we stored 'member_id' and 'campaign' in the call/context
        # We need to ensure we can access them here. 
        # Typically, custom parameters from Twilio come in ctx.session.state via the websocket "customParameters"
        
        member_id = ctx.session.state.get("member_id")
        campaign_name = ctx.session.state.get("campaign")
        
        member_data = _get_member_data(phone_number, member_id, campaign_name)
        
        if member_data:
            logging.info(f"Found member: {member_data.get('first_name')}")
            # Inject into state just in case tools need it later
            for k, v in member_data.items():
                ctx.session.state[k] = v
        else:
            logging.warning("No member data found via DB lookup.")

        # 2. Instantiate Metna with context
        metna_agent = MetnaAgent(member_data=member_data)
        
        # 3. Run the tailored agent
        async for event in metna_agent.run_live(ctx):
            yield event
            
            # Logic to terminate if the LLM decides the conversation is over 
            # (e.g., after the handover is complete)
            if ctx.session.state.get("call_ended"):
                break

    @override
    async def _run_async_impl(self, ctx: InvocationContext) -> AsyncGenerator[Event, None]:
        # Logic similar to _run_live_impl but for async text chat
        self._ensure_state_safety(ctx.session.state)
        
        # 1. Lookup (Optional for text chat debug, but good to have)
        from .tools import _get_member_data
        
        phone_number = ctx.user_id 
        member_id = ctx.session.state.get("member_id")
        campaign_name = ctx.session.state.get("campaign")
        
        # Try lookup
        member_data = _get_member_data(phone_number, member_id, campaign_name)
        
        # 2. Instantiate
        metna_agent = MetnaAgent(member_data=member_data)
        
        # 3. Run
        async for event in metna_agent.run_async(ctx):
            yield event

# --- Export ---
root_agent = WarmHugTransferAgent()