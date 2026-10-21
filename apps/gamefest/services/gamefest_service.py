from apps.gamefest.db.session import GameFestRepository
from apps.gamefest.models.schemas import PastWinnersResponse, PublicProfile, SeasonWinners, Winner


def display_name(profile: PublicProfile | list[PublicProfile] | None) -> str:
    if isinstance(profile, list):
        profile = profile[0] if profile else None
    if profile is None:
        return "GameFest Player"
    full_name = " ".join(part.strip() for part in (profile.fname, profile.lname) if part and part.strip())
    return full_name or (profile.username or "").strip() or "GameFest Player"


async def past_winners(repository: GameFestRepository) -> PastWinnersResponse:
    records = await repository.list_past_winners()
    return PastWinnersResponse(seasons=[
        SeasonWinners(
            id=season.id,
            name=season.name,
            slug=season.slug,
            winners=[
                Winner(rank=index + 1, display_name=display_name(player.users), points=player.points or 0)
                for index, player in enumerate(season.players)
            ],
        )
        for season in records
    ])
