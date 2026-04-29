from pydantic import BaseModel


class StudentSignupRequest(BaseModel):
    qr_id: str
    claim_token: str
    signature: str
    student_id: str = ""
    email: str
    password: str
    name: str = ""
    major: str = ""
    year: str = ""


class StudentPassConsumeRequest(BaseModel):
    token: str
    signature: str
    location: str = ""
    context: str = "event_checkin"
