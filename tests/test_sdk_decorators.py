import pytest

from src.sdk.decorators import task


def _handler():
    return "ok"


class TestTaskDecoratorRetryValidation:
    def test_accepts_zero_and_positive_retries(self):
        zero_retry_task = task(retries=0)(_handler)
        three_retry_task = task(retries=3)(_handler)

        assert zero_retry_task.__task_config__["retries"] == 0
        assert three_retry_task.__task_config__["retries"] == 3

    @pytest.mark.parametrize("retries", [-1, -3])
    def test_rejects_negative_retries(self, retries):
        with pytest.raises(ValueError, match="non-negative integer"):
            task(retries=retries)

    @pytest.mark.parametrize("retries", [True, False, 1.5, "3", None])
    def test_rejects_non_integer_retries(self, retries):
        with pytest.raises(ValueError, match="non-negative integer"):
            task(retries=retries)
