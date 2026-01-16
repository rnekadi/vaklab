import logging
from typing import AsyncGenerator
from typing_extensions import override
from google.adk.agents import LlmAgent, BaseAgent
from google.adk.agents.invocation_context import InvocationContext
from google.adk.events import Event
from .tools import lookup_debtor_info, trigger_dtmf_payment, send_payment_plan_email, schedule_callback, confirm_verification, hang_up

# --- Configure Logging ---
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class VerifierAgent(LlmAgent):
    def __init__(self):
        # We pass the collector agent here so the Verifier knows where to go
        sub_agents = [collector_agent] if collector_agent else []
        super().__init__(
            name="Metna",
            model="gemini-2.0-flash-exp",
            sub_agents=sub_agents, # Register the collector as a sub-agent
            instruction="""## Persona
You are Metna, a professional and courteous AI assistant from Metna Insurance. Your tone is calm,helpful and requester.

## Initial Greeting (SPEAK FIRST)
Start with: "Hello, this is Metna, an AI assistant from Metna Insurance. I'm calling regarding a Metna Healthy Habits Programs teams. Is this right time to have discussion further with customer service colleague on same?"

## Goal: Identity Verification
You MUST verify the debtor's identity before handing off to the collector.

## Verification Logic
- **Required Info**: Full Name and Last 4 digits of SSN
- **Reference Values**: Name is `{debtor_name}`, SSN Last 4 is `{true_ssn_last4}`
- **Partial Info**: If they only give Name, ask for SSN. If they only give SSN, ask for Name.
- **Validation**: After they provide both pieces of information:
  - Compare their provided Name to `{debtor_name}` (case-insensitive)
  - Compare their provided SSN Last 4 to `{true_ssn_last4}` (exact match)
  - If BOTH match: Immediately call `confirm_verification` with the `{debtor_name}`
  - If they DON'T match: Say "I'm sorry, but the information you provided doesn't match our records. Let me try again. Can you please re-confirm your full name and the last four digits of your Social Security number?"
  
- **Why Verification?**: If asked why, explain: "By law, I must verify your identity to protect your privacy and ensure I am speaking with the correct person regarding a sensitive financial matter."
- **Who is this?**: If asked who is calling again, say: "This is Moojan, an AI assistant from McCombs Financial Services."

## Success Handoff
Once both name and SSN match the reference values:
1. Call `confirm_verification` passing the user's verified name
2. After the tool succeeds, say: "Thank you for verifying. I'm now connecting you with our collections team." 
3. Call `transfer_to_agent(agent_name="Collector")`..
""",
            tools=[confirm_verification]
        )

class CollectorAgent(LlmAgent):
    def __init__(self):
        super().__init__(
            name="Collector",
            model="gemini-2.0-flash-exp",
            instruction="""## Persona
You are Moojan, an empathetic and professional female AI assistant from McCombs Financial Services. Your tone is calm, firm, but helpful.

## Goal: Negotiation & Collection
The user is now verified. You have access to their account information and must discuss the outstanding balance of **${balance}**.

## Immediate Opening (if just_verified == TRUE)
Start with: "Thank you for verifying your identity. I can now discuss your account. You have an outstanding balance of ${balance} with McCombs Financial Services. Is there anything you'd like to discuss about this balance today?"

## Negotiation Strategy
1. **Initial Offer**: State the balance and ask for full payment today.
2. **If they can't pay in full or mention hardship**:
   - Check if `{payment_plan_qualified}` is TRUE
   - If **TRUE**: Offer the 4-equal-payment plan of **${installment}** per month for 4 months
     - Ask: "Would you like to set up a payment plan with 4 equal payments of ${installment}?"
     - If they agree and want to pay now via card: Call `trigger_dtmf_payment` with amount **${installment}**
     - If they agree but want details in writing: Call `send_payment_plan_email` and then say: "I have sent the payment plan details to your email. You'll receive 4 invoices for ${installment} each. Is there anything else you need?"
   - If **FALSE**: Politely say: "Unfortunately, a payment plan is not available at this time. What is the maximum you can pay toward this balance today?"

3. **Closure**: When ready to end the call, say: "Thank you for your time today. Have a nice day!" and call `hang_up` to terminate the call.

## Important Notes
- Be empathetic but firm about collection
- Always respect the user's circumstances
- Never threaten or use aggressive language
- Document any promises made
""",
            tools=[trigger_dtmf_payment, send_payment_plan_email, hang_up]
        )

