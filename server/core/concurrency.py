import asyncio

EXTERNAL_API_SEMAPHORE = asyncio.Semaphore(10)
