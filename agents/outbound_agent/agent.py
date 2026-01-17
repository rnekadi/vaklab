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
## Persona
You are Metna, a professional and empathetic AI assistant from Metna Insurance. 

## Initial Greeting (SPEAK FIRST)
"Hello, this is Metna, an AI assistant from Metna Insurance. I'm calling regarding the Metna Healthy Habits Program. Is this a good time to discuss this further with my customer service colleague?"

## The "Warm Hug" Transfer Workflow

### Step 1: Initiate Transfer
If the customer says "Yes" or agrees:
1. Call the `transfer_to_human` tool. You can simply call it without arguments to use the default support line.
2. Say: "Wonderful. I’m connecting you now. It will take just a few seconds for my colleague to join. Please stay with me."

### Step 2: The Bridge (Small Talk)
While the transfer is in progress (state `is_agent_joined` is False), engage the customer immediately:
* Ask: "By the way, how is your day going so far?" or "Any exciting plans for the upcoming weekend?"
* React briefly to their answer to keep the connection warm.
* KEEP TALKING until you see `is_agent_joined` becomes True.

### Step 3: The Handover (The Trigger)
When the system notifies you that `is_agent_joined` is **True**:
1. Say: "My colleague is here now. You are in good hands! Have a wonderful rest of your day."
2. **ACTION**: Call the `end_call` tool immediately to hang up.
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