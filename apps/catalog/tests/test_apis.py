import uuid
from datetime import time

import pytest

from apps.catalog.models import DiagnosticCategory, DiagnosticCentre
from apps.catalog.tests.factories import (
    DiagnosticCentreFactory,
    DiagnosticTestFactory,
    OfferingFactory,
)
from apps.core.tests.assertions import assert_problem, field_errors, query_count

pytestmark = pytest.mark.django_db

CENTRES_URL = "/api/v1/centres/"
TESTS_URL = "/api/v1/tests/"

NEW_CENTRE = {
    "name": "Golf Course Road Diagnostics",
    "address_line": "Sector 54, Golf Course Road",
    "city": "Gurugram",
    "state": "Haryana",
    "pincode": "122011",
    "opens_at": "07:00",
    "closes_at": "21:00",
}


def centre_url(centre_id) -> str:
    return f"{CENTRES_URL}{centre_id}/"


def offerings_url(centre_id) -> str:
    return f"{centre_url(centre_id)}tests/"


def offering_url(centre_id, test_id) -> str:
    return f"{offerings_url(centre_id)}{test_id}/"


def catalogue_entry_url(test_id) -> str:
    return f"{TESTS_URL}{test_id}/"


def price_comparison_url(test_id) -> str:
    return f"{catalogue_entry_url(test_id)}centres/"


def ids(response) -> list[str]:
    assert response.status_code == 200, response.content
    return [item["id"] for item in response.json()["results"]]


# ------------------------------------------------------------------------- centres: reading


def test_patients_see_only_active_centres(api_client):
    active = DiagnosticCentreFactory()
    DiagnosticCentreFactory(is_active=False)
    response = api_client.get(CENTRES_URL)
    assert ids(response) == [str(active.id)]
    assert response.json()["count"] == 1


def test_administrators_also_see_inactive_centres(admin_client):
    DiagnosticCentreFactory()
    DiagnosticCentreFactory(is_active=False)
    assert admin_client.get(CENTRES_URL).json()["count"] == 2


def test_centres_filter_by_city_ignoring_case(api_client):
    gurugram = DiagnosticCentreFactory(city="Gurugram")
    DiagnosticCentreFactory(city="Mumbai")
    assert ids(api_client.get(CENTRES_URL, {"city": "gurugram"})) == [str(gurugram.id)]


def test_centres_filter_by_a_test_they_currently_offer(api_client):
    mri = DiagnosticTestFactory()
    offering = OfferingFactory(test=mri)
    OfferingFactory(test=mri, is_active=False)  # this centre stopped offering it
    OfferingFactory()  # this one offers something else
    assert ids(api_client.get(CENTRES_URL, {"test": str(mri.id)})) == [str(offering.centre.id)]


def test_centres_search_by_name(api_client):
    match = DiagnosticCentreFactory(name="Indiranagar Scan & Lab")
    DiagnosticCentreFactory(name="Sector 29 Diagnostics")
    assert ids(api_client.get(CENTRES_URL, {"search": "scan"})) == [str(match.id)]


def test_an_invalid_filter_value_is_a_validation_error(api_client):
    response = api_client.get(CENTRES_URL, {"test": "not-a-uuid"})
    body = assert_problem(response, 400, "VALIDATION_ERROR")
    assert ("test", "invalid") in field_errors(body)


def test_centre_lists_are_paginated(api_client):
    DiagnosticCentreFactory.create_batch(3)
    body = api_client.get(CENTRES_URL, {"page_size": 2}).json()
    assert (body["count"], len(body["results"])) == (3, 2)
    assert body["next"]
    assert_problem(api_client.get(CENTRES_URL, {"page": 99}), 404, "NOT_FOUND")


def test_centre_list_query_count_does_not_grow_with_rows(api_client):
    DiagnosticCentreFactory.create_batch(2)
    few = query_count(lambda: api_client.get(CENTRES_URL))
    DiagnosticCentreFactory.create_batch(10)
    assert query_count(lambda: api_client.get(CENTRES_URL)) == few


def test_centre_detail_lists_its_bookable_tests_with_prices(api_client):
    centre = DiagnosticCentreFactory()
    cbc = OfferingFactory(centre=centre, price=40_000)
    OfferingFactory(centre=centre, is_active=False)
    OfferingFactory(centre=centre, test=DiagnosticTestFactory(is_active=False))

    response = api_client.get(centre_url(centre.id))

    assert response.status_code == 200
    [listed] = response.json()["tests"]
    assert listed["test"]["id"] == str(cbc.test.id)
    assert (listed["price"], listed["currency"]) == (40_000, "INR")


