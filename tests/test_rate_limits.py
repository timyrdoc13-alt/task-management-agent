import pytest

from agent.rate_limits import RateLimitError, RateLimiter


def test_kaiten_write_per_run():
    from kaiten_api import ENV

    ENV["RATE_KAITEN_WRITES_PER_RUN"] = "2"
    lim = RateLimiter()
    lim.check("kaiten_write")
    lim.record("kaiten_write")
    lim.check("kaiten_write")
    lim.record("kaiten_write")
    with pytest.raises(RateLimitError):
        lim.check("kaiten_write")
