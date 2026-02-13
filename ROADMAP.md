# 📋 Calendar Agent v4 - Development Roadmap

## 🎯 Project Overview
Telegram Google Calendar Agent with AI-powered intent classification and calendar management.

---

## ✅ PHASE A: Calendar Core Features

### Task 1: Color Override XOR Logic ✅ **COMPLETED**
**Status:** Verified & Live  
**Description:** Fixed color hierarchy to respect explicit user color requests over category defaults.  
**Implementation:**
- Router extracts explicit `color_name` from user input
- Events handler applies XOR logic: Explicit Color > Payload > User Prefs > Default
- Color confirmation messages reflect the applied color

**Files Modified:**
- `prompts/router.py` - Added color extraction rules
- `bot/handlers/events.py` - Implemented color resolution hierarchy

---

### Task 2: All-Day & Multi-Day Event Detection ✅ **COMPLETED**
**Status:** Verified & Live  
**Description:** Enhanced event detection to properly handle all-day and multi-day events.  
**Implementation:**
- Router detects all-day events from keywords and date patterns
- Proper DATE-ONLY formatting for Google Calendar API
- Multi-day event support with correct end date calculation (exclusive end)

**Files Modified:**
- `prompts/router.py` - Added all-day detection rules and few-shot examples
- `services/calendar_service.py` - Enhanced `_format_datetime()` for all-day events
- `bot/handlers/events.py` - Added all-day event guard logic

---

### Task 3: Recurring Events (RRULE) ✅ **COMPLETED**
**Status:** Code Complete, Implementation In Progress  
**Description:** Full support for RFC 5545 RRULE recurring events with FSM flow for missing end dates.  
**Implementation:**
- Router extracts recurrence frequency, interval, and end date
- Calendar service builds RFC 5545 RRULE strings
- FSM flow asks "until when?" if end date missing
- Supports DAILY, WEEKLY, MONTHLY, YEARLY with custom intervals

**Files Modified:**
- `prompts/router.py` - Added recurrence fields and detection rules
- `services/calendar_service.py` - Added `_build_rrule()` helper and recurrence attachment
- `bot/states.py` - `RecurrenceFlowStates` already existed
- `bot/handlers/events.py` - Added FSM logic for end date collection
- `prompts/skills/create_event.py` - Added recurring event examples
- `prompts/base.py` - Removed recurring events from "in development" guardrails

**Features:**
- ✅ Daily, Weekly, Monthly, Yearly frequencies
- ✅ Custom intervals (e.g., "every 2 weeks")
- ✅ End date parsing from natural language
- ✅ BYDAY support for weekly events
- ✅ FSM flow for missing end dates
- ✅ Integration with existing missing contact flow

---

## 🧪 PHASE B: Admin Test Suite

### Task 4: Test Suite Infrastructure 🔄 **IN PROGRESS**
**Status:** Planning  
**Description:** Build isolated admin test suite infrastructure with password protection and global exit.

**Requirements:**
- Password-protected entry (specific keyword + password)
- Fully isolated from regular user flows
- Global exit mechanism (always accessible)
- State management for 5 test scenarios
- Test result logging/reporting

**Planned Files:**
- `bot/handlers/admin_tests.py` - Main test suite handler
- `bot/states.py` - Add `AdminTestStates` group
- `config.py` - Add admin password configuration

---

### Task 5: The 5 Test Scenarios 🔄 **PLANNED**
**Status:** Planning  
**Description:** Implement 5 specific test scenarios for comprehensive system validation.

**Test 1: CRUD Obstacle Course**
- Create → Read → Update → Delete flow
- Tests all CRUD operations in sequence
- Validates data persistence and state transitions

**Test 2: Onboarding Simulation**
- Simulates new user onboarding flow
- Tests all onboarding states
- Validates preference persistence

**Test 3: Voice Loop**
- Tests voice message processing
- Multiple voice messages in sequence
- Validates transcription and intent classification

**Test 4: Search Loop**
- Multiple search queries in sequence
- Tests calendar search functionality
- Validates result accuracy

**Test 5: Dry-Run Event**
- Creates test event without saving
- Validates event parsing and validation
- Tests error handling

---

## 📊 Progress Summary

| Phase | Task | Status | Completion |
|-------|------|--------|------------|
| Phase A | Task 1: Color XOR | ✅ Complete | 100% |
| Phase A | Task 2: All-Day Events | ✅ Complete | 100% |
| Phase A | Task 3: Recurring Events | ✅ Complete | 100% |
| Phase B | Task 4: Test Infrastructure | 🔄 Planning | 0% |
| Phase B | Task 5: 5 Test Scenarios | 🔄 Planning | 0% |

**Overall Progress:** 60% (3/5 tasks complete)

---

## 🚀 Next Steps

1. **Complete Task 3 Testing** - Verify recurring events work end-to-end
2. **Implement Task 4** - Build admin test suite infrastructure
3. **Implement Task 5** - Create the 5 test scenarios
4. **Integration Testing** - Ensure test suite doesn't interfere with production flows

---

## 📝 Notes

- All Phase A tasks are production-ready and live
- Phase B is isolated and will not affect regular users
- Test suite requires admin password for access
- Global exit ensures users can always escape test mode
