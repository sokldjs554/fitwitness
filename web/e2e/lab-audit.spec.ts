import { test, expect, type Page } from '@playwright/test';
import { readFileSync } from 'node:fs';

const fixture = (name: string) => JSON.parse(readFileSync(new URL(`../../docs/evaluation/${name}.json`, import.meta.url), 'utf8'));
// Exercise the real UI against committed published records, with no paid calls or DB mutations.
test.beforeEach(async ({page}) => {
  await page.route('**/api/demo-sessions', route => route.fulfill({json:{}}));
  await page.route('**/api/documents', route => route.fulfill({json:[]}));
  await page.route(/\/api\/evaluations/, route => {
    const url = new URL(route.request().url());
    if (url.pathname.endsWith('/catalog')) return route.fulfill({json:{experiments:[{id:'qwen',label:'Qwen3 · 로컬'},{id:'claude',label:'Claude Haiku 4.5 · API'}]}});
    const file = url.pathname.endsWith('/retrieval') ? (url.searchParams.get('experiment') === 'reranking' ? 'reranking' : 'retrieval') : url.pathname.endsWith('/vision') ? 'vision' : url.pathname.endsWith('/agent') ? (url.searchParams.get('version') === 'v1' ? 'agent-v1' : 'agent') : url.searchParams.get('experiment') === 'claude' ? 'claude' : 'latest';
    return route.fulfill({json:fixture(file)});
  });
});
async function openLab(page: Page) {
  await page.goto('/');
  await page.getByRole('button', { name: '실험실', exact: true }).click();
  await expect(page.getByRole('heading', { name: '평가 결과 비교', exact: true })).toBeVisible();
}

test('every recorded model, query, vision repetition and agent run can be inspected', async ({ page }) => {
  test.setTimeout(120000);
  const errors: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  await openLab(page);
  for (const [model, file] of [['qwen', 'latest'], ['claude', 'claude']]) {
    const report = fixture(file);
    await page.getByRole('combobox', { name: '측정 모델', exact: true }).selectOption(model);
    await expect(page.getByTestId('experiment-comparison')).toContainText(report.methods[1].label);
    await expect(page.locator('.case-row')).toHaveCount(24);
    for (let i = 0; i < report.cases.length; i++) {
      const c = report.cases[i];
      await page.locator('.case-row').nth(i).click();
      await expect(page.locator('.case-inspector')).toContainText(c.drawing_number);
      for (let repeat = 0; repeat < 3; repeat++) {
        await page.locator('[aria-label="평가 반복 선택"]').getByRole('button', {name: `#${repeat + 1}`, exact:true}).click();
        const row = report.predictions.find((r: any) => r.case_id === c.case_id && r.method !== 'rules' && r.repeat === repeat);
        await expect(page.locator('.output-code')).toHaveText(row.raw_output || row.error || '기록 없음');
      }
    }
  }
  const retrieval = page.getByRole('region', {name:'도면 검색 방식 비교', exact:true});
  for (const [experiment, file] of [['baseline', 'retrieval'], ['reranking', 'reranking']]) {
    await retrieval.getByRole('combobox', {name:'검색 비교 실험'}).selectOption(experiment);
    await expect(retrieval.getByRole('table')).toContainText(fixture(file).methods[0].label);
    for (const category of ['exact','paraphrase','image','mixed']) {
      await retrieval.getByRole('combobox', {name:'검색 질문 유형'}).selectOption(category);
      const cases = fixture(file).cases.filter((c: any) => c.category === category);
      await expect(retrieval.getByRole('combobox', {name:'검색 평가 질문'}).locator('option')).toHaveCount(6);
      for (const c of cases) {
        await retrieval.getByRole('combobox', {name:'검색 평가 질문'}).selectOption(c.id);
        await expect(retrieval.locator('.retrieval-query')).toContainText(c.text || '텍스트 없이 변형된 도면 이미지만 입력');
        if (c.image_data_url) await expect.poll(() => retrieval.getByRole('img').evaluate((img: HTMLImageElement) => img.naturalWidth)).toBeGreaterThan(0);
      }
    }
  }
  const vision = page.getByRole('region', {name:'도면 이미지 읽기', exact:true});
  for (const c of fixture('vision').protocol.cases) {
    await vision.getByRole('combobox', {name:'이미지 읽기 사례'}).selectOption(c.id);
    await expect(vision.getByRole('img')).toHaveJSProperty('complete', true);
    expect(await vision.getByRole('img').evaluate((img: HTMLImageElement) => img.naturalWidth)).toBeGreaterThan(0);
    for (let repeat = 1; repeat <= 3; repeat++) {
      await vision.getByRole('combobox', {name:'이미지 읽기 반복'}).selectOption(String(repeat));
      const row = fixture('vision').rows.find((r: any) => r.case_id === c.id && r.repeat === repeat);
      if (row.status === 'error') await expect(vision.getByRole('status')).toContainText('호출 실패');
      else await expect(vision.getByRole('table')).toBeVisible();
    }
  }
  const agent = page.getByRole('region', {name:'실제 Agent 실행 비교', exact:true});
  for (const [version, file] of [['latest', 'agent'], ['v1', 'agent-v1']]) {
    await agent.getByRole('combobox', {name:'Agent 실행 버전'}).selectOption(version);
    const runs = fixture(file).runs;
    await expect(agent.locator('tbody tr')).toHaveCount(runs.length);
    for (let i = 0; i < runs.length; i++) {
      await agent.locator('tbody button').nth(i).click();
      await expect(agent.locator('tbody tr').nth(i)).toHaveAttribute('aria-selected', 'true');
      await expect(agent.locator('details').first().locator('summary').first()).toContainText(`${runs[i].provider} / ${runs[i].mode}`);
    }
  }
  expect(errors).toEqual([]);
  expect(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth + 1)).toBe(false);
});

