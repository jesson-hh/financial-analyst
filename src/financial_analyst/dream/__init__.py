"""Dream loop: agent self-improving memory iteration."""
from financial_analyst.dream.outcome_tracker import Outcome, OutcomeTracker
from financial_analyst.dream.introspector import Introspector, IntrospectionOutput, Proposal
from financial_analyst.dream.proposal_writer import write_proposals
from financial_analyst.dream.aggregator import aggregate_pending, aggregate_capability_gaps

__all__ = [
    "Outcome", "OutcomeTracker",
    "Introspector", "IntrospectionOutput", "Proposal",
    "write_proposals",
    "aggregate_pending", "aggregate_capability_gaps",
]
