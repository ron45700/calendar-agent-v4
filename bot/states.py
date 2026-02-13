"""
FSM States for Agentic Calendar 2.0
Defines state groups for multi-step flows like onboarding and event creation.
"""

from aiogram.fsm.state import State, StatesGroup


class OnboardingStates(StatesGroup):
    """
    States for the onboarding questionnaire flow.
    New users go through this after first OAuth authentication.
    
    Flow: Confirmation → Nickname → Agent Name → Gender → Colors → Complete
    """
    # Step 0: User confirms they want to do onboarding
    WAITING_FOR_CONFIRMATION = State()
    
    # Step 1: User enters their nickname
    WAITING_FOR_NICKNAME = State()
    
    # Step 2: User enters agent's nickname
    WAITING_FOR_AGENT_NAME = State()
    
    # Step 3: User selects gender (for Hebrew phrasing)
    WAITING_FOR_GENDER = State()
    
    # Step 4: User sets up reminders preference
    WAITING_FOR_REMINDERS = State()
    
    # Step 5: Daily check toggle
    WAITING_FOR_DAILY_CHECK = State()
    
    # Step 5b: Daily check time selection
    WAITING_FOR_DAILY_TIME = State()
    
    # Step 5c: Daily briefing toggle
    WAITING_FOR_DAILY_BRIEFING = State()
    
    # Step 6: User defines color preferences
    WAITING_FOR_COLORS = State()
    
    # Step 7: User adds contacts
    WAITING_FOR_CONTACTS = State()


class EventFlowStates(StatesGroup):
    """
    States for the event creation flow.
    Handles LLM parsing, missing contact resolution, and calendar creation.
    """
    # User needs to confirm the parsed event
    WAITING_FOR_EVENT_CONFIRMATION = State()
    
    # User needs to provide missing contact email
    WAITING_FOR_MISSING_CONTACT_EMAIL = State()


class SettingsStates(StatesGroup):
    """
    States for settings modification flow.
    (Placeholder for future implementation)
    """
    EDITING_NICKNAME = State()
    EDITING_GENDER = State()
    EDITING_AGENT_NAME = State()
    EDITING_PREFERENCES = State()
    EDITING_COLORS = State()
    EDITING_CONTACTS = State()


class DeleteFlowStates(StatesGroup):
    """
    States for the event deletion confirmation flow.
    Requires explicit user confirmation before deleting.
    
    Flow: User requests delete → Bot shows event + asks "בטוח?" → User confirms/cancels
    """
    # User must confirm or cancel the pending deletion
    WAITING_FOR_DELETE_CONFIRM = State()
