import {defineConfig} from '@playwright/test';
export default defineConfig({testDir:'./tests',workers:1,timeout:45000,use:{baseURL:process.env.E2E_URL ?? 'http://localhost:3000',trace:'retain-on-failure'},reporter:'list'});
