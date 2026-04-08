import re
import html
import requests
import streamlit as st
from datetime import datetime, timedelta

API_BASE = "http://localhost:8000"

st.set_page_config(page_title="ExecAI", page_icon="🤖", layout="centered")

# Minimal CSS — only things Streamlit can't do natively
st.markdown("""
<style>
    .block-container { padding-top: 2rem; padding-bottom: 2rem; }
    .empty-hint { color: #9ca3af; font-size: 0.9rem; line-height: 2; }
</style>
""", unsafe_allow_html=True)


# ── Session state ──────────────────────────────────────────────────────────
for k, v in [("messages", []), ("debug_last", {}), ("google_auth_url", None),
             ("outlook_auth_url", None), ("google_status", False),
             ("outlook_status", False), ("provider", "google")]:
    if k not in st.session_state:
        st.session_state[k] = v


# ── Utilities ──────────────────────────────────────────────────────────────
def prov():     return st.session_state.provider
def prov_name(p=None): p = p or prov(); return "Outlook" if p == "outlook" else "Google"

def fmt(value: str) -> str:
    if not value: return "—"
    try:
        from zoneinfo import ZoneInfo
        return datetime.fromisoformat(value).astimezone(ZoneInfo("America/New_York")).strftime("%a %b %d · %I:%M %p %Z")
    except Exception:
        return value

