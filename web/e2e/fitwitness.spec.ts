import {test,expect} from '@playwright/test';
test('conditions, evidence and revision revalidation use real API',async({page})=>{
 await page.goto('/');await expect(page.getByRole('heading',{name:'도면의 생김새보다, 맞는 근거.'})).toBeVisible();
 await page.getByRole('button',{name:'조건 검증 시작',exact:true}).click();
 await expect(page.getByText('검증 완료',{exact:true})).toBeVisible({timeout:30000});
 await expect(page.getByTestId('candidate-card').first()).toBeVisible();
 await page.getByTestId('candidate-card').first().click();
 await expect(page.getByTestId('evidence-panel')).toContainText('도면 근거');
 await page.getByRole('button',{name:'개정판 적용',exact:true}).click();
 await expect(page.getByText('재검증 필요',{exact:true})).toBeVisible();
 await page.getByRole('button',{name:'바뀐 도면으로 재검증',exact:true}).click();
 await expect(page.getByText('검증 완료',{exact:true})).toBeVisible({timeout:30000});
 const overflow=await page.evaluate(()=>document.documentElement.scrollWidth>window.innerWidth+1);expect(overflow).toBe(false);
});
test('worker crash recovery is visible and completes once',async({page})=>{
 await page.goto('/');await page.getByRole('checkbox',{name:'중간 중단 후 복구 체험'}).check();
 await page.getByRole('button',{name:'조건 검증 시작',exact:true}).click();
 await expect(page.getByText('검증 완료',{exact:true})).toBeVisible({timeout:30000});
 await page.getByRole('button',{name:'실행 기록 보기'}).click();
 await expect(page.getByTestId('run-timeline')).toContainText('저장된 지점에서 재개');
});
