"""Create the schema in the `test` database and count the constraints (run from orchestration-service/)."""

import asyncio
import os

from graph.driver import make_driver
from graph.schema import ensure_schema


async def main() -> None:
    driver = make_driver("bolt://localhost:7687", "neo4j", os.environ["GRAPH_PASSWORD"])
    try:
        await ensure_schema(driver, "test")
        async with driver.session(database="test") as session:
            result = await session.run("SHOW CONSTRAINTS YIELD name RETURN count(*) AS n")
            print("constraints:", (await result.single())["n"])
    finally:
        await driver.close()


asyncio.run(main())
