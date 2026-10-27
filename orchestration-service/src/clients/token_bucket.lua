-- Token bucket shared by every worker replica.
-- KEYS[1] = bucket hash key
-- ARGV[1] = refill rate (token per second), ARGV[2] = capacity (max_tokens)
-- Returns: 0 when a token was taken, otherwise milliseconds to wait before retrying 
-- Runs atomically inside Redis, so two workers can never take the same token.

local rate = tonumber(ARGV[1])
local capacity = tonumber(ARGV[2])

local t = redis.call('TIME')   -- Redis clock: same for all workers

local now = tonumber(t[1]) * 1000 + math.floor(tonumber(t[2])/1000)

local state = redis.call('HMGET', KEYS[1], 'tokens', 'ts')

local tokens = tonumber(state[1])
local ts = tonumber(state[2])
if tokens == nil then
    tokens = capacity
    ts = now
end

-- refill for the time elapsed since the last call, never above th capacity

tokens = math.min(capacity, tokens + (now-ts) * rate / 1000)

local wait = 0
if tokens >= 1 then
    tokens = tokens - 1
else 
    wait = math.ceil((1 - tokens) * 1000 / rate)
end

redis.call('HSET', KEYS[1], 'tokens', tokens, 'ts', now)
redis.call('PEXPIRE', KEYS[1], 60000) -- idle buckets clean themselves up

return wait