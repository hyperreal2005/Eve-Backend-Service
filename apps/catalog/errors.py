from apps.core.errors import Conflict, NotFound


class CentreNotFound(NotFound):
    code = "CENTRE_NOT_FOUND"
    title = "Centre not found"
    default_detail = "No diagnostic centre with this id."


class DiagnosticTestNotFound(NotFound):
    code = "TEST_NOT_FOUND"
    title = "Test not found"
    default_detail = "No diagnostic test with this id."


class OfferingNotFound(NotFound):
    code = "OFFERING_NOT_FOUND"
    title = "Offering not found"
    default_detail = "This centre does not offer this test."


class CentreAlreadyExists(Conflict):
    code = "CENTRE_ALREADY_EXISTS"
    title = "Centre already exists"
    default_detail = "A centre with this name already exists in this city."


class DiagnosticTestCodeAlreadyExists(Conflict):
    code = "TEST_CODE_ALREADY_EXISTS"
    title = "Test code already exists"
    default_detail = "A diagnostic test with this code already exists."


class OfferingAlreadyExists(Conflict):
    code = "OFFERING_ALREADY_EXISTS"
    title = "Offering already exists"
    default_detail = "This centre already offers this test; update its price instead."


class DiagnosticTestInactive(Conflict):
    code = "TEST_INACTIVE"
    title = "Test is inactive"
    default_detail = "This test is not active, so it can't be offered."