def test_an_inactive_centre_is_hidden_from_patients_only(api_client, admin_client):
    centre = DiagnosticCentreFactory(is_active=False)
    assert_problem(api_client.get(centre_url(centre.id)), 404, "CENTRE_NOT_FOUND")
    assert admin_client.get(centre_url(centre.id)).json()["is_active"] is False


def test_unknown_and_malformed_centre_ids_are_404s(api_client):
    assert_problem(api_client.get(centre_url(uuid.uuid4())), 404, "CENTRE_NOT_FOUND")
    assert_problem(api_client.get(centre_url("not-a-uuid")), 404, "NOT_FOUND")


def test_centre_detail_query_count_does_not_grow_with_offerings(api_client):
    centre = DiagnosticCentreFactory()
    OfferingFactory.create_batch(2, centre=centre)
    few = query_count(lambda: api_client.get(centre_url(centre.id)))
    OfferingFactory.create_batch(8, centre=centre)
    assert query_count(lambda: api_client.get(centre_url(centre.id))) == few


# ------------------------------------------------------------------------- centres: writing


def test_admin_creates_a_centre(admin_client):
    response = admin_client.post(CENTRES_URL, NEW_CENTRE)
    assert response.status_code == 201
    body = response.json()
    assert (body["timezone"], body["opens_at"], body["is_active"]) == (
        "Asia/Kolkata",
        "07:00:00",
        True,
    )
    assert response["Location"].endswith(centre_url(body["id"]))


@pytest.mark.parametrize(
    ("client_fixture", "status", "code"),
    [("api_client", 401, "AUTHENTICATION_REQUIRED"), ("user_client", 403, "PERMISSION_DENIED")],
)
def test_only_administrators_create_centres(request, client_fixture, status, code):
    client = request.getfixturevalue(client_fixture)
    assert_problem(client.post(CENTRES_URL, NEW_CENTRE), status, code)
    assert not DiagnosticCentre.objects.exists()


@pytest.mark.parametrize(
    ("overrides", "field", "code"),
    [
        ({"pincode": "012345"}, "pincode", "invalid"),
        ({"opens_at": "21:00", "closes_at": "07:00"}, "closes_at", "before_opening"),
        ({"timezone": "Mars/Olympus_Mons"}, "timezone", "invalid"),
        ({"latitude": "123.0"}, "latitude", "max_value"),
        ({"rating": 5}, "rating", "unknown_field"),
    ],
)
def test_centre_input_is_validated(admin_client, overrides, field, code):
    response = admin_client.post(CENTRES_URL, {**NEW_CENTRE, **overrides})
    body = assert_problem(response, 400, "VALIDATION_ERROR")
    assert (field, code) in field_errors(body)


def test_a_duplicate_centre_is_a_conflict(admin_client):
    assert admin_client.post(CENTRES_URL, NEW_CENTRE).status_code == 201
    duplicate = {**NEW_CENTRE, "name": NEW_CENTRE["name"].upper(), "city": "gurugram"}
    assert_problem(admin_client.post(CENTRES_URL, duplicate), 409, "CENTRE_ALREADY_EXISTS")


def test_admin_partially_updates_a_centre(admin_client):
    centre = DiagnosticCentreFactory(opens_at=time(7), closes_at=time(21))
    response = admin_client.patch(centre_url(centre.id), {"closes_at": "22:30"})
    assert response.status_code == 200
    assert (response.json()["opens_at"], response.json()["closes_at"]) == ("07:00:00", "22:30:00")


def test_a_partial_update_is_checked_against_the_stored_hours(admin_client):
    centre = DiagnosticCentreFactory(opens_at=time(7), closes_at=time(21))
    response = admin_client.patch(centre_url(centre.id), {"closes_at": "06:00"})
    body = assert_problem(response, 400, "VALIDATION_ERROR")
    assert ("closes_at", "before_opening") in field_errors(body)


