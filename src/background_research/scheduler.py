"""Advance only complete, registered windows; recover unfinished batch identities."""

from datetime import datetime, timedelta

from src.background_research.contracts import LearningBatch, LearningCampaign
from src.background_research.registry import TERMINAL_STATES, LearningRegistry, _source, _validate_source
from src.research.round_two_contracts import _utc
from src.strategies.types import canonical_hash


class LearningScheduler:
    def __init__(self, registry: LearningRegistry):
        self.registry = registry

    def next_batch(
        self,
        campaign: LearningCampaign,
        *,
        symbol: str,
        data_fingerprint: str,
        through: datetime,
        now: datetime,
    ) -> LearningBatch | None:
        campaign = campaign.validated()
        through, now = _utc(through, "through"), _utc(now, "now")
        if through > now:
            raise ValueError("eligible observations cannot extend into the future")
        if symbol not in campaign.symbols:
            raise ValueError("symbol is not registered in campaign")
        _validate_source(campaign, _source(campaign))
        with self.registry._locked():
            state, raw, rows = self.registry._read()
            if campaign.identity_hash not in state.campaigns:
                raise ValueError("campaign must be registered before dispatch")
            protocol = state.campaigns[campaign.identity_hash][1]
            key = protocol.source.provider, protocol.source.feed, symbol, protocol.interval
            peers = [batch for batch in state.batches.values() if state.key(batch) == key]
            if any(batch.created_at > now for batch in peers):
                return None
            own = [batch for batch in peers if batch.campaign_hash == campaign.identity_hash]
            for batch in reversed(own):
                batch_state = state.batch_state(batch.batch_id)
                if batch_state in {"paused", "pausing", "blocked"}:
                    return None
                if batch_state not in TERMINAL_STATES:
                    return batch if batch.holdout_end <= through else None
            schedule = campaign.schedule
            start = (
                max(batch.training_start for batch in own) + timedelta(days=schedule.step_days)
                if own
                else schedule.starts_at
            )
            training_end = start + timedelta(days=schedule.train_days)
            validation_end = training_end + timedelta(days=schedule.validation_days)
            holdout_end = validation_end + timedelta(days=schedule.sealed_test_days)
            if holdout_end > through:
                return None
            identity = {
                "campaign_hash": campaign.identity_hash,
                "symbol": symbol,
                "utc_day": now.date(),
                "data_fingerprint": data_fingerprint,
                "training_start": start,
            }
            batch = LearningBatch(
                batch_id=canonical_hash(identity),
                campaign_hash=campaign.identity_hash,
                symbol=symbol,
                utc_day=now.date(),
                data_fingerprint=data_fingerprint,
                training_start=start,
                training_end=training_end,
                validation_end=validation_end,
                holdout_end=holdout_end,
                max_attempts=campaign.max_attempts_per_batch,
                created_at=now,
            )
            if not state.can_reserve(batch):
                return None
            self.registry._commit(state, raw, rows, {"kind": "batch", "batch": batch.model_dump(mode="json")})
            return batch
