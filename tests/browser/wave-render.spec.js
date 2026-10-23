import { test, expect } from '@playwright/test';

test('waveform reuses native bars between frames and adjusts them on resize', async ({ page }) => {
  await page.goto('/');
  await expect(page.locator('#settingsInfo')).toContainText('Loaded saved config');
  const result = await page.evaluate(async () => {
    const { state } = await import('/js/state.js');
    const { drawWave } = await import('/js/vad.js');
    const wave = document.getElementById('wave');
    const originalFrame = window.requestAnimationFrame;
    const originalAnalyser = state.analyser;
    const originalRaf = state.raf;
    let level = 128;
    window.requestAnimationFrame = () => 0;
    state.analyser = { frequencyBinCount: 256, getByteTimeDomainData: data => data.fill(level) };
    try {
      drawWave();
      const bars = [...wave.children];
      const initialHeights = bars.map(bar => bar.style.height);
      level = 160;
      for (let frame = 0; frame < 30; frame++) drawWave();
      const stable = bars.every((bar, index) => wave.children[index] === bar);
      const changedHeights = [...wave.children].map(bar => bar.style.height);
      wave.style.width = '300px';
      drawWave();
      const small = [...wave.children];
      const retainedOnShrink = small.every((bar, index) => bars[index] === bar);
      wave.style.width = '600px';
      drawWave();
      const wide = [...wave.children];
      return { stable, retainedOnShrink, retainedOnGrow: small.every((bar, index) => wide[index] === bar),
        initialCount: bars.length, smallCount: small.length, wideCount: wide.length,
        initialHeights, changedHeights, finalHeights: wide.map(bar => bar.style.height),
        allBars: wide.every(bar => bar.className === 'bar') };
    } finally {
      window.requestAnimationFrame = originalFrame;
      state.analyser = originalAnalyser;
      state.raf = originalRaf;
      wave.style.removeProperty('width');
    }
  });
  expect(result.initialCount).toBeGreaterThan(result.smallCount);
  expect(result.wideCount).toBeGreaterThan(result.smallCount);
  expect(result.stable && result.retainedOnShrink && result.retainedOnGrow && result.allBars).toBe(true);
  expect(new Set(result.initialHeights)).toEqual(new Set(['4px']));
  expect(new Set(result.changedHeights)).toEqual(new Set(['15px']));
  expect(new Set(result.finalHeights)).toEqual(new Set(['15px']));
});
