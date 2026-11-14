from .connection import get_connection_context
from .forms import (
    WhatsAppOtpForm,
    WhatsAppPhoneForm,
    WhatsAppPreferencesForm,
)
from .services import normalize_phone


def build_profile_context(
    request,
    *,
    phone_form=None,
    otp_form=None,
    preferences_form=None,
):
    connection_context = get_connection_context(request.user)
    active = connection_context["active"]
    if phone_form is None:
        initial_phone = normalize_phone(request.user.phone)
        phone_form = WhatsAppPhoneForm(
            initial={"phone": initial_phone or request.user.phone or ""}
        )
    if otp_form is None:
        otp_form = WhatsAppOtpForm()
    if preferences_form is None and active is not None:
        preferences_form = WhatsAppPreferencesForm(instance=active)

    return {
        "whatsapp": connection_context,
        "whatsapp_state": connection_context["state"],
        "whatsapp_active": active,
        "whatsapp_pending": connection_context["pending"],
        "whatsapp_approved": connection_context["approved"],
        "whatsapp_last": connection_context["last"],
        "whatsapp_phone_form": phone_form,
        "whatsapp_otp_form": otp_form,
        "whatsapp_preferences_form": preferences_form,
        "whatsapp_system_enabled": connection_context["system_enabled"],
    }
