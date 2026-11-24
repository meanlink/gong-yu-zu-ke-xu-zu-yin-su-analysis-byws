from typing import Annotated, Literal

from pydantic import AwareDatetime, BaseModel, Field, StringConstraints

SheetRef = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=60)]
IdempotencyKey = Annotated[str, StringConstraints(strip_whitespace=True, min_length=8, max_length=120)]
ResolutionNote = Annotated[str, StringConstraints(strip_whitespace=True, min_length=4, max_length=300)]


class ImportSheetIn(BaseModel):
    tenant_id: int = Field(gt=0)
    lease_period: str = Field(min_length=4, max_length=40)
    cleanliness_score: float = Field(ge=1, le=5)
    service_score: float = Field(ge=1, le=5)
    value_score: float = Field(ge=1, le=5)
    community_score: float = Field(ge=1, le=5)
    renewal_intent: float = Field(ge=1, le=10)
    comments: str = Field(default="", max_length=2000)
    sheet_ref: SheetRef | None = None
    collected_at: AwareDatetime | None = None


class ImportIn(BaseModel):
    idempotency_key: IdempotencyKey
    source: str = Field(default="纸质问卷补录", min_length=2, max_length=120)
    note: str | None = Field(default=None, max_length=500)
    sheets: list[ImportSheetIn] = Field(min_length=1, max_length=200)


class ConflictResolveIn(BaseModel):
    resolution: Literal["adopted", "kept_original"]
    sheet_id: int | None = Field(default=None, gt=0)
    note: ResolutionNote
