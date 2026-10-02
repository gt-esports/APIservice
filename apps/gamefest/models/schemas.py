from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, Field, StringConstraints


NonBlankString = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
Points = Annotated[int, Field(strict=True, ge=0, le=2147483647)]


class Winner(BaseModel):
    rank: int = Field(ge=1, le=3)
    display_name: NonBlankString
    points: Points


class SeasonWinners(BaseModel):
    id: UUID
    name: NonBlankString
    slug: NonBlankString
    winners: list[Winner] = Field(max_length=3)


class PastWinnersResponse(BaseModel):
    seasons: list[SeasonWinners] = Field(max_length=20)


class PublicProfile(BaseModel):
    username: str | None = None
    fname: str | None = None
    lname: str | None = None


class PlayerRecord(BaseModel):
    id: UUID
    points: Points | None
    users: PublicProfile | list[PublicProfile] | None


class SeasonRecord(BaseModel):
    id: UUID
    name: NonBlankString
    slug: NonBlankString
    players: list[PlayerRecord] = Field(max_length=3)