class SchedulerAgent(LlmAgent):
    def __init__(self):
        super().__init__(
            name="Scheduler",
            model="gemini-2.0-flash-exp",
            instruction="""Goal: Schedule a callback.
Use the `schedule_callback` tool and confirm it with the user.
""",
            tools=[schedule_callback]
        )

class DebtCollectionAgent(BaseAgent):
    """
    Multi-Agent Debt Collector with Turn-Start Safety & Robust Transition.
    """
    verifier: VerifierAgent
    collector: CollectorAgent
    scheduler: SchedulerAgent

    def __init__(self, name: str = "DebtCollectionAgent"):
        v = VerifierAgent()
        c = CollectorAgent()
        s = SchedulerAgent()
        super().__init__(
            name=name, 
            verifier=v,
            collector=c,
            scheduler=s,
            sub_agents=[v, c, s]
        )

    def _ensure_state_safety(self, state):
        """Initializes all prompt variables to prevent KeyError crashes."""
        defaults = {
            "debtor_name": "UNKNOWN",
            "balance": 0.0,
            "installment": 0.0,
            "payment_plan_qualified": False,
            "true_ssn_last4": "0000",
            "is_verified": False,
            "call_ended": False,
            "is_email": False,
            "just_verified": False
        }
        for k, v in defaults.items():
            if k not in state:
                state[k] = v
        # Ensure installment is always updated if balance changes
        if state.get("balance"):
            state["installment"] = round(float(state["balance"]) / 4, 2)

    def _perform_lookup(self, ctx: InvocationContext):
        state = ctx.session.state
        if state.get("debtor_name") == "UNKNOWN":
            phone_number = state.get("phone_number") or ctx.user_id
            if not phone_number or phone_number == "user":
                lookup_num = "9135960926"
            else:
                lookup_num = phone_number.replace("+1", "").replace("+", "")
                if len(lookup_num) == 11 and lookup_num.startswith("1"):
                    lookup_num = lookup_num[1:]
            
            logger.info(f"Auto-preloading debtor info for {lookup_num}")
            lookup_debtor_info(lookup_num, tool_context=ctx.session)
            # Re-ensure safety to pick up new balance/installment
            self._ensure_state_safety(state)

    async def _run_live_impl(self, ctx: InvocationContext) -> AsyncGenerator[Event, None]:
        self._ensure_state_safety(ctx.session.state)
        self._perform_lookup(ctx)
        
        state = ctx.session.state

        while True:
            current_phase = "verified" if state.get("is_verified") else "unverified"
        
            if current_phase == "unverified":
                async for event in self.verifier.run_live(ctx):
                    yield event
                    # Detect if the agent itself requested a transfer
                if event.type == "transfer": 
                    break # This breaks the 'async for' and restarts the 'while True'
        else:
            async for event in self.collector.run_live(ctx):
                yield event
        if state.get("call_ended"):
            break

        # last_phase = None
        
        # while True:
        #     current_phase = "verified" if state.get("is_verified") else "unverified"
        #     if current_phase == last_phase:
        #         break
            
        #     # Transition detected within the same turn
        #     if last_phase == "unverified" and current_phase == "verified":
        #         state["just_verified"] = True
            
        #     last_phase = current_phase

        #     if current_phase == "unverified":
        #         async for event in self.verifier.run_live(ctx):
        #             yield event
        #     else:
        #         async for event in self.collector.run_live(ctx):
        #             yield event
        #         state["just_verified"] = False
            
        #     if state.get("call_ended"):
        #         break

    @override
    async def _run_async_impl(self, ctx: InvocationContext) -> AsyncGenerator[Event, None]:
        self._ensure_state_safety(ctx.session.state)
        self._perform_lookup(ctx)
        
        state = ctx.session.state
        last_phase = None
        
        while True:
            current_phase = "verified" if state.get("is_verified") else "unverified"
            if current_phase == last_phase:
                break
            
            if last_phase == "unverified" and current_phase == "verified":
                state["just_verified"] = True
                
            last_phase = current_phase

            if current_phase == "unverified":
                async for event in self.verifier.run_async(ctx):
                    yield event
            else:
                async for event in self.collector.run_async(ctx):
                    yield event
                state["just_verified"] = False
            
            if state.get("call_ended"):
                break

# Expose the agent instance
root_agent = DebtCollectionAgent()
