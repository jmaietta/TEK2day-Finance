"""Who may call the partner API: Kilby production and Kilby test, no one else.

Kilby's test site moved to its own account (chatllm-test-run) on 2026-10-05 and
every call it made was refused with 403 until this list named it. Production's
account must keep working through any change to the list.
"""
import partner_api

PRODUCTION = "cloud-run-chat@chatapp-488502.iam.gserviceaccount.com"
TEST = "chatllm-test-run@chatapp-488502.iam.gserviceaccount.com"


def test_kilby_production_is_still_allowed():
    assert partner_api.caller_allowed({"email": PRODUCTION, "email_verified": True})


def test_kilby_test_is_allowed():
    assert partner_api.caller_allowed({"email": TEST, "email_verified": True})


def test_any_other_account_is_refused():
    assert not partner_api.caller_allowed({"email": "someone@example.iam.gserviceaccount.com", "email_verified": True})
    assert not partner_api.caller_allowed({"email": "", "email_verified": True})
    assert not partner_api.caller_allowed({})


def test_an_unverified_address_is_refused_even_when_it_names_kilby():
    assert not partner_api.caller_allowed({"email": PRODUCTION, "email_verified": False})
    assert not partner_api.caller_allowed({"email": TEST})


def test_the_allowed_list_is_exactly_kilbys_two_accounts():
    assert partner_api.KILBY_SERVICE_ACCOUNTS == {PRODUCTION, TEST}
