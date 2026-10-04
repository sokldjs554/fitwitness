from decimal import Decimal
import pytest
from fitwitness.contracts import DrawingRevision,Budget
from fitwitness.storage.repository import Repository
from fitwitness.agents.budget import BudgetTracker
from fitwitness.retrieval.pipeline import extract_requirements

def test_text_dimensions_are_not_silently_dropped():
    rs=extract_requirements('구멍 간격 40mm, 두께 999mm 브래킷')
    assert {'hole_spacing','thickness'} <= {r.field for r in rs}

def test_unknown_numeric_condition_is_explicitly_unverified():
    assert any(r.field=='unparsed_constraint' for r in extract_requirements('브래킷 하중 10kg'))

def test_future_revision_not_active_and_withdrawn_does_not_restore_parent():
    a=DrawingRevision(id='a',tenant_id='t',document_id='d',drawing_number='D',family_id='f',revision_label='A',source_hash='a'*64)
    b=a.model_copy(update={'id':'b','supersedes':'a','effective_from':'2099-01-01T00:00:00+00:00'})
    assert Repository.active([a,b])[0]==['a']
    b=a.model_copy(update={'id':'b','supersedes':'a','approval':'withdrawn'})
    assert Repository.active([a,b])[0]==[]

def test_invalid_effective_date_rejected():
    with pytest.raises(ValueError):DrawingRevision(tenant_id='t',document_id='d',drawing_number='D',family_id='f',revision_label='A',source_hash='a'*64,effective_from='tomorrow')

def test_reservation_survives_unanswered_provider_call():
    b=BudgetTracker(Budget(max_cost_usd=Decimal('.01')))
    b.reserve(1000,1000,Decimal(2),Decimal(5))
    assert b.usage.reserved_cost_usd==Decimal('.007')
    with pytest.raises(RuntimeError):b.reserve(1000,1000,Decimal(2),Decimal(5))
    b.account({'input_tokens':100,'output_tokens':100},Decimal(2),Decimal(5))
    assert b.usage.reserved_cost_usd==0 and b.usage.cost_usd==Decimal('.0007')