def test_deleting_a_centre_deactivates_it(api_client, admin_client):
    centre = DiagnosticCentreFactory()

    assert admin_client.delete(centre_url(centre.id)).status_code == 204
    assert admin_client.delete(centre_url(centre.id)).status_code == 204  # idempotent
    assert_problem(api_client.get(centre_url(centre.id)), 404, "CENTRE_NOT_FOUND")
    assert DiagnosticCentre.objects.get(id=centre.id).is_active is False

    reactivated = admin_client.patch(centre_url(centre.id), {"is_active": True})
    assert reactivated.json()["is_active"] is True


def test_patients_cannot_change_centres(user_client):
    centre = DiagnosticCentreFactory()
    assert_problem(
        user_client.patch(centre_url(centre.id), {"name": "Mine"}), 403, "PERMISSION_DENIED"
    )
    assert_problem(user_client.delete(centre_url(centre.id)), 403, "PERMISSION_DENIED")


# -------------------------------------------------------------------------------- offerings


def test_admin_offers_a_test_at_a_centre(admin_client):
    centre, test = DiagnosticCentreFactory(), DiagnosticTestFactory()
    response = admin_client.post(
        offerings_url(centre.id), {"test_id": str(test.id), "price": 65_000}
    )
    assert response.status_code == 201
    assert (response.json()["test"]["id"], response.json()["price"]) == (str(test.id), 65_000)
    assert response["Location"].endswith(offering_url(centre.id, test.id))


def test_patients_cannot_create_offerings(user_client):
    centre, test = DiagnosticCentreFactory(), DiagnosticTestFactory()
    response = user_client.post(offerings_url(centre.id), {"test_id": str(test.id), "price": 1})
    assert_problem(response, 403, "PERMISSION_DENIED")


def test_offering_a_test_twice_is_a_conflict(admin_client):
    offering = OfferingFactory()
    payload = {"test_id": str(offering.test.id), "price": 99_900}
    response = admin_client.post(offerings_url(offering.centre.id), payload)
    assert_problem(response, 409, "OFFERING_ALREADY_EXISTS")


def test_an_inactive_test_cannot_be_offered(admin_client):
    centre, test = DiagnosticCentreFactory(), DiagnosticTestFactory(is_active=False)
    response = admin_client.post(offerings_url(centre.id), {"test_id": str(test.id), "price": 100})
    assert_problem(response, 409, "TEST_INACTIVE")


def test_offering_references_must_exist(admin_client):
    centre, test = DiagnosticCentreFactory(), DiagnosticTestFactory()
    unknown_test = {"test_id": str(uuid.uuid4()), "price": 100}
    assert_problem(admin_client.post(offerings_url(centre.id), unknown_test), 404, "TEST_NOT_FOUND")
    unknown_centre = offerings_url(uuid.uuid4())
    response = admin_client.post(unknown_centre, {"test_id": str(test.id), "price": 100})
    assert_problem(response, 404, "CENTRE_NOT_FOUND")


@pytest.mark.parametrize(
    ("price", "code"),
    [(0, "min_value"), (-100, "min_value"), (100_000_001, "max_value"), ("12.50", "invalid")],
)
def test_prices_are_whole_positive_paise(admin_client, price, code):
    centre, test = DiagnosticCentreFactory(), DiagnosticTestFactory()
    response = admin_client.post(
        offerings_url(centre.id), {"test_id": str(test.id), "price": price}
    )
    body = assert_problem(response, 400, "VALIDATION_ERROR")
    assert ("price", code) in field_errors(body)


def test_admin_changes_a_price(admin_client):
    offering = OfferingFactory(price=100_000)
    url = offering_url(offering.centre.id, offering.test.id)
    response = admin_client.patch(url, {"price": 90_000})
    assert response.status_code == 200
    assert (response.json()["price"], response.json()["is_active"]) == (90_000, True)


def test_a_withdrawn_offering_leaves_the_public_catalogue(api_client, admin_client):
    offering = OfferingFactory()
    url = offering_url(offering.centre.id, offering.test.id)

    assert api_client.get(url).status_code == 200
    assert admin_client.delete(url).status_code == 204

    assert api_client.get(offerings_url(offering.centre.id)).json()["count"] == 0
    assert_problem(api_client.get(url), 404, "OFFERING_NOT_FOUND")


def test_centre_offerings_can_be_ordered_by_price(api_client):
    centre = DiagnosticCentreFactory()
    OfferingFactory(centre=centre, price=30_000)
    OfferingFactory(centre=centre, price=90_000)
    response = api_client.get(offerings_url(centre.id), {"ordering": "-price"})
    assert [item["price"] for item in response.json()["results"]] == [90_000, 30_000]