test('error-only filtering never leaves an excluded case in the inspector', async ({ page }) => {
  await openLab(page);
  const report = fixture('latest');
  const correctIndex = report.cases.findIndex((c:any) => report.predictions.filter((p:any)=>p.case_id===c.case_id&&p.method!=='rules').every((p:any)=>p.status==='ok'&&p.predicted===c.expected));
  await page.locator('.case-row').nth(correctIndex).click();
  await page.getByRole('button', {name:'오류 사례만', exact:true}).click();
  await expect(page.locator('.case-row.active')).toHaveCount(1);
});

for (const target of ['model', 'retrieval', 'agent']) test(`${target} failed selection reports failure and can recover`, async ({ page }) => {
  const path = target === 'model' ? '/api/evaluations?experiment=claude' : target === 'retrieval' ? '/api/evaluations/retrieval?experiment=reranking' : '/api/evaluations/agent?version=v1';
  await page.route(`**${path}`, route => route.fulfill({status:503,json:{detail:'audit-unavailable'}}));
  await openLab(page);
  const label = target === 'model' ? '측정 모델' : target === 'retrieval' ? '검색 비교 실험' : 'Agent 실행 버전';
  const control = page.getByRole('combobox', {name:label,exact:true});
  await control.selectOption(target === 'model' ? 'claude' : target === 'retrieval' ? 'reranking' : 'v1');
  await expect(page.getByText(/불러오지 못했습니다/)).toBeVisible();
  await expect(control).toBeVisible();
  await control.selectOption(target === 'model' ? 'qwen' : target === 'retrieval' ? 'baseline' : 'latest');
  await expect(page.getByText(/불러오지 못했습니다/)).toHaveCount(0);
  await expect(target === 'model' ? page.getByTestId('experiment-comparison') : page.getByRole('region',{name:target === 'retrieval' ? '도면 검색 방식 비교' : '실제 Agent 실행 비교',exact:true}).getByRole('table')).toBeVisible();
});

for (const target of ['retrieval','vision']) test(`${target} empty cases preserve the laboratory with an explicit empty state`, async ({page}) => {
  const report = fixture(target);
  if (target === 'retrieval') report.cases=[]; else report.protocol.cases=[];
  await page.route(`**/api/evaluations/${target}*`, route => route.fulfill({json:report}));
  const errors:string[]=[];page.on('pageerror',error=>errors.push(error.message));
  await openLab(page);
  await expect(page.getByRole('status').filter({hasText:'사례가 없습니다'})).toBeVisible();
  expect(errors).toEqual([]);
});

