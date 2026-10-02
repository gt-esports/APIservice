from typing import Annotated

from fastapi import APIRouter, Depends, Response

from apps.gamefest.db.session import GameFestRepository, get_gamefest_repository
from apps.gamefest.models.schemas import PastWinnersResponse
from apps.gamefest.services.gamefest_service import past_winners


router = APIRouter(tags=["GameFest"])


@router.get(
    "/past-winners",
    response_model=PastWinnersResponse,
    summary="Public Hall of Fame for the latest 20 inactive GameFest seasons",
    responses={503: {"description": "Public data source is unavailable or incorrectly configured"}},
)
async def get_past_winners(
    response: Response,
    repository: Annotated[GameFestRepository, Depends(get_gamefest_repository)],
) -> PastWinnersResponse:
    result = await past_winners(repository)
    response.headers["Cache-Control"] = "public, max-age=30"
    return result
