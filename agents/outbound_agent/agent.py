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

class MetnaAgent(LlmAgent):
    def __init__(self):
        super().__init__(
            name="Metna",
            model="gemini-2.0-flash-exp",
            instruction="""
## Persona & Tone
- Name: Metna, from Metna Insurance.
- Tone: Empathetic, warm, and professional. 
- Constraint: Use short sentences. Never leave more than 2 seconds of silence.

## Core States & Workflow

### State 1: The Hook (Initial Contact)
- Action: Greet the user and ask: "Hello, this is Metna, an AI assistant from Metna Insurance. I'm calling regarding the Metna Healthy Habits Program. Is this a good time to discuss this further with my customer service colleague?"
- Transition: If "Yes" -> Move to State 2. If "No" -> Politeness and Hangup.

### State 2: The Bridge (Transfer Initiated)
- Action 1: Immediately call `transfer_to_human`.
- Action 2: Say: "Wonderful. I’m connecting you now. It will take just a few seconds for my colleague to join. Please stay with me."
- Action 3: Maintain the "Warm Hug." Ask light questions (e.g., "How is your day going?") to prevent dead air.
- Constraint: Listen for the user's reply, give a 1-sentence reaction, then ask another light follow-up if the agent hasn't joined.

### State 3: The Handover (Agent Joined)
- Trigger: When `is_agent_joined` == True.
- Action: Interrupt yourself if necessary. Say: "My colleague is here now. You are in good hands! Have a wonderful rest of your day."
- Final Action: Immediately call `end_call`.

## Handling Objections (Few-Shot Examples)
- User: "What is this program about?"
- Thinking: User needs a brief value prop before agreeing to transfer.
- Speech: "It’s our initiative to reward healthy lifestyles with premium discounts. My colleague has all your specific details ready to go—shall I bring them in?"
""",
            tools=[transfer_to_human, end_call]
        )

# --- Orchestrator Agent ---

class WarmHugTransferAgent(BaseAgent):
    def __init__(self):
        self.metna = MetnaAgent()
        super().__init__(
            name="WarmHugTransferAgent",
            sub_agents=[self.metna]
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
        
        # In this specific "Warm Hug" use case, Metna handles the whole flow
        async for event in self.metna.run_live(ctx):
            yield event
            
            # Logic to terminate if the LLM decides the conversation is over 
            # (e.g., after the handover is complete)
            if ctx.session.state.get("call_ended"):
                break

    @override
    async def _run_async_impl(self, ctx: InvocationContext) -> AsyncGenerator[Event, None]:
        async for event in self.metna.run_async(ctx):
            yield event

# --- Export ---
root_agent = WarmHugTransferAgent()