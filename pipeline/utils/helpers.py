import importlib
from types import SimpleNamespace

def import_voting_rules_from_vote_kit(rules_list: List[str]) -> SimpleNamespace:
    election_lib = importlib.import_module("votekit.elections.election_types")
    rules = { getattr(election_lib, rule) for rule in rules_list }
    rules = SimpleNamespace(**rules)
    return rules