def clean_body(body: str, max_chars=2000) -> str:
    if not body: return ""
    t = html.unescape(body)
    for pat in [r"<script.*?>.*?</script>", r"<style.*?>.*?</style>", r"<!--.*?-->",
                r"<xml.*?>.*?</xml>", r"<o:.*?>.*?</o:.*?>", r"<v:.*?>.*?</v:.*?>"]:
        t = re.sub(pat, " ", t, flags=re.IGNORECASE | re.DOTALL)
    t = re.sub(r"<[^>]+>", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t[:max_chars] + ("…" if len(t) > max_chars else "")

def push(decision, result):
    st.session_state.messages.append(
        {"role": "assistant", "decision": decision, "result": result})

def check_status():
    try:
        d = requests.get(f"{API_BASE}/integrations/status", timeout=8).json()
        st.session_state.google_status  = bool(d.get("google_connected"))
        st.session_state.outlook_status = bool(d.get("outlook_connected"))
    except Exception:
        st.session_state.google_status = st.session_state.outlook_status = False


# ✅ NEW: Build context from last assistant message for follow-ups
def _build_last_context():
    """Extract the last assistant action + result for multi-turn context."""
    for msg in reversed(st.session_state.messages):
        if msg.get("role") == "assistant":
            decision = msg.get("decision") or {}
            result = msg.get("result") or {}
            action = decision.get("action") or ""
            if action:
                return {"action": action, "decision": decision, "result": result}
    return None


def run_prompt(prompt: str):
    if not prompt.strip(): return
    st.session_state.messages.append({"role": "user", "content": prompt})
    try:
        # ✅ NEW: send last_context for follow-up detection
        res = requests.post(f"{API_BASE}/assistant",
                            json={"text": prompt, "provider": prov(),
                                  "last_context": _build_last_context()}, timeout=25)
        res.raise_for_status()
        d           = res.json()
        intent_data = d.get("intent_data", {})
        decision    = d.get("decision", {})
        result      = d.get("result")
        st.session_state.debug_last = {"intent_data": intent_data, "decision": decision, "result": result}
        if (intent_data.get("intent") or "").strip() == "email_drafting" and result is None:
            try:
                ent = intent_data.get("entities") or {}
                r2  = requests.post(f"{API_BASE}/draft-email",
                                    json={"recipient": ent.get("recipient"), "topic": ent.get("topic"),
                                          "tone": ent.get("tone", "professional"), "original_text": prompt},
                                    timeout=10)
                r2.raise_for_status(); result = r2.json()
            except Exception as e:
                result = {"status": "error", "detail": str(e)}
        push(decision, result)
    except Exception as e:
        push({"message": "Backend error"}, {"status": "error", "detail": str(e)})

def book(title, start, duration_min, attendees=None, pending_reply=None, pending_draft=None):
    attendees = attendees or []
    p = prov()
    try:
        res = requests.post(f"{API_BASE}/integrations/{p}/create-event",
                            json={"title": title, "start": start, "duration_min": int(duration_min),
                                  "attendees": attendees, "description": "", "send_notifications": True},
                            timeout=25)
        res.raise_for_status()
        result = res.json()
        reply_result = None
        draft_result = None

        # handle pending reply (reply_and_create_event flow)
        if pending_reply:
            try:
                body = pending_reply.get("body", "")
                if start:
                    try:
                        from zoneinfo import ZoneInfo
                        ts = datetime.fromisoformat(start).astimezone(ZoneInfo("America/New_York")).strftime("%I:%M %p").lstrip("0")
                        body = re.sub(r'\b\d{1,2}(?::\d{2})?\s*(?:am|pm)\b', ts, body, flags=re.IGNORECASE)
                    except Exception: pass
                r2 = requests.post(f"{API_BASE}/integrations/{p}/create-reply-draft",
                                   json={"to": pending_reply.get("to", ""),
                                         "subject": pending_reply.get("subject", ""),
                                         "body": body, "thread_id": pending_reply.get("thread_id", "")},
                                   timeout=15)
                r2.raise_for_status(); reply_result = r2.json()
            except Exception: pass

        # ✅ NEW: handle pending draft (draft_and_create_event flow)
        if pending_draft:
            try:
                body = pending_draft.get("body", "")
                # update body with the chosen time
                if start:
                    try:
                        from zoneinfo import ZoneInfo
                        ts = datetime.fromisoformat(start).astimezone(ZoneInfo("America/New_York")).strftime("%I:%M %p").lstrip("0")
                        body = re.sub(r'\b\d{1,2}(?::\d{2})?\s*(?:am|pm)\b', ts, body, flags=re.IGNORECASE)
                        # if no time in body, append it
                        if ts.lower() not in body.lower():
                            body += f"\n\nThe meeting is scheduled for {ts}."
                    except Exception: pass
                r3 = requests.post(f"{API_BASE}/integrations/{p}/create-draft",
                                   json={"to": pending_draft.get("to", ""),
                                         "subject": pending_draft.get("subject", ""),
                                         "body": body},
                                   timeout=15)
                r3.raise_for_status(); draft_result = r3.json()
            except Exception: pass

        if reply_result:
            push({"action": "reply_and_create_event"},
                 {"status": "success", "reply": reply_result, "calendar": result,
                  "message": "Reply draft and event created."})
        elif draft_result:
            push({"action": "draft_email_and_create_event"},
                 {"status": "success", "draft": draft_result, "calendar": result,
                  "message": "Email draft and event created."})
        else:
            push({"action": "create_event", "provider": p, "title": title,
                  "start": start, "duration_min": int(duration_min),
                  "attendee_emails": attendees}, result)
        st.rerun()
    except Exception as e:
        push({"action": "create_event"}, {"status": "error", "detail": str(e)}); st.rerun()


# ── Card components (all native Streamlit) ─────────────────────────────────

def event_card(e: dict, provider: str = None):
    with st.container(border=True):
        col1, col2 = st.columns([3, 1])
        col1.markdown(f"**{e.get('title') or '(No title)'}**")
        col2.caption(prov_name(provider))
        st.caption(f"🕐 {fmt(e.get('start',''))}  →  {fmt(e.get('end',''))}")
        if e.get("htmlLink"):
            st.markdown(f"[Open in {prov_name(provider)} ↗]({e['htmlLink']})")


def event_card_with_delete(event: dict, provider: str):
    event_id = event.get("id")
    del_key  = f"del_{event_id}"
    if st.session_state.get(del_key):
        st.success("Event deleted.")
        return
    with st.container(border=True):
        col1, col2 = st.columns([3, 1])
        col1.markdown(f"**{event.get('title', '(No title)')}**")
        col2.caption(prov_name(provider))
        st.caption(f"🕐 {fmt(event.get('start',''))}  →  {fmt(event.get('end',''))}")
        attendees = event.get("attendees") or []
        if attendees:
            st.caption("👥 " + "  ·  ".join(a.get("email", "") for a in attendees))
        if event.get("htmlLink"):
            st.markdown(f"[Open in {prov_name(provider)} ↗]({event['htmlLink']})")
        if event_id and st.button("🗑 Delete", key=f"delbtn_{event_id}", type="secondary"):
            try:
                requests.delete(f"{API_BASE}/integrations/{provider}/events/{event_id}",
                                timeout=15).raise_for_status()
                st.session_state[del_key] = True; st.rerun()
            except Exception as e:
                st.error(f"Could not delete: {e}")


def email_card(em: dict, provider: str):
    with st.container(border=True):
        col1, col2 = st.columns([3, 1])
        col1.markdown(f"**{em.get('subject') or '(No subject)'}**")
        col2.caption(prov_name(provider))
        st.caption(f"From: {em.get('from','—')}")
        if em.get("date"): st.caption(em["date"])
        if em.get("snippet"): st.markdown(f"_{em['snippet']}_")


def draft_card(label: str, em: dict, draft: dict, sent_key: str, send_url: str):
    with st.container(border=True):
        col1, col2 = st.columns([3, 1])
        col1.markdown(f"**{em.get('subject', '(No subject)')}**")
        col2.caption(label)
        st.caption(f"To: {em.get('to','—')}")
        if st.session_state.get(sent_key):
            st.text_area("Body", value=em.get("body", ""), height=130,
                         disabled=True, key=f"sb_{draft.get('id','x')}")
            st.success("✅ Sent!")
        else:
            edited = st.text_area("Edit before sending", value=em.get("body", ""),
                                  height=130, key=f"eb_{draft.get('id','x')}")
            if st.button("📤 Send", key=f"send_{draft.get('id','x')}", type="primary"):
                try:
                    requests.post(send_url,
                                  json={"to": em.get("to", ""), "subject": em.get("subject", ""),
                                        "body": edited, "thread_id": draft.get("threadId")},
                                  timeout=15).raise_for_status()
                    st.session_state[sent_key] = True; st.rerun()
                except Exception as e:
                    st.error(f"Failed: {e}")


def time_option_card(label: str, start_raw: str, dur: int, attendees: list,
                     btn_key: str, title: str, pending_reply=None, pending_draft=None):
    with st.container(border=True):
        col1, col2 = st.columns([3, 1])
        col1.markdown(f"**{label}**")
        col2.caption(prov_name())
        st.caption(f"🕐 {fmt(start_raw)}  ·  {dur} min")
        if attendees: st.caption("👥 " + "  ·  ".join(attendees))
        if pending_reply: st.caption("_Will also create the reply draft._")
        if pending_draft: st.caption("_Will also create the email draft._")
        if st.button("Book this time", key=btn_key, use_container_width=True, type="primary"):
            book(title=title, start=start_raw, duration_min=int(dur),
                 attendees=attendees, pending_reply=pending_reply, pending_draft=pending_draft)


def custom_time_picker(title: str, duration: int, attendees: list,
                       key_prefix: str, pending_reply=None, pending_draft=None):
    with st.container(border=True):
        st.markdown("**Pick a custom time**")
        c1, c2 = st.columns(2)
        date_ = c1.date_input("Date", key=f"{key_prefix}_date")
        time_ = c2.time_input("Time", key=f"{key_prefix}_time", value=None)
        if pending_reply: st.caption("_Will also create the reply draft._")
        if pending_draft: st.caption("_Will also create the email draft._")
        if st.button("Book", key=f"{key_prefix}_book", use_container_width=True):
            if date_ and time_:
                from zoneinfo import ZoneInfo
                tz = ZoneInfo("America/New_York")
                combined = datetime(date_.year, date_.month, date_.day,
                                    time_.hour, time_.minute, tzinfo=tz)
                book(title=title, start=combined.isoformat(), duration_min=int(duration),
                     attendees=attendees, pending_reply=pending_reply, pending_draft=pending_draft)
            else:
                st.warning("Select both date and time.")


# ── Render functions ───────────────────────────────────────────────────────

def render_event_list(result: dict):
    events = result.get("events") or []
    p      = result.get("provider", "google")
    st.markdown(f"##### 📅 Upcoming — {prov_name(p)}")
    if not events: st.info("No events found."); return
    for e in events:
        event_card(e, p)


def render_created_event(result: dict):
    event = result.get("event") or {}
    p     = result.get("provider", prov())
    st.success("✅ Event created!")
    event_card_with_delete(event, p)


def render_pending_event(result: dict):
    p         = result.get("provider", prov())
    title     = result.get("title", "(No title)")
    start     = result.get("start", "")
    dur       = result.get("duration_min", 30)
    attendees = result.get("attendee_emails") or []
    st.markdown("##### 📋 Confirm event")
    with st.container(border=True):
        st.markdown(f"**{title}**")
        st.caption(f"🕐 {fmt(start)}  ·  {dur} min  ·  {prov_name(p)}")
        if attendees: st.caption("👥 " + "  ·  ".join(attendees))
        c1, c2 = st.columns(2)
        h = abs(hash(str(result)))
        if c1.button("✅ Confirm & Create", key=f"conf_{h}", use_container_width=True, type="primary"):
            book(title=title, start=start, duration_min=int(dur), attendees=attendees)
        if c2.button("Cancel", key=f"canc_{h}", use_container_width=True):
            st.info("Cancelled.")


def render_email_list(result: dict):
    emails = result.get("emails") or []
    p      = result.get("provider", "google")
    st.markdown(f"##### 📧 Inbox — {prov_name(p)}")
    if not emails: st.info("No emails found."); return
    for em in emails:
        email_card(em, p)


def render_read_email(result: dict):
    em   = result.get("email") or {}
    p    = result.get("provider", "google")
    st.markdown(f"##### 📩 {em.get('subject','(No subject)')}")
    with st.container(border=True):
        st.caption(f"From: {em.get('from','—')}  ·  {em.get('date','')}")
        if em.get("snippet"): st.markdown(f"_{em['snippet']}_")
        st.text_area("Body", value=clean_body(em.get("body","")),
                     height=260, disabled=True, key=f"body_{em.get('id','x')}")


def render_created_draft(result: dict):
    draft = result.get("draft") or {}
    em    = result.get("email") or {}
    p     = prov()
    draft_card(
        label=f"{'Outlook' if p=='outlook' else 'Gmail'} Draft",
        em=em, draft=draft,
        sent_key=f"sent_d_{draft.get('id','x')}",
        send_url=f"{API_BASE}/integrations/{p}/send-email",
    )


def render_reply_draft(result: dict):
    draft = result.get("draft") or {}
    em    = result.get("email") or {}
    p     = prov()
    draft_card(
        label=f"{'Outlook' if p=='outlook' else 'Gmail'} Reply",
        em=em, draft=draft,
        sent_key=f"sent_r_{draft.get('id','x')}",
        send_url=f"{API_BASE}/integrations/{p}/send-email",
    )


def render_mock_draft(result: dict):
    em = result.get("email") or {}
    with st.container(border=True):
        st.markdown(f"**{em.get('subject','(No subject)')}**")
        st.caption(f"To: {em.get('to','—')}")
        st.text_area("Body", value=em.get("body",""), height=160, disabled=True, key="mock_body")


def render_conflicts(conflicts: list, message: str = "Conflict detected."):
    st.warning(f"⚠️ {message}")
    for c in conflicts:
        with st.container(border=True):
            st.markdown(f"**{c.get('title','Busy')}**")
            st.caption(f"🕐 {fmt(c.get('start',''))}  →  {fmt(c.get('end',''))}")


def render_proposed_event(proposed: dict):
    if not proposed: return
    with st.container(border=True):
        st.caption("Proposed time")
        st.markdown(f"**{proposed.get('title','(No title)')}**")
        st.caption(f"🕐 {fmt(proposed.get('start',''))}  ·  {proposed.get('duration_min',30)} min")


def render_alternatives(alternatives: list, proposed_event: dict = None,
                        key_prefix: str = "alt", pending_reply=None, pending_draft=None):
    proposed_event = proposed_event or {}
    title     = proposed_event.get("title") or "Meeting"
    attendees = proposed_event.get("attendee_emails") or []
    duration  = proposed_event.get("duration_min", 30)

    if alternatives:
        st.markdown("##### 🕒 Alternative times")
        for idx, alt in enumerate(alternatives):
            time_option_card(
                label=alt.get("label", f"Option {idx+1}"),
                start_raw=alt.get("start", ""),
                dur=alt.get("duration_min", duration),
                attendees=attendees,
                btn_key=f"{key_prefix}_{idx}_{alt.get('start','')}",
                title=title,
                pending_reply=pending_reply,
                pending_draft=pending_draft,
            )

    custom_time_picker(title=title, duration=duration, attendees=attendees,
                       key_prefix=key_prefix, pending_reply=pending_reply, pending_draft=pending_draft)


def render_meeting_options(decision: dict, result: dict, key_prefix: str = "sug"):
    options   = result.get("options") or decision.get("options") or []
    title     = result.get("title") or decision.get("title") or "Meeting"
    if title == "ExecAI Event": title = "Meeting"
    attendees = result.get("attendee_emails") or decision.get("attendee_emails") or []
    busy_disp = result.get("busy_display") or decision.get("busy_display") or []

    if not options:
        st.info(result.get("message") or "No options found."); return

    st.markdown("##### 🗓 Suggested times")
    if busy_disp: st.caption("Already busy: " + "  ·  ".join(busy_disp))

    for idx, opt in enumerate(options):
        time_option_card(
            label=opt.get("label", f"Option {idx+1}"),
            start_raw=opt.get("start", ""),
            dur=opt.get("duration_min", result.get("duration_min", 30)),
            attendees=attendees,
            btn_key=f"{key_prefix}_{idx}_{opt.get('start','')}_{title}",
            title=title,
        )


def render_needs_clarification(result: dict):
    st.warning(result.get("message", "I need a bit more information."))
    missing = result.get("missing") or []
    suggestions = result.get("suggestions") or []

    # ✅ NEW: show clickable buttons for contact suggestions
    if suggestions and "recipient" in str(missing):
        for i, contact in enumerate(suggestions):
            name = contact.get("name", "")
            email = contact.get("email", "")
            label = f"📧 {name} — {email}" if name and name != email else f"📧 {email}"
            btn_key = f"contact_{i}_{email}_{abs(hash(str(result)))}"
            if st.button(label, key=btn_key, use_container_width=True):
                # send just the email — follow-up handler will use it
                run_prompt(email)
                st.rerun()
    elif missing:
        st.caption("Missing: " + ", ".join(missing))

    if result.get("example") and not suggestions:
        st.caption(f"Example: `{result['example']}`")


def render_generic(decision: dict):
    msg = decision.get("message") or "Done."
    if msg == "I'm not sure how to help with that yet.":
        st.info("I didn't understand that. Try something like:\n\n"
                "- *show my latest emails*\n"
                "- *create a meeting tomorrow at 3pm*\n"
                "- *draft an email to john@example.com about the budget*")
    else:
        st.info(msg)


def render_reply_and_create(result: dict):
    s = result.get("status")
    if s == "success":
        st.success(result.get("message", "Done."))
        if result.get("reply"):   render_reply_draft(result["reply"])
        if (result.get("calendar") or {}).get("status") == "created":
            render_created_event(result["calendar"])
        return
    if s == "partial_success":
        if result.get("reply"): render_reply_draft(result["reply"])
        cal = result.get("calendar") or {}
        pending_reply = result.get("pending_reply")
        cs = cal.get("status")
        if cs == "conflict_detected":
            render_conflicts(cal.get("conflicts") or [], cal.get("message","Conflict detected."))
            render_proposed_event(cal.get("proposed_event") or {})
            render_alternatives(cal.get("alternatives") or [], cal.get("proposed_event") or {},
                                key_prefix="r_alt", pending_reply=pending_reply)
        elif cs == "needs_clarification": render_needs_clarification(cal)
        elif cs == "created":             render_created_event(cal)
        else: st.warning(result.get("message","Partial success."))
        return
    if s == "not_found":          st.warning(result.get("message","Nothing found.")); return
    if s == "needs_clarification": render_needs_clarification(result); return
    render_generic({"message": result.get("message","Done.")})


def render_draft_and_create(result: dict):
    s = result.get("status")
    if s == "success":
        st.success(result.get("message","Done."))
        if result.get("draft"):   render_created_draft(result["draft"])
        if (result.get("calendar") or {}).get("status") == "created":
            render_created_event(result["calendar"])
        return
    if s == "partial_success":
        if result.get("draft"): render_created_draft(result["draft"])
        cal = result.get("calendar") or {}
        # ✅ FIX: get pending_draft for conflict flow
        pending_draft = result.get("pending_draft")
        cs  = cal.get("status")
        if cs == "conflict_detected":
            render_conflicts(cal.get("conflicts") or [])
            render_proposed_event(cal.get("proposed_event") or {})
            render_alternatives(cal.get("alternatives") or [], cal.get("proposed_event") or {},
                                key_prefix="d_alt", pending_draft=pending_draft)
        elif cs == "needs_clarification": render_needs_clarification(cal)
        elif cs == "created":             render_created_event(cal)
        return
    render_generic({"message": result.get("message","Done.")})


def render_result(decision: dict, result, idx: int = 0):
    action = (decision or {}).get("action") or ""
    if isinstance(result, dict):
        s = result.get("status")
        if s == "error":               st.error(result.get("detail","Error")); return
        if s == "needs_clarification": render_needs_clarification(result); return
        if s == "not_found":           st.warning(result.get("message","Nothing found.")); return

    if action == "list_events":
        render_event_list(result); return

    if action == "create_event":
        if isinstance(result, dict):
            s = result.get("status")
            if s == "pending_confirmation": render_pending_event(result); return
            if s == "conflict_detected":
                render_conflicts(result.get("conflicts") or [], result.get("message",""))
                render_proposed_event(result.get("proposed_event") or {})
                render_alternatives(result.get("alternatives") or [], result.get("proposed_event") or {},
                                    key_prefix=f"c_{idx}"); return
            if s == "created": render_created_event(result); return
        if decision.get("has_conflicts"):
            proposed = {"title": decision.get("title"), "start": decision.get("start"),
                        "duration_min": decision.get("duration_min"),
                        "attendee_emails": decision.get("attendee_emails", [])}
            render_conflicts(decision.get("conflicts") or [])
            render_proposed_event(proposed)
            render_alternatives(decision.get("alternatives") or [], proposed, key_prefix=f"h_{idx}"); return
        render_generic(decision); return

    if action == "list_emails":                                render_email_list(result); return
    if action == "read_email" and "email" in (result or {}):   render_read_email(result); return
    if action in {"create_draft","draft_email"}:
        s = (result or {}).get("status")
        if s == "draft_created": render_created_draft(result); return
        if s == "drafted":       render_mock_draft(result); return
    if action == "reply_email" and (result or {}).get("status") == "reply_draft_created":
        render_reply_draft(result); return
    if action == "reply_and_create_event":        render_reply_and_create(result); return
    if action == "draft_email_and_create_event":  render_draft_and_create(result); return
    if action == "suggest_times":
        render_meeting_options(decision, result,
                               key_prefix=f"s_{idx}_{abs(hash(str(decision)+str(result)))}"); return
    render_generic(decision)


# ── Sidebar ────────────────────────────────────────────────────────────────
check_status()

with st.sidebar:
    st.markdown("## ExecAI")
    st.caption("AI executive assistant")
    st.divider()

    # Provider
    st.session_state.provider = st.radio(
        "**Provider**", ["google", "outlook"],
        format_func=lambda x: "📅 Google" if x == "google" else "📅 Outlook",
        horizontal=True,
        index=0 if st.session_state.provider == "google" else 1,
    )
    st.divider()

    # Connections
    st.markdown("**Connections**")
    g_icon = "🟢" if st.session_state.google_status  else "🔴"
    o_icon = "🟢" if st.session_state.outlook_status else "🔴"
    st.caption(f"{g_icon} Google   {o_icon} Outlook")

    if not st.session_state.google_status:
        if st.button("Connect Google", use_container_width=True):
            try:
                res = requests.get(f"{API_BASE}/integrations/google/auth-url",
                                   timeout=15, allow_redirects=False)
                url = res.headers.get("Location") if 300 <= res.status_code < 400 else res.json().get("auth_url")
                st.session_state.google_auth_url = url
            except Exception as e: st.error(str(e))
            st.rerun()
    if st.session_state.google_auth_url:
        st.markdown(f"[Authorize Google →]({st.session_state.google_auth_url})")

    if not st.session_state.outlook_status:
        if st.button("Connect Outlook", use_container_width=True):
            try:
                res = requests.get(f"{API_BASE}/integrations/outlook/auth-url",
                                   timeout=15, allow_redirects=False)
                url = res.headers.get("Location") if 300 <= res.status_code < 400 else res.json().get("auth_url")
                st.session_state.outlook_auth_url = url
            except Exception as e: st.error(str(e))
            st.rerun()
    if st.session_state.outlook_auth_url:
        st.markdown(f"[Authorize Outlook →]({st.session_state.outlook_auth_url})")

    st.divider()

    # Quick actions
    st.markdown("**Shortcuts**")
    shortcuts = [
        ("📧 Inbox",              "show my latest emails"),
        ("📅 Calendar",           "show my calendar for next week"),
        ("🗓 Find time tomorrow", "find a time to meet tomorrow"),
    ]
    for label, prompt in shortcuts:
        if st.button(label, use_container_width=True, key=f"sc_{label}"):
            run_prompt(prompt); st.rerun()

    st.divider()
    if st.button("🧹 Clear chat", use_container_width=True):
        st.session_state.messages = []
        st.session_state.debug_last = {}
        st.session_state.google_auth_url = None
        st.session_state.outlook_auth_url = None
        st.rerun()
    show_debug = st.toggle("Debug", value=False)


# ── Header ─────────────────────────────────────────────────────────────────
st.markdown("## ExecAI")

# ── Quick tools (collapsed) ────────────────────────────────────────────────
with st.expander("🛠 Quick tools", expanded=False):
    tc1, tc2, tc3 = st.columns(3)

    with tc1:
        st.caption(f"**Meetings** — {prov_name()}")
        if st.button("Load next 7 days", use_container_width=True, key="t_meet"):
            try:
                data = requests.post(f"{API_BASE}/integrations/{prov()}/list-events",
                                     json={"days": 7}, timeout=20).json()
                for e in (data.get("events") or [])[:5]:
                    st.markdown(f"**{e.get('title','?')}**  \n{fmt(e.get('start',''))}")
            except Exception as ex: st.error(str(ex))

    with tc2:
        st.caption(f"**Availability tomorrow** — {prov_name()}")
        if st.button("Check free time", use_container_width=True, key="t_free"):
            try:
                tmr = datetime.now() + timedelta(days=1)
                s   = tmr.replace(hour=9,  minute=0, second=0, microsecond=0).astimezone()
                e   = tmr.replace(hour=17, minute=0, second=0, microsecond=0).astimezone()
                data = requests.post(f"{API_BASE}/integrations/{prov()}/freebusy",
                                     json={"time_min": s.isoformat(), "time_max": e.isoformat(),
                                           "calendar_ids": ["primary"] if prov()=="google" else []},
                                     timeout=20).json()
                busy = data.get("busy_blocks") or []
                if busy:
                    for b in busy: st.caption(f"Busy: {fmt(b['start'])} → {fmt(b['end'])}")
                else:
                    st.success("Free 9 AM – 5 PM")
            except Exception as ex: st.error(str(ex))

    with tc3:
        st.caption("**Quick draft**")
        to_  = st.text_input("To",      key="qt_to",  placeholder="email@example.com",  label_visibility="collapsed")
        sub_ = st.text_input("Subject", key="qt_sub", placeholder="Subject",             label_visibility="collapsed")
        bod_ = st.text_area("Message",  key="qt_bod", placeholder="Message…", height=68, label_visibility="collapsed")
        if st.button("Create draft", use_container_width=True, key="t_draft"):
            try:
                # ✅ FIX: usa provider dinámico en vez de hardcoded "google"
                requests.post(f"{API_BASE}/integrations/{prov()}/create-draft",
                              json={"to": to_, "subject": sub_, "body": bod_},
                              timeout=20).raise_for_status()
                st.success("Draft created.")
            except Exception as ex: st.error(str(ex))

st.divider()


# ── Chat history ───────────────────────────────────────────────────────────
for idx, msg in enumerate(st.session_state.messages):
    with st.chat_message(msg["role"]):
        if msg["role"] == "user":
            st.markdown(msg["content"])
        else:
            render_result(msg.get("decision") or {}, msg.get("result"), idx=idx)

# ── Empty state ────────────────────────────────────────────────────────────
if not st.session_state.messages:
    st.markdown("""
<p class="empty-hint">
Try asking something like…<br>
• show my latest emails<br>
• read my latest email<br>
• reply to my latest email saying "Thanks for the update"<br>
• create a meeting with sarah@example.com tomorrow at 11am<br>
• draft an email to sarah@example.com about the proposal<br>
• find a time to meet tomorrow<br>
• show my calendar for next week
</p>""", unsafe_allow_html=True)

# ── Chat input ─────────────────────────────────────────────────────────────
prompt = st.chat_input("Ask ExecAI something…")
if prompt:
    st.session_state.messages.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)
    with st.chat_message("assistant"):
        with st.spinner("Thinking…"):
            try:
                # ✅ NEW: send last_context for follow-up detection
                res = requests.post(f"{API_BASE}/assistant",
                                    json={"text": prompt, "provider": prov(),
                                          "last_context": _build_last_context()}, timeout=25)
                res.raise_for_status()
                d           = res.json()
                intent_data = d.get("intent_data", {})
                decision    = d.get("decision", {})
                result      = d.get("result")
                st.session_state.debug_last = {"intent_data": intent_data,
                                               "decision": decision, "result": result}
                if (intent_data.get("intent") or "").strip() == "email_drafting" and result is None:
                    try:
                        ent = intent_data.get("entities") or {}
                        r2  = requests.post(f"{API_BASE}/draft-email",
                                            json={"recipient": ent.get("recipient"),
                                                  "topic": ent.get("topic"),
                                                  "tone": ent.get("tone","professional"),
                                                  "original_text": prompt}, timeout=10)
                        r2.raise_for_status(); result = r2.json()
                    except Exception as e:
                        result = {"status": "error", "detail": str(e)}
                new_idx = len(st.session_state.messages)
                render_result(decision, result, idx=new_idx)
                push(decision, result)
            except Exception as e:
                st.error(f"Backend error: {e}")
                push({"message": "Backend error"}, {"status": "error", "detail": str(e)})


# ── Debug ──────────────────────────────────────────────────────────────────
if show_debug:
    with st.expander("Debug", expanded=False):
        dbg = st.session_state.debug_last or {}
        for k, label in [("intent_data","Intent"), ("decision","Decision"), ("result","Result")]:
            if dbg.get(k): st.markdown(f"**{label}**"); st.json(dbg[k])