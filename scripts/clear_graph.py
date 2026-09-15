import asyncio
from sqlalchemy import text
from sci_rag.db.engine import session_scope

async def clear_graph():
    async with session_scope() as session:
        # 1. Clear community tables
        await session.execute(text("TRUNCATE TABLE kg_communities CASCADE"))
        # 2. Clear relationship tables
        await session.execute(text("TRUNCATE TABLE kg_relationships CASCADE"))
        # 3. Clear entity resolution audit
        await session.execute(text("TRUNCATE TABLE entity_resolution_audit CASCADE"))
        # 4. Clear entity tables
        await session.execute(text("TRUNCATE TABLE kg_entities CASCADE"))
        # 5. Reset the graph_extracted_at timestamp on chunks to allow re-extraction
        await session.execute(text("UPDATE chunks SET graph_extracted_at = NULL"))
        
        await session.commit()
        print("Graph cleared and chunk extraction status reset.")

if __name__ == "__main__":
    asyncio.run(clear_graph())
