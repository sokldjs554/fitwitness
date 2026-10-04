from time import monotonic
from decimal import Decimal
from fitwitness.contracts import Budget, Usage


class BudgetExhausted(RuntimeError):
    """A normal resource stop, distinct from cancellation or a missed deadline."""


class BudgetTracker:
    def __init__(self, budget: Budget):
        self.budget = budget
        self.usage = Usage()
        self.start = monotonic()
        self.persist = lambda usage: None
        self.current_reservation = None

    def check(self, resources=True):
        if monotonic() - self.start >= self.budget.deadline_seconds:
            raise RuntimeError("실행 시간 예산 초과")
        if not resources:
            return
        if (
            self.usage.input_tokens
            + self.usage.output_tokens
            + self.usage.reserved_tokens
            >= self.budget.max_tokens
        ):
            raise BudgetExhausted("토큰 예산 초과")
        if (
            self.usage.cost_usd or 0
        ) + self.usage.reserved_cost_usd >= self.budget.max_cost_usd:
            raise BudgetExhausted("비용 예산 초과")

    def tool(self):
        self.check()
        if self.usage.tool_calls >= self.budget.max_tool_calls:
            raise BudgetExhausted("도구 호출 예산 초과")
        self.usage.tool_calls += 1
        self.persist(self.usage)

    def model(self):
        self.check()
        if self.usage.model_calls >= self.budget.max_model_calls:
            raise BudgetExhausted("모델 호출 예산 초과")
        self.usage.model_calls += 1

    def reserve(self, input_bound, output_bound, input_rate, output_rate):
        projected = (
            Decimal(input_bound) * input_rate + Decimal(output_bound) * output_rate
        ) / 1000000
        if (
            self.usage.cost_usd or 0
        ) + self.usage.reserved_cost_usd + projected > self.budget.max_cost_usd:
            raise BudgetExhausted("비용 예산 초과: 호출 전 차단")
        tokens = input_bound + output_bound
        if (
            self.usage.input_tokens
            + self.usage.output_tokens
            + self.usage.reserved_tokens
            + tokens
            > self.budget.max_tokens
        ):
            raise BudgetExhausted("토큰 예산 초과: 호출 전 차단")
        self.model()
        self.usage.reserved_cost_usd += projected
        self.usage.reserved_tokens += tokens
        self.current_reservation = (projected, tokens)
        self.persist(self.usage)

    def account(self, usage, input_rate, output_rate):
        i = usage.get("input_tokens", 0)
        o = usage.get("output_tokens", 0)
        if self.current_reservation:
            cost, tokens = self.current_reservation
            self.usage.reserved_cost_usd -= cost
            self.usage.reserved_tokens -= tokens
            self.current_reservation = None
        self.usage.input_tokens += i
        self.usage.output_tokens += o
        self.usage.cost_usd = (self.usage.cost_usd or Decimal(0)) + (
            Decimal(i) * input_rate + Decimal(o) * output_rate
        ) / 1000000
        self.persist(self.usage)
