import {defineConfig,devices} from '@playwright/test';
export default defineConfig({testDir:'e2e',timeout:60000,use:{baseURL:process.env.PLAYWRIGHT_BASE_URL||'http://127.0.0.1:5173',trace:'retain-on-failure'},projects:[{name:'chromium',use:{...devices['Desktop Chrome']}},{name:'mobile',use:{...devices['iPhone 13'],defaultBrowserType:'chromium'}}]});
