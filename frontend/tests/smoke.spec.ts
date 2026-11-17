import {test,expect} from '@playwright/test';
test('three text rounds, writeback and reload',async({page})=>{
  await page.goto('/');
  await page.getByLabel('演示密码').fill(process.env.DEMO_PASSWORD ?? 'demo-local-only');
  await page.getByRole('button',{name:'登录',exact:true}).click();
  await page.getByRole('button',{name:'创建会话'}).click();
  await page.getByRole('button',{name:'开始',exact:true}).click();
  await expect(page.getByRole('button',{name:'打断',exact:true})).toBeDisabled();
  await expect(page.getByText('关单渠道：电话说明')).toBeVisible();
  await expect(page.getByText('原因：模拟授权书缺项')).toHaveCount(0);
  for(const text of ['我是演示商家1','补齐材料一定必过吗','明白了']){
    await page.getByLabel('商户回复').fill(text);
    await page.getByRole('button',{name:'发送',exact:true}).click();
    await expect(page.getByRole('button',{name:'打断',exact:true})).toBeDisabled();
    if (text === '我是演示商家1') {
      await expect(page.getByText('身份：已核对')).toBeVisible();
      // Verification may be visible while the first explanation streams.
      await expect(page.getByText('原因：模拟授权书缺项')).toBeVisible();
    }
    if(text.includes('必过'))await expect(page.getByText('补齐材料后仍需审核，不能保证通过。')).toBeVisible();
  }
  await expect(page.getByTestId('writeback')).toBeVisible();
  await expect(page.getByTestId('operation-status')).toContainText('模拟处理成功');
  await page.reload();
  await expect(page.getByTestId('writeback')).toBeVisible();
  await expect(page.getByRole('region',{name:'审核依据'})).toBeVisible();
  await expect(page.getByText('语音未配置')).toBeVisible();
  await page.screenshot({path:test.info().outputPath('completed-flow.png'),fullPage:true});
});
