from time import monotonic
from decimal import Decimal
from fitwitness.contracts import Budget,Usage

class BudgetTracker:
    def __init__(self,budget:Budget):self.budget=budget;self.usage=Usage();self.start=monotonic()
    def check(self):
        if monotonic()-self.start>=self.budget.deadline_seconds:raise RuntimeError('실행 시간 예산 초과')
        if self.usage.input_tokens+self.usage.output_tokens>=self.budget.max_tokens:raise RuntimeError('토큰 예산 초과')
        if (self.usage.cost_usd or 0)>=self.budget.max_cost_usd:raise RuntimeError('비용 예산 초과')
    def tool(self):
        self.check()
        if self.usage.tool_calls>=self.budget.max_tool_calls:raise RuntimeError('도구 호출 예산 초과')
        self.usage.tool_calls+=1
    def model(self):
        self.check()
        if self.usage.model_calls>=self.budget.max_model_calls:raise RuntimeError('모델 호출 예산 초과')
        self.usage.model_calls+=1
    def reserve(self,input_bound,output_bound,input_rate,output_rate):
        # Rates are deployment configuration in USD/million, never guessed from a model name.
        projected=(Decimal(input_bound)*input_rate+Decimal(output_bound)*output_rate)/1000000
        if (self.usage.cost_usd or 0)+projected>self.budget.max_cost_usd:raise RuntimeError('비용 예산 초과: 호출 전 차단')
        if self.usage.input_tokens+self.usage.output_tokens+input_bound+output_bound>self.budget.max_tokens:raise RuntimeError('토큰 예산 초과: 호출 전 차단')
        self.model()
    def account(self,usage,input_rate,output_rate):
        i=usage.get('input_tokens',0);o=usage.get('output_tokens',0)
        self.usage.input_tokens+=i;self.usage.output_tokens+=o
        self.usage.cost_usd=(self.usage.cost_usd or Decimal(0))+(Decimal(i)*input_rate+Decimal(o)*output_rate)/1000000
