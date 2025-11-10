# exceptions.py

class CollegePortalError(Exception):
    """
    Exception for errors related to college portal parsing, login, or data fetch.
    Used in tools.py / services layer.
    """
    def __init__(self, status_code: int, message: str, detail: str = None):
        self.status_code = status_code   # like 400, 401, 500
        self.message = message           # short error summary
        self.detail = detail             # optional more info (like HTML snippet, field name)
        super().__init__(f"{status_code}: {message} - {detail}")


