from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Response, status

from app.api.deps import CurrentUser, DbDep
from app.schemas.agent import AgentCreate, AgentList, AgentOut, AgentUpdate
from app.services import agent_service

router = APIRouter(prefix="/agents", tags=["agents"])


@router.post("", response_model=AgentOut, status_code=status.HTTP_201_CREATED)
async def create_agent(data: AgentCreate, user: CurrentUser, db: DbDep) -> AgentOut:
    doc = await agent_service.create_agent(db, data, user["email"])
    return AgentOut.from_doc(doc)


@router.get("", response_model=AgentList)
async def list_agents(
    db: DbDep,
    user: CurrentUser,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    skip: Annotated[int, Query(ge=0)] = 0,
) -> AgentList:
    agents, total = await agent_service.list_agents(db, skip=skip, limit=limit)
    return AgentList(agents=[AgentOut.from_doc(a) for a in agents], total=total)


@router.get("/{agent_id}", response_model=AgentOut)
async def get_agent(agent_id: str, user: CurrentUser, db: DbDep) -> AgentOut:
    doc = await agent_service.get_agent(db, agent_id)
    if doc is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Agent not found"
        )
    return AgentOut.from_doc(doc)


@router.patch("/{agent_id}", response_model=AgentOut)
async def update_agent(
    agent_id: str, data: AgentUpdate, user: CurrentUser, db: DbDep
) -> AgentOut:
    doc = await agent_service.update_agent(db, agent_id, data, user["email"])
    if doc is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Agent not found"
        )
    return AgentOut.from_doc(doc)


@router.delete(
    "/{agent_id}", status_code=status.HTTP_204_NO_CONTENT, response_class=Response
)
async def delete_agent(agent_id: str, user: CurrentUser, db: DbDep) -> Response:
    if not await agent_service.delete_agent(db, agent_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Agent not found"
        )
    return Response(status_code=status.HTTP_204_NO_CONTENT)