# ---------------------------------------------------------------------------- the test catalogue


def test_patients_see_active_tests_by_category_or_search(api_client):
    mri = DiagnosticTestFactory(
        code="MRI_BRAIN", name="MRI Brain", category=DiagnosticCategory.RADIOLOGY
    )
    DiagnosticTestFactory(code="CBC", name="Complete Blood Count")
    DiagnosticTestFactory(code="RETIRED", is_active=False)

    assert api_client.get(TESTS_URL).json()["count"] == 2
    assert ids(api_client.get(TESTS_URL, {"category": "RADIOLOGY"})) == [str(mri.id)]
    assert ids(api_client.get(TESTS_URL, {"search": "mri_b"})) == [str(mri.id)]


def test_admin_adds_a_test_with_a_normalised_code(admin_client):
    payload = {"code": "vit_b12", "name": "Vitamin B12", "category": "PATHOLOGY"}
    response = admin_client.post(TESTS_URL, payload)
    assert response.status_code == 201
    assert response.json()["code"] == "VIT_B12"
    assert response["Location"].endswith(catalogue_entry_url(response.json()["id"]))

    duplicate = admin_client.post(TESTS_URL, {**payload, "code": "VIT_b12"})
    assert_problem(duplicate, 409, "TEST_CODE_ALREADY_EXISTS")


@pytest.mark.parametrize(
    ("overrides", "field", "code"),
    [
        ({"code": "has space"}, "code", "invalid"),
        ({"category": "DENTAL"}, "category", "invalid_choice"),
    ],
)
def test_catalogue_input_is_validated(admin_client, overrides, field, code):
    payload = {"code": "NEW", "name": "New test", "category": "PATHOLOGY", **overrides}
    body = assert_problem(admin_client.post(TESTS_URL, payload), 400, "VALIDATION_ERROR")
    assert (field, code) in field_errors(body)


def test_admin_updates_a_test(admin_client):
    test = DiagnosticTestFactory(name="Old name")
    response = admin_client.patch(catalogue_entry_url(test.id), {"name": "New name"})
    assert (response.status_code, response.json()["name"]) == (200, "New name")


def test_deleting_a_test_withdraws_it_at_every_centre(api_client, admin_client):
    offering = OfferingFactory()
    assert admin_client.delete(catalogue_entry_url(offering.test.id)).status_code == 204
    assert_problem(api_client.get(catalogue_entry_url(offering.test.id)), 404, "TEST_NOT_FOUND")
    assert api_client.get(centre_url(offering.centre.id)).json()["tests"] == []


# --------------------------------------------------------------------------- price comparison


def test_price_comparison_lists_bookable_centres_cheapest_first(api_client):
    mri = DiagnosticTestFactory()
    mumbai = OfferingFactory(test=mri, price=800_000, centre=DiagnosticCentreFactory(city="Mumbai"))
    gurugram = OfferingFactory(test=mri, price=650_000)
    OfferingFactory(test=mri, price=100, centre=DiagnosticCentreFactory(is_active=False))
    OfferingFactory(test=mri, price=200, is_active=False)

    results = api_client.get(price_comparison_url(mri.id)).json()["results"]
    assert [(entry["centre"]["id"], entry["price"]) for entry in results] == [
        (str(gurugram.centre.id), 650_000),
        (str(mumbai.centre.id), 800_000),
    ]

    in_mumbai = api_client.get(price_comparison_url(mri.id), {"city": "MUMBAI"}).json()["results"]
    assert [entry["centre"]["id"] for entry in in_mumbai] == [str(mumbai.centre.id)]


def test_price_comparison_for_an_unknown_test_is_a_404(api_client):
    assert_problem(api_client.get(price_comparison_url(uuid.uuid4())), 404, "TEST_NOT_FOUND")


def test_price_comparison_query_count_does_not_grow_with_centres(api_client):
    mri = DiagnosticTestFactory()
    OfferingFactory.create_batch(2, test=mri)
    few = query_count(lambda: api_client.get(price_comparison_url(mri.id)))
    OfferingFactory.create_batch(8, test=mri)
    assert query_count(lambda: api_client.get(price_comparison_url(mri.id))) == few
