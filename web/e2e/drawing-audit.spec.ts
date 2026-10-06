import {test,expect} from '@playwright/test';

test('every active drawing can be selected by number and loaded as its own source',async({page})=>{
 await page.goto('/');
 const picker=page.getByRole('combobox',{name:'도면 바로 선택'});
 await expect(picker).toBeVisible();
 await expect(page.locator('.sheet-toolbar')).toContainText('FW-000-0');
 const image=page.getByRole('img',{name:'원본 도면과 근거 영역'});
 const sources=new Set<string>();
 for(let i=0;i<5;i++){
  await picker.selectOption({label:`FW-000-${i} · A`});
  await expect(page.locator('.sheet-toolbar')).toContainText(`FW-000-${i}`);
  await expect.poll(()=>image.evaluate((el:HTMLImageElement)=>el.complete&&el.naturalWidth>0)).toBe(true);
  const src=await image.getAttribute('src'); sources.add(src!);
  const pdf=await page.getByRole('link',{name:'원본 PDF 열기'}).getAttribute('href');
  expect(pdf!.replace('/pdf','/png')).toBe(src);
 }
 expect(sources.size).toBe(5);
 await page.getByRole('button',{name:'이전 도면',exact:true}).click();
 await expect(page.locator('.sheet-toolbar')).toContainText('FW-000-3');
 await page.getByRole('button',{name:'다음 도면',exact:true}).click();
 await expect(page.locator('.sheet-toolbar')).toContainText('FW-000-4');
});

test('exact query is submitted unchanged and all sources remain accessible',async({page})=>{
 await page.goto('/');await expect(page.getByRole('button',{name:'조건 검증 시작',exact:true})).toBeEnabled();
 await page.getByRole('textbox').fill('FW-000-0');
 const req=page.waitForRequest(r=>r.url().endsWith('/api/runs')&&r.method()==='POST');
 await page.getByRole('button',{name:'조건 검증 시작',exact:true}).click();
 expect((await req).postDataJSON().search.text).toBe('FW-000-0');
 await expect(page.getByText('검증 완료',{exact:true})).toBeVisible({timeout:60000});
 // An exact drawing number retrieves that drawing alone; the rest of the workspace folds away.
 await expect(page.getByTestId('candidate-card')).toHaveCount(1);
 await expect(page.getByRole('button',{name:/검색 제외 19개 보기/})).toBeVisible();
 await page.getByRole('combobox',{name:'도면 바로 선택'}).selectOption({label:'FW-000-3 · A'});
 await expect(page.locator('.sheet-toolbar')).toContainText('FW-000-3');
});

test('revision list never sends users back to the superseded drawing',async({page})=>{
 await page.goto('/');await page.getByRole('button',{name:'조건 검증 시작',exact:true}).click();
 await expect(page.getByText('검증 완료',{exact:true})).toBeVisible({timeout:60000});
 await page.getByRole('button',{name:'개정판 적용',exact:true}).click();
 await expect(page.getByText('재검증 필요',{exact:true})).toBeVisible();
 const card=page.getByTestId('candidate-card').filter({hasText:'FW-000-0'});
 await expect(card.locator('.revision')).toHaveText('B');
 await card.click();await expect(page.locator('.sheet-toolbar .revision')).toContainText('B');
 await expect(page.getByTestId('candidate-card')).toHaveCount(5);
 await page.getByRole('button',{name:'바뀐 도면으로 재검증',exact:true}).click();
 await expect(page.getByText('검증 완료',{exact:true})).toBeVisible({timeout:60000});
 await page.getByRole('button',{name:'불일치만 보기',exact:true}).click();
 await expect(page.getByTestId('candidate-card')).toHaveCount(3);
});

test('failed source image has an actionable retry',async({page})=>{
 await page.route('**/api/documents/*/assets/png*',route=>route.abort());
 await page.goto('/');
 await expect(page.getByRole('button',{name:'도면 다시 불러오기'})).toBeVisible();
 await page.unroute('**/api/documents/*/assets/png*');
 await page.getByRole('button',{name:'도면 다시 불러오기'}).click();
 await expect.poll(()=>page.getByRole('img',{name:'원본 도면과 근거 영역'}).evaluate((el:HTMLImageElement)=>el.complete&&el.naturalWidth>0)).toBe(true);
 await expect(page.getByText('도면 이미지를 불러오지 못했습니다.')).toHaveCount(0);
});

test('expired workspace can be reopened from the visible error',async({page})=>{
 await page.goto('/');await expect(page.getByRole('button',{name:'조건 검증 시작',exact:true})).toBeEnabled();
 await page.route('**/api/runs',r=>r.fulfill({status:401,json:{detail:'체험 세션이 만료됐습니다'}}));
 await page.getByRole('button',{name:'조건 검증 시작',exact:true}).click();
 await expect(page.getByRole('alert')).toContainText('만료');
 await page.getByRole('button',{name:'새 체험 공간 열기'}).click();
 await expect(page.getByRole('button',{name:'조건 검증 시작',exact:true})).toBeEnabled();
 await expect(page.getByRole('alert')).toHaveCount(0);
 await expect(page.getByRole('combobox',{name:'도면 바로 선택'})).toContainText('FW-000-0');
});
