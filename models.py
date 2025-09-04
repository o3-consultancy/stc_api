from __future__ import annotations
import re
from typing import Optional
from pydantic import BaseModel, EmailStr, field_validator


class RegisterUserRequest(BaseModel):

    name: str
    qrId: str
    email: Optional[EmailStr] = None
    phone: Optional[str] = None

    @field_validator("name")
    @classmethod
    def name_length(cls, v: str) -> str:

        v = v.strip()
        if not (3 <= len(v) <= 50):
            raise ValueError("name must be between 3 and 50 characters")
        return v

    @field_validator("qrId")
    @classmethod
    def qr_required(cls, v: str) -> str:

        v = v.strip()
        if not v:
            raise ValueError("qrId is required")
        return v

    @field_validator("phone")
    @classmethod
    def phone_digits(cls, v: Optional[str]) -> Optional[str]:

        if v is None:
            return v
        digits = re.sub(r"\D", "", v)
        if len(digits) != 10:
            raise ValueError("phone must be exactly 10 digits")
        return digits


class SubmitQuizRequest(BaseModel):

    qrId: str
    correctAnswers: int

    @field_validator("qrId")
    @classmethod
    def qr_required(cls, v: str) -> str:

        v = v.strip()
        if not v:
            raise ValueError("qrId is required")
        return v

    @field_validator("correctAnswers")
    @classmethod
    def non_negative(cls, v: int) -> int:

        if v < 0:
            raise ValueError("correctAnswers must be >= 0")
        return v
