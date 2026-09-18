from fastapi import APIRouter, HTTPException, Response, status

from app.api.deps import CurrentUser, DbDep
from app.schemas.data import DataDeleteRequest, DataDeleteResult
from app.services import data_service

router = APIRouter(prefix="/data", tags=["data"])


@router.delete("", response_model=DataDeleteResult)
async def delete_many_data(
    data: DataDeleteRequest, user: CurrentUser, db: DbDep
) -> DataDeleteResult:
    """Bulk-delete data documents by id."""
    deleted = await data_service.delete_many(db, data.ids)
    return DataDeleteResult(deleted=deleted)


@router.delete(
    "/{data_id}", status_code=status.HTTP_204_NO_CONTENT, response_class=Response
)
async def delete_data(data_id: str, user: CurrentUser, db: DbDep) -> Response:
    if not await data_service.delete_one(db, data_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Data not found"
        )
    return Response(status_code=status.HTTP_204_NO_CONTENT)
