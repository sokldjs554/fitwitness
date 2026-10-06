"""Insurance claim adjudication: the second workflow on the FitWitness runtime.

Synthetic Korean claim documents (진단서, 입퇴원확인서, 수술확인서, 영수증) are extracted
field by field with their source position, checked against each other, adjudicated by a
deterministic policy table with rule ids on every won, routed to a reviewer when the
rules say so, and paid once through an idempotent ledger. No real personal data anywhere.
"""
