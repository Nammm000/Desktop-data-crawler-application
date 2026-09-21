from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Response, status

from app.api.deps import CurrentUser, DbDep
from app.schemas.data import (
    DataDeleteRequest,
    DataDeleteResult,
    DataList,
    DataOut,
)
from app.services import data_service

router = APIRouter(prefix="/data", tags=["data"])


@router.get("/orphaned", response_model=DataList)
async def list_orphaned_data(
    db: DbDep,
    user: CurrentUser,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    skip: Annotated[int, Query(ge=0)] = 0,
) -> DataList:
    """Crawled-data records whose agent has been deleted, newest first."""
    docs, total = await data_service.list_orphaned(db, skip=skip, limit=limit)
    return DataList(data=[DataOut.from_doc(d) for d in docs], total=total)


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
