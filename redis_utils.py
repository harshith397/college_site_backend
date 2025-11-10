import redis.asyncio as redis
import json
from typing import Optional, Any

async def init_redis_connection(redis_url: str):
    """
    Initialize async Redis connection from URL.
    Supports both redis:// and rediss:// (SSL) URLs.
    """
    try:
        client = redis.from_url(
            redis_url,
            encoding="utf-8",
            decode_responses=True,
            socket_connect_timeout=5,
            socket_keepalive=True
        )
        # Test connection
        await client.ping()
        print(f"✅ Redis connected successfully")
        return client
    except Exception as e:
        print(f"❌ Redis connection failed: {e}")
        raise

async def get_cached_data(redis_client, key: str) -> Optional[dict]:
    """
    Retrieve cached data from Redis.
    Returns None if key doesn't exist.
    """
    try:
        data = await redis_client.get(key)
        if data:
            return json.loads(data)
        return None
    except Exception as e:
        print(f"Error getting cache for key '{key}': {e}")
        return None

async def set_cached_data(redis_client, key: str, value: Any, ttl: int = 3600) -> bool:
    """
    Store data in Redis with TTL (in seconds).
    Default TTL: 1 hour (3600 seconds)
    """
    try:
        serialized = json.dumps(value)
        await redis_client.setex(key, ttl, serialized)
        return True
    except Exception as e:
        print(f"Error setting cache for key '{key}': {e}")
        return False

async def delete_cache(redis_client, key: str) -> bool:
    """
    Delete a key from Redis.
    Returns True if key was deleted, False otherwise.
    """
    try:
        result = await redis_client.delete(key)
        return result > 0
    except Exception as e:
        print(f"Error deleting cache for key '{key}': {e}")
        return False

async def clear_pattern(redis_client, pattern: str) -> int:
    """
    Delete all keys matching a pattern.
    Example: clear_pattern(client, "erp:session:*")
    Returns number of keys deleted.
    """
    try:
        keys = []
        async for key in redis_client.scan_iter(match=pattern):
            keys.append(key)
        
        if keys:
            return await redis_client.delete(*keys)
        return 0
    except Exception as e:
        print(f"Error clearing pattern '{pattern}': {e}")
        return 0

async def get_ttl(redis_client, key: str) -> int:
    """
    Get remaining TTL for a key in seconds.
    Returns -1 if key has no expiry, -2 if key doesn't exist.
    """
    try:
        return await redis_client.ttl(key)
    except Exception as e:
        print(f"Error getting TTL for key '{key}': {e}")
        return -2