for (const target of ['retrieval','vision']) test(`${target} broken source image reports the problem and the next case recovers`, async ({page}) => {
  const report = fixture(target);
  const cases = target === 'retrieval' ? report.cases.filter((c:any)=>c.category==='image') : report.protocol.cases;
  cases[0].image_data_url='data:image/png;base64,broken';
  await page.route(`**/api/evaluations/${target}*`,route=>route.fulfill({json:report}));
  await openLab(page);
  const region = page.getByRole('region',{name:target==='retrieval'?'도면 검색 방식 비교':'도면 이미지 읽기',exact:true});
  if (target==='retrieval') await region.getByRole('combobox',{name:'검색 질문 유형'}).selectOption('image');
  await expect(region.getByRole('status').filter({hasText:'이미지를 불러오지 못했습니다'})).toBeVisible();
  await region.getByRole('combobox',{name:target==='retrieval'?'검색 평가 질문':'이미지 읽기 사례'}).selectOption(cases[1].id);
  await expect(region.getByText(/이미지를 불러오지 못했습니다/)).toHaveCount(0);
  await expect.poll(()=>region.getByRole('img').evaluate((img:HTMLImageElement)=>img.naturalWidth)).toBeGreaterThan(0);
});

test('missing vision repetition is distinguished from a failed observation', async ({page})=>{
  const report=fixture('vision');
  report.rows=report.rows.filter((r:any)=>!(r.case_id===report.protocol.cases[0].id&&r.repeat===1));
  await page.route('**/api/evaluations/vision',route=>route.fulfill({json:report}));
  await openLab(page);
  const region=page.getByRole('region',{name:'도면 이미지 읽기',exact:true});
  await expect(region.getByRole('status')).toContainText('반복의 측정 기록이 없습니다');
  await expect(region.getByRole('table')).toHaveCount(0);
  await region.getByRole('combobox',{name:'이미지 읽기 반복'}).selectOption('2');
  await expect(region.getByRole('table')).toBeVisible();
});

test('failed model catalog is explained and can be retried without losing results',async({page})=>{
  let unavailable=true;
  await page.route('**/api/evaluations/catalog',route=>unavailable?route.fulfill({status:503,json:{detail:'catalog unavailable'}}):route.fallback());
  await openLab(page);
  await expect(page.getByTestId('experiment-comparison')).toContainText('Qwen');
  await expect(page.getByRole('alert')).toContainText('모델 목록');
  unavailable=false;
  await page.getByRole('button',{name:'모델 목록 다시 불러오기'}).click();
  await expect(page.getByRole('combobox',{name:'측정 모델',exact:true}).locator('option')).toHaveCount(2);
  await expect(page.getByRole('alert')).toHaveCount(0);
  await page.getByRole('combobox',{name:'측정 모델',exact:true}).selectOption('claude');
  await expect(page.getByTestId('experiment-comparison')).toContainText('claude-haiku-4-5');
});

for (const target of ['retrieval', 'agent']) test(`${target} selection survives the primary model report finishing`, async ({page}) => {
  let release!: () => void;
  const pending = new Promise<void>(resolve => { release = resolve; });
  await page.route('**/api/evaluations?experiment=qwen', async route => {
    await pending;
    await route.fulfill({json:fixture('latest')});
  });
  await openLab(page);
  const control = page.getByRole('combobox',{name:target==='retrieval'?'검색 비교 실험':'Agent 실행 버전',exact:true});
  const choice = target==='retrieval'?'reranking':'v1';
  await control.selectOption(choice);
  await expect(control).toHaveValue(choice);
  release();
  await expect(page.getByTestId('experiment-comparison')).toBeVisible();
  await expect(control).toHaveValue(choice);
  const region=page.getByRole('region',{name:target==='retrieval'?'도면 검색 방식 비교':'실제 Agent 실행 비교',exact:true});
  await expect(region).toContainText(target==='retrieval'?'복합 + BGE 재정렬':'failed');
});

test('an unmeasured primary report leaves other experiments usable', async ({page}) => {
  await page.route('**/api/evaluations?experiment=qwen',route=>route.fulfill({json:{status:'not_measured'}}));
  await openLab(page);
  await expect(page.getByRole('status').filter({hasText:'아직 측정된 결과가 없습니다'})).toBeVisible();
  await page.getByRole('combobox',{name:'검색 비교 실험',exact:true}).selectOption('reranking');
  await expect(page.getByRole('region',{name:'도면 검색 방식 비교',exact:true})).toContainText('복합 + BGE 재정렬');
});
